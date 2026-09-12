const DETAIL_PANE_MIN_WIDTH = 280;
// The detail pane may grow until it takes up 80% of the space to the right of
// the left resource menu (graph pane + detail pane combined), letting it grow
// wide enough to meet the resource menu.
const DETAIL_PANE_MAX_RATIO = 0.8;

function resourcePanel() {
  return {
    yamlText: '',
    treeHtml: '',
    describeSections: [],
    logsText: '',
    viewMode: 'yaml',
    loading: false,
    error: null,
    requestSeq: 0,
    resizing: false,

    init() {
      this.$watch('$store.app.detailResource', (selected) => this.load(selected));
      this.load(this.$store.app.detailResource);
    },

    startResize(evt) {
      evt.preventDefault();
      const startX = evt.clientX;
      const startWidth = this.$store.app.detailPaneWidth;
      const gridEl = document.querySelector('.krw-right-pane');
      const gridWidth = gridEl ? gridEl.getBoundingClientRect().width : window.innerWidth - startWidth;
      const containerWidth = gridWidth + startWidth;
      const maxWidth = Math.max(DETAIL_PANE_MIN_WIDTH, containerWidth * DETAIL_PANE_MAX_RATIO);
      this.resizing = true;
      document.body.style.userSelect = 'none';

      const onMouseMove = (moveEvt) => {
        // The handle sits on the pane's left edge, so dragging left (negative
        // clientX delta) should widen the pane.
        const delta = startX - moveEvt.clientX;
        const newWidth = Math.min(Math.max(startWidth + delta, DETAIL_PANE_MIN_WIDTH), maxWidth);
        this.$store.app.detailPaneWidth = newWidth;
      };

      const onMouseUp = () => {
        this.resizing = false;
        document.body.style.userSelect = '';
        document.removeEventListener('mousemove', onMouseMove);
        document.removeEventListener('mouseup', onMouseUp);
        try {
          localStorage.setItem('krowser.detailPaneWidth', String(this.$store.app.detailPaneWidth));
        } catch (_) {
          // localStorage unavailable (private browsing, etc.) -- width just won't persist.
        }
      };

      document.addEventListener('mousemove', onMouseMove);
      document.addEventListener('mouseup', onMouseUp);
    },

    async load(selected) {
      // Guard against out-of-order responses if the user clicks between nodes
      // faster than a fetch resolves (same class of bug fixed in app.js's poll refresh).
      const requestId = ++this.requestSeq;

      if (!selected) {
        this.yamlText = '';
        this.treeHtml = '';
        this.describeSections = [];
        this.logsText = '';
        this.error = null;
        return;
      }

      this.viewMode = selected.view || 'yaml';
      this.loading = true;
      this.error = null;
      try {
        if (this.viewMode === 'describe') {
          const res = await api.getPodDescribe({
            namespace: selected.namespace,
            name: selected.name,
            context: this.$store.app.context,
          });
          if (requestId !== this.requestSeq) return;
          this.describeSections = res.sections;
        } else if (this.viewMode === 'logs') {
          const res = await api.getPodLogs({
            namespace: selected.namespace,
            name: selected.name,
            container: selected.container,
            context: this.$store.app.context,
          });
          if (requestId !== this.requestSeq) return;
          this.logsText = res.logs;
        } else {
          const res = await api.getResourceYaml({
            kind: selected.kind,
            namespace: selected.namespace,
            name: selected.name,
            context: this.$store.app.context,
            group: selected.api_group,
            version: selected.api_version,
            plural: selected.plural,
          });
          if (requestId !== this.requestSeq) return;
          this.yamlText = res.yaml;
          // Rendered once per load (not a live getter) so a new selection
          // always starts fully expanded, independent of any .collapsed
          // classes left on the previous resource's tree DOM.
          this.treeHtml = renderYamlTree(res.data);
        }
      } catch (e) {
        if (requestId !== this.requestSeq) return;
        this.error = e.message;
        this.yamlText = '';
        this.treeHtml = '';
        this.describeSections = [];
        this.logsText = '';
      } finally {
        if (requestId === this.requestSeq) this.loading = false;
      }
    },

    handleTreeClick(event) {
      const row = event.target.closest('.krw-tree-row[data-toggle]');
      if (!row) return;
      row.closest('.krw-tree-node').classList.toggle('collapsed');
    },

    close() {
      this.$store.app.detailResource = null;
    },
  };
}

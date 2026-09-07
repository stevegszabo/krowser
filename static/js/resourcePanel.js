const DETAIL_PANE_MIN_WIDTH = 280;
const DETAIL_PANE_MAX_WIDTH = 900;

function resourcePanel() {
  return {
    yamlText: '',
    loading: false,
    error: null,
    requestSeq: 0,
    resizing: false,

    init() {
      this.$watch('$store.app.selectedResource', (selected) => this.load(selected));
      this.load(this.$store.app.selectedResource);
    },

    startResize(evt) {
      evt.preventDefault();
      const startX = evt.clientX;
      const startWidth = this.$store.app.detailPaneWidth;
      this.resizing = true;
      document.body.style.userSelect = 'none';

      const onMouseMove = (moveEvt) => {
        // The handle sits on the pane's left edge, so dragging left (negative
        // clientX delta) should widen the pane.
        const delta = startX - moveEvt.clientX;
        const maxWidth = Math.min(DETAIL_PANE_MAX_WIDTH, window.innerWidth - 300);
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
        this.error = null;
        return;
      }

      this.loading = true;
      this.error = null;
      try {
        const res = await api.getResourceYaml({
          kind: selected.kind,
          namespace: selected.namespace,
          name: selected.name,
          context: this.$store.app.context,
        });
        if (requestId !== this.requestSeq) return;
        this.yamlText = res.yaml;
      } catch (e) {
        if (requestId !== this.requestSeq) return;
        this.error = e.message;
        this.yamlText = '';
      } finally {
        if (requestId === this.requestSeq) this.loading = false;
      }
    },

    close() {
      this.$store.app.selectedResource = null;
    },
  };
}

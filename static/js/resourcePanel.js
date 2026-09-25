const DETAIL_PANE_MIN_WIDTH = 280;
// The detail pane may grow until it takes up 80% of the space to the right of
// the left resource menu (graph pane + detail pane combined), letting it grow
// wide enough to meet the resource menu.
const DETAIL_PANE_MAX_RATIO = 0.8;

// These two kinds' "Get <kind>" YAML renders as a rules table instead of the
// generic tree -- a Role/ClusterRole's `rules` array is its whole point, and
// reads far easier as a table than nested under nine layers of YAML tree.
const RULES_TABLE_KINDS = ['Role', 'ClusterRole'];

// These two kinds offer a Tree/Table toggle (unlike RULES_TABLE_KINDS above,
// which forces the table with no way back) -- a ConfigMap/Secret's `data`
// map is usually what a viewer actually wants, but the raw tree is still
// useful (e.g. to see metadata/labels), so both stay available.
const DATA_TABLE_KINDS = ['ConfigMap', 'Secret'];

// Builds one log line's HTML: every occurrence of `filter` (plain substring,
// case-insensitive) wrapped in <mark>, everything else escaped as text.
// Operates on raw text and escapes each piece as it's assembled, rather than
// escaping the whole line up front and pattern-matching on the escaped
// result, so a filter containing HTML-special characters (e.g. "<foo>")
// still matches correctly.
function highlightLogLine(line, filter) {
  if (!filter) return escapeHtml(line);
  const lowerLine = line.toLowerCase();
  const lowerFilter = filter.toLowerCase();
  let html = '';
  let i = 0;
  while (i < line.length) {
    const matchIndex = lowerLine.indexOf(lowerFilter, i);
    if (matchIndex === -1) {
      html += escapeHtml(line.slice(i));
      break;
    }
    html += escapeHtml(line.slice(i, matchIndex));
    html += `<mark class="krw-log-highlight">${escapeHtml(line.slice(matchIndex, matchIndex + filter.length))}</mark>`;
    i = matchIndex + filter.length;
  }
  return html;
}

function resourcePanel() {
  return {
    yamlText: '',
    yamlCopyStatus: '',
    treeHtml: '',
    rulesTable: null,
    dataMap: null,
    yamlViewMode: 'tree',
    describeSections: [],
    eventsRows: [],
    logsText: '',
    logFilter: '',
    execCommand: '',
    execRunning: false,
    execStarted: false,
    execExitInfo: '',
    execFailed: false,
    scanResults: null,
    expandedFindings: [],
    kubescanResults: null,
    // Off by default -- the actual command (esp. Kubescape's, with its temp
    // --output path) is diagnostic detail most viewers don't need to see on
    // every scan. Deliberately not reset per-load, so a user who turns it on
    // to debug something stays opted in across resource switches this session.
    showScanCommand: false,
    viewMode: 'yaml',
    loading: false,
    error: null,
    requestSeq: 0,
    resizing: false,
    _lastResourceId: null,
    _lastExecKey: null,
    _lastScanKey: null,
    _lastKubescanKey: null,
    _execSocket: null,
    _execTerm: null,
    _execFitAddon: null,
    _execResizeObserver: null,
    _yamlCopyStatusTimer: null,

    // Only the lines matching the filter (plain, case-insensitive substring
    // match), each with its matches wrapped in <mark>. Empty filter shows
    // every line unchanged, exactly reproducing the raw log text.
    get filteredLogsHtml() {
      const filter = this.logFilter.trim();
      const lines = this.logsText.split('\n');
      const kept = filter ? lines.filter((line) => line.toLowerCase().includes(filter.toLowerCase())) : lines;
      return kept.map((line) => highlightLogLine(line, filter)).join('\n');
    },

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

    // Mirrors into $store.app.detailLoading (alongside the local `loading`
    // used by this pane's own "Loading..." message) so the global busy
    // cursor (see app.js's $store.app.busy) also covers detail-pane fetches.
    setLoading(value) {
      this.loading = value;
      this.$store.app.detailLoading = value;
    },

    async load(selected) {
      // Guard against out-of-order responses if the user clicks between nodes
      // faster than a fetch resolves (same class of bug fixed in app.js's poll refresh).
      const requestId = ++this.requestSeq;

      if (!selected) {
        this.yamlText = '';
        this.yamlCopyStatus = '';
        this.treeHtml = '';
        this.rulesTable = null;
        this.dataMap = null;
        this.yamlViewMode = 'tree';
        this.describeSections = [];
        this.eventsRows = [];
        this.logsText = '';
        this.logFilter = '';
        this.error = null;
        this.scanResults = null;
        this.expandedFindings = [];
        this.kubescanResults = null;
        this.setLoading(false);
        this.resetExec();
        return;
      }

      // Preserve a typed filter across a container switch (same pod, see
      // changeLogContainer) but drop it when the selection is genuinely a
      // different resource, so it doesn't silently carry over.
      if (selected.id !== this._lastResourceId) {
        this.logFilter = '';
        this.yamlCopyStatus = '';
      }
      this._lastResourceId = selected.id;

      this.viewMode = selected.view || 'yaml';

      if (this.viewMode === 'exec') {
        this.setLoading(false);
        this.error = null;
        // A running/completed exec session is only torn down when the
        // pod or container actually changes -- switching back and forth
        // between other view modes on the same container must not kill it.
        const execKey = `${selected.id}::${selected.container}`;
        if (execKey !== this._lastExecKey) {
          this._lastExecKey = execKey;
          this.resetExec();
        }
        return;
      }

      if (this.viewMode === 'vulnscan') {
        this.setLoading(false);
        this.error = null;
        // Scanning is user-triggered (see runScan) rather than auto-fetched
        // like describe/logs -- only reset stale results when the pod or
        // container actually changes, same key pattern as exec's session.
        const scanKey = `${selected.id}::${selected.container}`;
        if (scanKey !== this._lastScanKey) {
          this._lastScanKey = scanKey;
          this.scanResults = null;
          this.expandedFindings = [];
        }
        return;
      }

      if (this.viewMode === 'kubescan') {
        this.setLoading(false);
        this.error = null;
        // Same deliberate user-trigger pattern as vulnscan, above -- no
        // container dimension here since Kubescape scans the whole resource.
        if (selected.id !== this._lastKubescanKey) {
          this._lastKubescanKey = selected.id;
          this.kubescanResults = null;
          this.expandedFindings = [];
        }
        return;
      }

      this.setLoading(true);
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
        } else if (this.viewMode === 'events') {
          const res = await api.getResourceEvents({
            kind: selected.kind,
            namespace: selected.namespace,
            name: selected.name,
            context: this.$store.app.context,
          });
          if (requestId !== this.requestSeq) return;
          this.eventsRows = res.events;
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
            crd: selected.crd,
          });
          if (requestId !== this.requestSeq) return;
          this.yamlText = res.yaml;
          // Rendered once per load (not a live getter) so a new selection
          // always starts fully expanded, independent of any .collapsed
          // classes left on the previous resource's tree DOM.
          this.treeHtml = renderYamlTree(res.data);
          this.rulesTable = RULES_TABLE_KINDS.includes(selected.kind) ? res.data.rules || [] : null;
          this.dataMap = DATA_TABLE_KINDS.includes(selected.kind)
            ? { data: res.data.data || {}, binaryData: res.data.binaryData || {} }
            : null;
          this.yamlViewMode = 'tree';
        }
      } catch (e) {
        if (requestId !== this.requestSeq) return;
        this.error = e.message;
        this.yamlText = '';
        this.treeHtml = '';
        this.rulesTable = null;
        this.dataMap = null;
        this.yamlViewMode = 'tree';
        this.describeSections = [];
        this.eventsRows = [];
        this.logsText = '';
      } finally {
        if (requestId === this.requestSeq) this.setLoading(false);
      }
    },

    // Replaces detailResource wholesale (rather than mutating .container in
    // place) so the existing $watch('$store.app.detailResource', ...) in
    // init() -- which only tracks the object reference, not its nested
    // fields -- reliably fires and reloads logs for the new container.
    changeLogContainer(container) {
      this.$store.app.detailResource = { ...this.$store.app.detailResource, container };
    },

    // Same wholesale-replace pattern as changeLogContainer, above. Switching
    // containers here goes through load()'s execKey check, which tears down
    // any running session for the old container via resetExec().
    changeExecContainer(container) {
      this.$store.app.detailResource = { ...this.$store.app.detailResource, container };
    },

    // Same wholesale-replace pattern again. Switching containers here goes
    // through load()'s scanKey check, which clears the previous container's
    // stale results.
    changeScanContainer(container) {
      this.$store.app.detailResource = { ...this.$store.app.detailResource, container };
    },

    // Same array-of-open-keys shape as resourceList.js's
    // expandedGroups/isGroupCollapsed/toggleGroup -- findings lists are small
    // enough that a plain array is simpler than a Set here.
    isFindingExpanded(key) {
      return this.expandedFindings.includes(key);
    },

    toggleFinding(key) {
      this.expandedFindings = this.expandedFindings.includes(key)
        ? this.expandedFindings.filter((k) => k !== key)
        : [...this.expandedFindings, key];
    },

    // User-triggered (not auto-fetched on load, unlike describe/logs) since a
    // scan can take real time and bandwidth -- pulls the image, runs the
    // scanner, and can take anywhere from a few seconds to a couple of
    // minutes depending on image size and whether the scanner's own
    // vulnerability DB needs refreshing.
    async runScan() {
      const selected = this.$store.app.detailResource;
      if (!selected) return;
      const requestId = ++this.requestSeq;
      this.setLoading(true);
      this.error = null;
      this.expandedFindings = [];
      try {
        const res = await api.getPodVulnScan({
          namespace: selected.namespace,
          name: selected.name,
          container: selected.container,
          context: this.$store.app.context,
        });
        if (requestId !== this.requestSeq) return;
        this.scanResults = res;
      } catch (e) {
        if (requestId !== this.requestSeq) return;
        this.error = e.message;
        this.scanResults = null;
      } finally {
        if (requestId === this.requestSeq) this.setLoading(false);
      }
    },

    // Same user-triggered shape as runScan(), above, but scans the whole
    // resource's configuration via Kubescape rather than one container's
    // image via Trivy -- no `container` param needed.
    async runKubescan() {
      const selected = this.$store.app.detailResource;
      if (!selected) return;
      const requestId = ++this.requestSeq;
      this.setLoading(true);
      this.error = null;
      this.expandedFindings = [];
      try {
        const res = await api.getWorkloadKubescan({
          kind: selected.kind,
          namespace: selected.namespace,
          name: selected.name,
          context: this.$store.app.context,
        });
        if (requestId !== this.requestSeq) return;
        this.kubescanResults = res;
      } catch (e) {
        if (requestId !== this.requestSeq) return;
        this.error = e.message;
        this.kubescanResults = null;
      } finally {
        if (requestId === this.requestSeq) this.setLoading(false);
      }
    },

    // Creates the xterm.js Terminal once per pod/container selection (see
    // resetExec) and mounts it into the x-ref div rendered by the exec
    // template. A ResizeObserver keeps it fitted to the pane as the user
    // resizes the detail pane, forwarding the new size to the backend so
    // the remote TTY stays in sync (matters for full-screen apps like vim/top).
    mountExecTerminal() {
      if (this._execTerm) return;
      const term = new Terminal({
        fontSize: 13,
        cursorBlink: true,
        theme: { background: '#12181f' },
      });
      const fitAddon = new FitAddon.FitAddon();
      term.loadAddon(fitAddon);
      term.open(this.$refs.execTerminal);
      fitAddon.fit();

      term.onData((data) => {
        if (this._execSocket && this._execSocket.readyState === WebSocket.OPEN) {
          this._execSocket.send(JSON.stringify({ type: 'stdin', data }));
        }
      });

      const resizeObserver = new ResizeObserver(() => {
        fitAddon.fit();
        if (this._execSocket && this._execSocket.readyState === WebSocket.OPEN) {
          this._execSocket.send(JSON.stringify({ type: 'resize', rows: term.rows, cols: term.cols }));
        }
      });
      resizeObserver.observe(this.$refs.execTerminal);

      this._execTerm = term;
      this._execFitAddon = fitAddon;
      this._execResizeObserver = resizeObserver;
    },

    // Opens a live WebSocket to the backend's kubectl-exec-style bridge and
    // streams the pod's TTY into the terminal. Supports both a one-shot
    // command (e.g. "date", which just prints and exits) and an interactive
    // one (e.g. "bash"), since both are just processes attached to a TTY.
    runExec() {
      const selected = this.$store.app.detailResource;
      if (this.execRunning || !selected || !this.execCommand.trim()) return;

      this.mountExecTerminal();
      if (this.execStarted) {
        this._execTerm.write(`\r\n\x1b[2m$ ${this.execCommand}\x1b[0m\r\n`);
      }
      this.execStarted = true;
      this.execExitInfo = '';
      this.execFailed = false;
      this.execRunning = true;

      const params = new URLSearchParams({
        name: selected.name,
        namespace: selected.namespace,
        container: selected.container,
        command: this.execCommand,
      });
      if (this.$store.app.context) params.set('context', this.$store.app.context);
      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
      const ws = new WebSocket(`${proto}://${window.location.host}/api/pod-exec?${params.toString()}`);
      this._execSocket = ws;

      ws.onopen = () => {
        this._execFitAddon.fit();
        ws.send(JSON.stringify({ type: 'resize', rows: this._execTerm.rows, cols: this._execTerm.cols }));
      };
      ws.onmessage = (evt) => {
        const msg = JSON.parse(evt.data);
        if (msg.type === 'stdout') {
          this._execTerm.write(msg.data);
        } else if (msg.type === 'error') {
          this._execTerm.write(`\r\n\x1b[31m${msg.data}\x1b[0m\r\n`);
          this.execFailed = true;
          this.execRunning = false;
        } else if (msg.type === 'exit') {
          if (this.execFailed) {
            this.execExitInfo = 'failed';
          } else {
            this.execExitInfo = msg.data === null ? 'exited' : `exited (${msg.data})`;
          }
          this.execRunning = false;
        }
      };
      ws.onclose = () => {
        this.execRunning = false;
      };
      ws.onerror = () => {
        this.execRunning = false;
      };
    },

    stopExec() {
      if (this._execSocket) {
        this._execSocket.close();
        this._execSocket = null;
      }
      this.execRunning = false;
    },

    // Tears down any live session and the terminal itself -- called when the
    // exec pane's pod/container changes or the pane closes, so a stale
    // WebSocket/Terminal never lingers past the selection it belongs to.
    resetExec() {
      this.stopExec();
      if (this._execResizeObserver) {
        this._execResizeObserver.disconnect();
        this._execResizeObserver = null;
      }
      if (this._execTerm) {
        this._execTerm.dispose();
        this._execTerm = null;
      }
      this._execFitAddon = null;
      this.execCommand = '';
      this.execExitInfo = '';
      this.execFailed = false;
      this.execStarted = false;
    },

    handleTreeClick(event) {
      const row = event.target.closest('.krw-tree-row[data-toggle]');
      if (!row) return;
      row.closest('.krw-tree-node').classList.toggle('collapsed');
    },

    // Flattens dataMap's { data, binaryData } into one array of {key, value}
    // rows for the Table view -- Secret's data values are decoded (falling
    // back to a placeholder for genuine binary content), ConfigMap's data
    // values are already plain text, and binaryData entries (ConfigMap
    // only) always show a byte count rather than attempting to render raw
    // bytes as text.
    get dataTableRows() {
      if (!this.dataMap) return [];
      const kind = this.$store.app.selectedResource ? this.$store.app.selectedResource.kind : null;
      const rows = [];
      for (const [key, value] of Object.entries(this.dataMap.data || {})) {
        if (kind === 'Secret') {
          const decoded = this.decodedSecretValue(value);
          rows.push({ key, value: decoded !== null ? decoded : '(binary data)' });
        } else {
          rows.push({ key, value });
        }
      }
      for (const [key, value] of Object.entries(this.dataMap.binaryData || {})) {
        let byteLength = value.length;
        try {
          byteLength = atob(value).length;
        } catch (e) {
          // fall back to the base64 string's own length above
        }
        rows.push({ key, value: `(binary, ${byteLength} bytes)` });
      }
      return rows;
    },

    // Secret .data values are always base64 (the API never returns the
    // write-only stringData field back), unlike ConfigMap's .data which is
    // already plain text -- decode for the Table view so it's actually
    // readable instead of an opaque base64 blob. Some values are genuine
    // binary (e.g. a TLS keystore), which atob() happily decodes into bytes
    // that aren't valid UTF-8 -- decodeURIComponent(escape(...)) throws in
    // that case, and the caller falls back to a "(binary data)" placeholder
    // rather than rendering mojibake.
    decodedSecretValue(value) {
      try {
        return decodeURIComponent(escape(atob(value)));
      } catch (e) {
        return null;
      }
    },

    // yamlText always holds the raw fetched YAML, even when rulesTable is
    // set and the tree view is replaced by a table for RULES_TABLE_KINDS --
    // so both buttons work regardless of which of the two the pane renders.
    async copyYaml() {
      if (!this.yamlText) return;
      try {
        // navigator.clipboard only exists in a secure context (HTTPS or
        // localhost) -- krowser is often reached over plain HTTP via a LAN
        // IP/hostname, where it's undefined and this throws. Fall back to
        // the older execCommand('copy'), which works in both cases.
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(this.yamlText);
        } else {
          this.legacyCopyToClipboard(this.yamlText);
        }
        this.yamlCopyStatus = 'Copied!';
      } catch (_) {
        this.yamlCopyStatus = 'Copy failed';
      }
      clearTimeout(this._yamlCopyStatusTimer);
      this._yamlCopyStatusTimer = setTimeout(() => {
        this.yamlCopyStatus = '';
      }, 1500);
    },

    legacyCopyToClipboard(text) {
      const textarea = document.createElement('textarea');
      textarea.value = text;
      // Off-screen but still focusable/selectable -- execCommand('copy')
      // only works on the current selection, so it must actually be in the
      // document and focused, not just detached in memory.
      textarea.style.position = 'fixed';
      textarea.style.top = '0';
      textarea.style.left = '-9999px';
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(textarea);
      if (!ok) throw new Error('execCommand copy failed');
    },

    // Hits a real backend endpoint (Content-Disposition: attachment) rather
    // than building a client-side blob: URL -- a blob download triggered
    // from a page served over plain HTTP on a non-localhost origin gets
    // flagged by Chrome as an "insecure download" and saved under a generic
    // "Unconfirmed ####.crdownload" name instead of the real filename.
    downloadYaml() {
      if (!this.yamlText) return;
      const selected = this.$store.app.detailResource;
      const params = new URLSearchParams({ kind: selected.kind, name: selected.name });
      if (selected.namespace) params.set('namespace', selected.namespace);
      if (this.$store.app.context) params.set('context', this.$store.app.context);
      if (selected.crd) params.set('crd', selected.crd);
      const link = document.createElement('a');
      link.href = `/api/resource-yaml-download?${params.toString()}`;
      document.body.appendChild(link);
      link.click();
      link.remove();
    },

    close() {
      this.$store.app.detailResource = null;
    },
  };
}

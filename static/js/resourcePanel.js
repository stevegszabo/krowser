const DETAIL_PANE_MIN_WIDTH = 280;
// The detail pane may grow until it takes up 80% of the space to the right of
// the left resource menu (graph pane + detail pane combined), letting it grow
// wide enough to meet the resource menu.
const DETAIL_PANE_MAX_RATIO = 0.8;

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
    treeHtml: '',
    describeSections: [],
    logsText: '',
    logFilter: '',
    execCommand: '',
    execRunning: false,
    execStarted: false,
    execExitInfo: '',
    execFailed: false,
    scanResults: null,
    viewMode: 'yaml',
    loading: false,
    error: null,
    requestSeq: 0,
    resizing: false,
    _lastResourceId: null,
    _lastExecKey: null,
    _lastScanKey: null,
    _execSocket: null,
    _execTerm: null,
    _execFitAddon: null,
    _execResizeObserver: null,

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
        this.treeHtml = '';
        this.describeSections = [];
        this.logsText = '';
        this.logFilter = '';
        this.error = null;
        this.scanResults = null;
        this.setLoading(false);
        this.resetExec();
        return;
      }

      // Preserve a typed filter across a container switch (same pod, see
      // changeLogContainer) but drop it when the selection is genuinely a
      // different resource, so it doesn't silently carry over.
      if (selected.id !== this._lastResourceId) {
        this.logFilter = '';
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

    close() {
      this.$store.app.detailResource = null;
    },
  };
}

const RELATION_LABEL = {
  owns: 'owns',
  'routes-to': 'routes to',
  claims: 'claims',
  binds: 'binds',
  uses: 'uses',
  exposes: 'exposes',
  targets: 'targets',
  'runs-on': 'runs on',
  'runs-as': 'runs as',
  grants: 'grants',
  scales: 'scales',
};

// Kubescape's "scan workload" refuses to scan a Pod that has an owner (which
// is virtually every Pod krowser shows), so "Scan with Kubescape" is offered
// only for these top-level controller kinds -- never Pod.
const WORKLOAD_CONTROLLER_KINDS = ['DaemonSet', 'Deployment', 'StatefulSet', 'CronJob', 'Job'];

function edgeColor() {
  return getComputedStyle(document.documentElement).getPropertyValue('--edge-color').trim();
}

function sameIdSet(elements, ids) {
  if (elements.length !== ids.size) return false;
  for (let i = 0; i < elements.length; i++) {
    if (!ids.has(elements[i].id())) return false;
  }
  return true;
}

function graphView() {
  return {
    cy: null,
    panMode: true,
    zoomPct: 100,
    legendOpen: false,
    lastSelectionKey: null,
    // The very first graph the app renders after loading defaults to a
    // fixed 100% zoom instead of the usual fit-to-screen; cleared after
    // that first render so later type/namespace/context switches keep
    // fitting the whole graph on screen as before.
    isInitialRender: true,
    contextMenu: { visible: false, x: 0, y: 0, resource: null },
    hoveredNode: null,

    init() {
      this.cy = cytoscape({
        container: this.$refs.canvas,
        style: [
          {
            selector: 'node',
            style: {
              width: 224,
              height: 80,
              shape: 'roundrectangle',
              'background-opacity': 0,
              'border-width': 0,
            },
          },
          {
            selector: 'edge',
            style: {
              width: 1.5,
              'line-color': edgeColor(),
              'target-arrow-color': edgeColor(),
              'target-arrow-shape': 'triangle',
              'arrow-scale': 0.9,
              'curve-style': 'bezier',
            },
          },
        ],
        layout: { name: 'preset' },
        boxSelectionEnabled: false,
        autounselectify: true,
      });

      this.$watch('$store.app.theme', () => {
        this.cy
          .style()
          .selector('edge')
          .style({ 'line-color': edgeColor(), 'target-arrow-color': edgeColor() })
          .update();
      });

      this.cy.nodeHtmlLabel([
        {
          query: 'node',
          halign: 'center',
          valign: 'center',
          halignBox: 'center',
          valignBox: 'center',
          tpl: (data) => renderCard(data),
        },
      ]);

      this.cy.on('zoom', () => {
        this.zoomPct = Math.round(this.cy.zoom() * 100);
      });

      this.cy.on('tap', 'node', (evt) => {
        const data = evt.target.data();
        const store = this.$store.app;
        const isSame = store.selectedResource && store.selectedResource.id === data.id;
        store.selectedResource = isSame
          ? null
          : { id: data.id, kind: data.kind, namespace: data.namespace, name: data.name };
        this.contextMenu.visible = false;
      });

      // Cytoscape's own 'cxttap' event depends on the browser reporting a
      // right-button mousedown, which macOS trackpad right-click (Ctrl+click
      // or a two-finger tap) frequently does not do -- the OS/browser still
      // fires a native 'contextmenu' event, just without button:2 on the
      // preceding mousedown, so cxttap silently never fires. Driving the
      // menu off the native 'contextmenu' event instead is reliable across
      // platforms/input methods; we use the hover-tracked node (already
      // needed for the manual cursor styling below) to know which resource
      // was under the pointer.
      this.$refs.canvas.addEventListener('contextmenu', (evt) => {
        evt.preventDefault();
        if (!this.hoveredNode || this.hoveredNode.removed()) {
          this.contextMenu.visible = false;
          return;
        }
        const data = this.hoveredNode.data();
        const containers = data.containers || [];
        // Clamp so the menu can't render past the right/bottom edge of the
        // window. Pod nodes get four extra rows (Get pod logs, Get pod
        // description, Execute command, Scan for vulnerabilities); workload
        // controller kinds get one extra row (Scan with Kubescape); both are
        // on top of the two universal rows ("Get <kind>" and "Get events").
        const itemCount =
          2 + (data.kind === 'Pod' ? 4 : 0) + (WORKLOAD_CONTROLLER_KINDS.includes(data.kind) ? 1 : 0);
        const x = Math.min(evt.clientX, window.innerWidth - 240);
        const y = Math.min(evt.clientY, window.innerHeight - (itemCount * 36 + 8));
        this.contextMenu = {
          visible: true,
          x,
          y,
          resource: {
            id: data.id,
            kind: data.kind,
            namespace: data.namespace,
            name: data.name,
            containers,
          },
        };
      });

      // Any pan/zoom or background tap invalidates the menu's position/relevance.
      this.cy.on('pan zoom', () => {
        this.contextMenu.visible = false;
      });

      // The node-html-label overlay has pointer-events:none (clicks pass through
      // to the canvas, which is what lets the 'tap' handler above work at all),
      // so the browser won't show a hover cursor on its own -- set it manually.
      // Also tracked for the contextmenu handler above (see comment there).
      this.cy.on('mouseover', 'node', (evt) => {
        this.$refs.canvas.style.cursor = 'pointer';
        this.hoveredNode = evt.target;
      });
      this.cy.on('mouseout', 'node', () => {
        this.$refs.canvas.style.cursor = '';
        this.hoveredNode = null;
      });

      this.cy.on('tap', (evt) => {
        if (evt.target === this.cy) {
          this.$store.app.selectedResource = null;
        }
        this.contextMenu.visible = false;
      });

      // Single source of truth for the selection highlight: applies regardless
      // of whether the change came from a node tap, background tap, or the
      // resource-type/namespace/context change handlers clearing it in app.js.
      this.$watch('$store.app.selectedResource', (selected, previous) => {
        if (previous && previous.id) {
          const prevNode = this.cy.getElementById(previous.id);
          if (prevNode.nonempty()) prevNode.data('is_selected', false);
        }
        if (selected && selected.id) {
          const node = this.cy.getElementById(selected.id);
          if (node.nonempty()) node.data('is_selected', true);
        }
      });

      // The detail pane's open/closed state changes the graph container's
      // width; cytoscape needs to be told explicitly once the browser has
      // applied that layout change.
      this.$watch('$store.app.detailResource', () => {
        requestAnimationFrame(() => this.cy.resize());
      });

      // Also keep the canvas in sync while the user drags the detail pane's
      // resize handle (fires continuously during drag; cy.resize() is cheap).
      this.$watch('$store.app.detailPaneWidth', () => {
        requestAnimationFrame(() => this.cy.resize());
      });

      // Hiding/showing or resizing the left nav also changes the canvas's
      // available width.
      this.$watch('$store.app.leftPaneVisible', () => {
        requestAnimationFrame(() => this.cy.resize());
      });
      this.$watch('$store.app.leftPaneWidth', () => {
        requestAnimationFrame(() => this.cy.resize());
      });

      this.$watch('$store.app.graph', (graph) => this.renderGraph(graph));
      this.renderGraph(this.$store.app.graph);
    },

    reapplySelectionHighlight() {
      const selected = this.$store.app.selectedResource;
      if (!selected || !selected.id) return;
      const node = this.cy.getElementById(selected.id);
      if (node.nonempty()) node.data('is_selected', true);
    },

    renderGraph(graph) {
      if (!this.cy) return;

      const store = this.$store.app;
      const selectionKey = `${store.selectedType}|${store.namespace}|${store.context}`;
      const selectionChanged = selectionKey !== this.lastSelectionKey;
      this.lastSelectionKey = selectionKey;

      if (!graph || graph.nodes.length === 0) {
        this.cy.elements().remove();
        return;
      }

      const elements = [
        ...graph.nodes.map((n) => ({ data: { ...n } })),
        ...graph.edges.map((e) => ({
          data: {
            id: e.id,
            source: e.source,
            target: e.target,
            relation: e.relation,
            label: RELATION_LABEL[e.relation] || e.relation,
          },
        })),
      ];

      const newIds = new Set(elements.map((el) => el.data.id));
      const structureUnchanged =
        !selectionChanged && sameIdSet(this.cy.elements(), newIds);

      if (structureUnchanged) {
        // Same view, same set of nodes/edges (the common case on most poll
        // ticks): just refresh each element's data in place. cytoscape-node-html-label
        // re-renders a card's content on its `data` event without moving it or
        // touching pan/zoom, so the user's current view is left untouched.
        graph.nodes.forEach((n) => this.cy.getElementById(n.id).data({ ...n }));
        graph.edges.forEach((e) =>
          this.cy.getElementById(e.id).data({
            relation: e.relation,
            label: RELATION_LABEL[e.relation] || e.relation,
          })
        );
        return;
      }

      // Either a fresh selection (type/namespace/context changed) or the
      // underlying node/edge set actually changed shape during polling.
      // Preserve the user's pan/zoom across a background data change; only
      // reset the view to a fresh fit when the selection itself changed.
      const pan = this.cy.pan();
      const zoom = this.cy.zoom();
      this.cy.elements().remove();
      this.cy.add(elements);
      this.reapplySelectionHighlight();
      this.runLayout(selectionChanged ? undefined : { pan, zoom });
    },

    runLayout(preservedView) {
      // Unconnected graphs (e.g. PersistentVolumes, which have no edges) fall
      // into a single dagre rank and get laid out as one very tall column,
      // forcing a tiny fit-to-screen zoom. A grid reads far better for those.
      const hasEdges = this.cy.edges().length > 0;
      // Nodes fan out to potentially dozens of pods each; with the default
      // left-to-right rank direction those pods (all one rank) stack into a
      // single tall vertical column. Ranking top-to-bottom instead spreads
      // them horizontally, which reads much better for a wide, shallow fan-out.
      const isNodesView = this.$store.app.selectedType === 'cluster/nodes';
      const layout = hasEdges
        ? { name: 'dagre', rankDir: isNodesView ? 'TB' : 'LR', nodeSep: 24, rankSep: 90, animate: false }
        : { name: 'grid', condense: true, avoidOverlapPadding: 24, animate: false };
      this.cy.layout(layout).run();
      if (preservedView) {
        // Order matters: cytoscape's zoom(level) setter can itself shift pan
        // to keep the viewport centered, so set zoom first and pan last to
        // guarantee the final state matches exactly what was captured.
        this.cy.zoom(preservedView.zoom);
        this.cy.pan(preservedView.pan);
      } else if (this.isInitialRender) {
        this.cy.zoom(1);
        this.cy.center();
      } else {
        this.cy.fit(undefined, 40);
      }
      this.isInitialRender = false;
    },

    fit() {
      if (this.cy.elements().length) this.cy.fit(undefined, 40);
    },

    zoomIn() {
      this.cy.zoom({ level: this.cy.zoom() * 1.25, renderedPosition: this.centerPoint() });
    },

    zoomOut() {
      this.cy.zoom({ level: this.cy.zoom() / 1.25, renderedPosition: this.centerPoint() });
    },

    centerPoint() {
      return { x: this.$refs.canvas.clientWidth / 2, y: this.$refs.canvas.clientHeight / 2 };
    },

    // Cytoscape's own png()/jpg() export only rasterizes what's drawn on its
    // <canvas> -- every card's actual content (icon, title, badges) is a
    // separate HTML overlay (see cytoscape-node-html-label in init(), and
    // 'background-opacity': 0 on the node style above), so a plain cy.png()
    // export produces only bare edges on blank space. html2canvas rasterizes
    // the composited canvas+DOM together, matching what's actually on screen.
    async exportImage() {
      if (!this.cy.elements().length) return;
      const bg = getComputedStyle(document.documentElement).getPropertyValue('--page-bg').trim();
      const canvas = await html2canvas(this.$refs.canvas, { backgroundColor: bg, scale: 2 });
      canvas.toBlob((blob) => {
        if (!blob) return;
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = this.exportFilename();
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
      }, 'image/png');
    },

    exportFilename() {
      const store = this.$store.app;
      const typeSlug = (store.selectedType || 'graph').replace(/\//g, '-');
      const namespace = store.namespace || 'all-namespaces';
      const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
      return `krowser-${typeSlug}-${namespace}-${stamp}.png`;
    },

    togglePan() {
      this.panMode = !this.panMode;
      this.cy.userPanningEnabled(this.panMode);
      this.cy.boxSelectionEnabled(!this.panMode);
    },

    selectFromContextMenu() {
      this.$store.app.selectedResource = this.contextMenu.resource;
      this.$store.app.detailResource = { ...this.contextMenu.resource, view: 'yaml' };
      this.contextMenu.visible = false;
    },

    describeFromContextMenu() {
      this.$store.app.selectedResource = this.contextMenu.resource;
      this.$store.app.detailResource = { ...this.contextMenu.resource, view: 'describe' };
      this.contextMenu.visible = false;
    },

    eventsFromContextMenu() {
      this.$store.app.selectedResource = this.contextMenu.resource;
      this.$store.app.detailResource = { ...this.contextMenu.resource, view: 'events' };
      this.contextMenu.visible = false;
    },

    execFromContextMenu() {
      const container = (this.contextMenu.resource.containers || [])[0];
      this.$store.app.selectedResource = this.contextMenu.resource;
      this.$store.app.detailResource = { ...this.contextMenu.resource, view: 'exec', container };
      this.contextMenu.visible = false;
    },

    scanFromContextMenu() {
      const container = (this.contextMenu.resource.containers || [])[0];
      this.$store.app.selectedResource = this.contextMenu.resource;
      this.$store.app.detailResource = { ...this.contextMenu.resource, view: 'vulnscan', container };
      this.contextMenu.visible = false;
    },

    kubescanFromContextMenu() {
      this.$store.app.selectedResource = this.contextMenu.resource;
      this.$store.app.detailResource = { ...this.contextMenu.resource, view: 'kubescan' };
      this.contextMenu.visible = false;
    },

    viewLogsFromContextMenu() {
      // Defaults to the first container (init containers first, then
      // regular -- see krowser.k8s.status.build_node); the detail pane's own
      // dropdown lets the user switch to any other container afterward.
      const container = (this.contextMenu.resource.containers || [])[0];
      this.$store.app.selectedResource = this.contextMenu.resource;
      this.$store.app.detailResource = { ...this.contextMenu.resource, view: 'logs', container };
      this.contextMenu.visible = false;
    },

    closeContextMenu() {
      this.contextMenu.visible = false;
    },
  };
}

const RELATION_LABEL = {
  owns: 'owns',
  selects: 'selects',
  'routes-to': 'routes to',
  claims: 'claims',
  binds: 'binds',
  uses: 'uses',
  exposes: 'exposes',
  targets: 'targets',
};

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

    init() {
      this.cy = cytoscape({
        container: this.$refs.canvas,
        style: [
          {
            selector: 'node',
            style: {
              width: 224,
              height: 66,
              shape: 'roundrectangle',
              'background-opacity': 0,
              'border-width': 0,
            },
          },
          {
            selector: 'edge',
            style: {
              width: 1.5,
              'line-color': '#a9c9c2',
              'target-arrow-color': '#a9c9c2',
              'target-arrow-shape': 'triangle',
              'arrow-scale': 0.9,
              'curve-style': 'bezier',
            },
          },
        ],
        layout: { name: 'preset' },
        boxSelectionEnabled: false,
        autounselectify: true,
        autoungrabify: true,
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
      });

      // The node-html-label overlay has pointer-events:none (clicks pass through
      // to the canvas, which is what lets the 'tap' handler above work at all),
      // so the browser won't show a hover cursor on its own -- set it manually.
      this.cy.on('mouseover', 'node', () => {
        this.$refs.canvas.style.cursor = 'pointer';
      });
      this.cy.on('mouseout', 'node', () => {
        this.$refs.canvas.style.cursor = '';
      });

      this.cy.on('tap', (evt) => {
        if (evt.target === this.cy) {
          this.$store.app.selectedResource = null;
        }
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
        // The detail pane's open/closed state changes the graph container's
        // width; cytoscape needs to be told explicitly once the browser has
        // applied that layout change.
        requestAnimationFrame(() => this.cy.resize());
      });

      // Also keep the canvas in sync while the user drags the detail pane's
      // resize handle (fires continuously during drag; cy.resize() is cheap).
      this.$watch('$store.app.detailPaneWidth', () => {
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
      // Unconnected graphs (e.g. ConfigMaps/Secrets, which have no edges) fall
      // into a single dagre rank and get laid out as one very tall column,
      // forcing a tiny fit-to-screen zoom. A grid reads far better for those.
      const hasEdges = this.cy.edges().length > 0;
      const layout = hasEdges
        ? { name: 'dagre', rankDir: 'LR', nodeSep: 24, rankSep: 90, animate: false }
        : { name: 'grid', condense: true, avoidOverlapPadding: 24, animate: false };
      this.cy.layout(layout).run();
      if (preservedView) {
        // Order matters: cytoscape's zoom(level) setter can itself shift pan
        // to keep the viewport centered, so set zoom first and pan last to
        // guarantee the final state matches exactly what was captured.
        this.cy.zoom(preservedView.zoom);
        this.cy.pan(preservedView.pan);
      } else {
        this.cy.fit(undefined, 40);
      }
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

    togglePan() {
      this.panMode = !this.panMode;
      this.cy.userPanningEnabled(this.panMode);
      this.cy.boxSelectionEnabled(!this.panMode);
    },
  };
}

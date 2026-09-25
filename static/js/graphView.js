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
  restricts: 'restricts',
  'allows-from': 'allows from',
  'allows-to': 'allows to',
  protects: 'protects',
  attaches: 'attaches',
};

// Kubescape's "scan workload" refuses to scan a Pod that has an owner (which
// is virtually every Pod krowser shows), so "Scan with Kubescape" is offered
// only for these top-level controller kinds -- never Pod.
const WORKLOAD_CONTROLLER_KINDS = ['DaemonSet', 'Deployment', 'StatefulSet', 'CronJob', 'Job'];

// Keep in sync with the cytoscape node style's `height` below -- see
// applyMeasuredHeights() for why this is only a fallback, not the truth.
const DEFAULT_NODE_HEIGHT = 80;

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
    hiddenKinds: {},
    lastSelectionKey: null,
    // The very first graph the app renders after loading defaults to a
    // fixed 100% zoom instead of the usual fit-to-screen; cleared after
    // that first render so later type/namespace/context switches keep
    // fitting the whole graph on screen as before.
    isInitialRender: true,
    contextMenu: { visible: false, x: 0, y: 0, resource: null },
    hoveredNode: null,
    hasNoFilterMatches: false,

    init() {
      this.cy = cytoscape({
        container: this.$refs.canvas,
        style: [
          {
            selector: 'node',
            style: {
              width: 224,
              height: DEFAULT_NODE_HEIGHT,
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
          // ':visible' (not just 'node') so a node hidden via
          // style('display', 'none') -- see applyVisibility() -- drops its
          // HTML overlay too; the plugin listens for 'style' events and
          // re-checks this query, removing/re-adding the overlay div as it
          // changes.
          query: 'node:visible',
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
            // Only set for a custom resource instance -- see GraphNode.crd
            // and resourcePanel.js's YAML fetch, which needs it to resolve
            // an arbitrary CRD Kind back to a group/version/plural server-side.
            crd: data.crd || null,
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

      this.$watch('$store.app.filterText', () => this.applyVisibility());
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
      // A fresh selection means a different set of kinds may be present (or
      // the same kind names may mean something different) -- start every
      // new view with everything visible rather than carrying over the
      // previous view's toggles.
      if (selectionChanged) this.hiddenKinds = {};

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
            // e.label carries a backend-combined label (e.g. "restricts,
            // allows from") when multiple relations were merged into this
            // one edge -- see builder.py's _merge_parallel_edges.
            label: e.label || RELATION_LABEL[e.relation] || e.relation,
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
        // Deliberately not re-measuring heights here (see
        // applyMeasuredHeights()): the plugin updates the overlay's real DOM
        // content on a deferred setTimeout(0) in response to this `data`
        // event, not synchronously, so a remeasure right here would read
        // stale content anyway. The previous height override isn't cleared
        // by this refresh, so nothing regresses -- the next full relayout
        // (any selection change, or a poll tick where the node/edge set
        // itself changes) re-measures and picks up anything that changed.
        graph.nodes.forEach((n) => this.cy.getElementById(n.id).data({ ...n }));
        graph.edges.forEach((e) =>
          this.cy.getElementById(e.id).data({
            relation: e.relation,
            label: e.label || RELATION_LABEL[e.relation] || e.relation,
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
      const added = this.cy.add(elements);
      this.applyMeasuredHeights(added.nodes());
      this.reapplySelectionHighlight();
      this.runLayout(selectionChanged ? undefined : { pan, zoom });
      // Freshly added elements default to visible -- only worth re-applying
      // the name filter/kind toggles (and re-fitting to that subset) when
      // one is actually active; otherwise skip entirely so the pan/zoom
      // preservation above isn't immediately overridden by a
      // fit-to-everything call.
      if (this.$store.app.filterText.trim() || Object.keys(this.hiddenKinds).length > 0) {
        this.applyVisibility();
      }
    },

    // The cytoscape node style's height (DEFAULT_NODE_HEIGHT) is a fixed
    // guess; the actual visible card is a separately-sized HTML overlay (see
    // cardTemplate.js / cytoscape-node-html-label) whose height is driven
    // purely by its content -- a NetworkPolicy's rule-count badges routinely
    // wrap onto more rows than a Pod's, making it taller than the guess.
    // Read each node's *real* rendered card height from the DOM right after
    // it's added (its overlay div is guaranteed to exist synchronously by
    // then -- the plugin's 'add' handler parses/inserts it inline, no
    // setTimeout) and store it as a per-node style override, so dagre's
    // nodeSep math and separateOverlappingSiblings() both operate on the
    // true height instead of the uniform guess. Floored at
    // DEFAULT_NODE_HEIGHT so compact cards (the common case) never shrink
    // below today's baseline spacing.
    //
    // getBoundingClientRect() returns screen pixels, i.e. already scaled by
    // the overlay container's current pan/zoom transform -- but node
    // heights live in cytoscape's own unscaled graph-unit space (the same
    // space DEFAULT_NODE_HEIGHT/224 are defined in), so the measurement has
    // to be divided back out by the current zoom before it's usable, or a
    // graph left zoomed out from a previous view would systematically
    // under-report every card's true height here.
    applyMeasuredHeights(nodes) {
      const zoom = this.cy.zoom();
      const heightById = new Map();
      this.$refs.canvas.querySelectorAll('.krw-card[data-node-id]').forEach((card) => {
        heightById.set(card.dataset.nodeId, card.getBoundingClientRect().height / zoom);
      });
      nodes.forEach((n) => {
        const measured = heightById.get(n.id());
        if (measured) n.style('height', Math.max(measured, DEFAULT_NODE_HEIGHT));
      });
    },

    // Runs on `eles` (default: the whole graph) so a filtered subset can be
    // re-laid-out and tightened up on its own, independent of the full set.
    runLayout(preservedView, eles) {
      const collection = eles || this.cy.elements();
      // Unconnected graphs (e.g. PersistentVolumes, which have no edges) fall
      // into a single dagre rank and get laid out as one very tall column,
      // forcing a tiny fit-to-screen zoom. A grid reads far better for those.
      const hasEdges = collection.edges().length > 0;
      // Nodes fan out to potentially dozens of pods each; with the default
      // left-to-right rank direction those pods (all one rank) stack into a
      // single tall vertical column. Ranking top-to-bottom instead spreads
      // them horizontally, which reads much better for a wide, shallow fan-out.
      const isNodesView = this.$store.app.selectedType === 'cluster/nodes';
      const rankDir = isNodesView ? 'TB' : 'LR';
      const layout = hasEdges
        ? { name: 'dagre', rankDir, nodeSep: 24, rankSep: 90, animate: false }
        : { name: 'grid', condense: true, avoidOverlapPadding: 24, animate: false };
      collection.layout(layout).run();
      if (hasEdges) this.separateOverlappingSiblings(collection.nodes(), rankDir);
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
        this.cy.fit(eles, 40);
      }
      this.isInitialRender = false;
    },

    // Two nodes with no edge between them but identical connectivity (e.g.
    // two NetworkPolicies that each only "restricts" the same single pod,
    // and nothing else) land on the same dagre rank with no edge for dagre's
    // own order/tie-break logic to separate them by -- nodeSep only reliably
    // applies when dagre actually assigns them distinct order slots, which
    // isn't guaranteed for ties, and can otherwise leave them overlapping or
    // nearly so. This defensive pass re-spaces any nodes sharing a rank
    // (grouped with a tolerance, since dagre's own float coordinates for
    // "the same" rank can differ by a few px) along the cross axis, so two
    // cards are never closer than nodeSep regardless of what dagre decided.
    separateOverlappingSiblings(nodes, rankDir) {
      const NODE_SEP = 24;
      const RANK_BUCKET = 20;
      const rankAxis = rankDir === 'TB' ? 'y' : 'x';
      const crossAxis = rankDir === 'TB' ? 'x' : 'y';
      const crossSize = rankDir === 'TB' ? 'width' : 'height';

      const groups = new Map();
      nodes.forEach((n) => {
        const key = Math.round(n.position(rankAxis) / RANK_BUCKET);
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(n);
      });

      groups.forEach((group) => {
        if (group.length < 2) return;
        group.sort((a, b) => a.position(crossAxis) - b.position(crossAxis));
        for (let i = 1; i < group.length; i++) {
          const prev = group[i - 1];
          const curr = group[i];
          const minCross =
            prev.position(crossAxis) + prev[crossSize]() / 2 + NODE_SEP + curr[crossSize]() / 2;
          if (curr.position(crossAxis) < minCross) {
            curr.position(crossAxis, minCross);
          }
        }
      });
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

    // One distinct entry per Kind actually present in the current graph
    // (not every possible kind), each with an icon to match the legend
    // checkbox to what's on screen -- feeds the legend panel in the
    // bottom-left corner. Recomputed reactively off the store's graph, so
    // it stays in sync without any extra bookkeeping.
    get legendKinds() {
      if (!this.$store.app.graph) return [];
      const iconByKind = new Map();
      this.$store.app.graph.nodes.forEach((n) => {
        if (!iconByKind.has(n.kind)) iconByKind.set(n.kind, n.icon);
      });
      return Array.from(iconByKind, ([kind, icon]) => ({ kind, icon })).sort((a, b) =>
        a.kind.localeCompare(b.kind)
      );
    },

    toggleKind(kind) {
      if (this.hiddenKinds[kind]) {
        delete this.hiddenKinds[kind];
      } else {
        this.hiddenKinds[kind] = true;
      }
      this.applyVisibility();
    },

    // Single source of truth for which nodes/edges are actually shown,
    // combining two independent, composable criteria: $store.app.filterText
    // (a name substring, lives in the shared store so it's deep-linkable --
    // see app.js's syncUrl()) and hiddenKinds (the legend's per-Kind
    // checkboxes, local to this view). A name match keeps its immediate
    // neighbors visible too (rather than its whole connected component),
    // since many views funnel everything through one shared node (e.g. a
    // namespace) -- full BFS would keep that entire component and make the
    // filter a no-op. A kind toggle always wins over that neighbor-keeping,
    // though -- hiding "Secret" means no Secret shows even if it's a
    // filter match's own neighbor. The visible subset is then re-laid-out
    // on its own (not just re-fit) so it tightens up into the freed space
    // instead of sitting wherever it happened to land in the full graph.
    applyVisibility() {
      if (!this.cy) return;
      const query = this.$store.app.filterText.trim().toLowerCase();
      let base;
      if (query) {
        const matches = this.cy.nodes().filter((n) => (n.data('name') || '').toLowerCase().includes(query));
        this.hasNoFilterMatches = matches.empty();
        base = matches.closedNeighborhood().nodes();
      } else {
        this.hasNoFilterMatches = false;
        base = this.cy.nodes();
      }

      const visibleNodes = base.filter((n) => !this.hiddenKinds[n.data('kind')]);
      const visibleIds = new Set(visibleNodes.map((n) => n.id()));
      const visibleEdges = this.cy
        .edges()
        .filter((e) => visibleIds.has(e.data('source')) && visibleIds.has(e.data('target')));

      this.cy.elements().style('display', 'none');
      visibleNodes.style('display', 'element');
      visibleEdges.style('display', 'element');
      if (visibleNodes.nonempty()) this.runLayout(undefined, visibleNodes.union(visibleEdges));
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

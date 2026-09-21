function loadStoredDetailPaneWidth() {
  try {
    const stored = parseInt(localStorage.getItem('krowser.detailPaneWidth'), 10);
    return Number.isFinite(stored) ? stored : 420;
  } catch (_) {
    return 420;
  }
}

function loadStoredLeftPaneVisible() {
  try {
    const stored = localStorage.getItem('krowser.leftPaneVisible');
    return stored === null ? true : stored === 'true';
  } catch (_) {
    return true;
  }
}

function loadStoredLeftPaneWidth() {
  try {
    const stored = parseInt(localStorage.getItem('krowser.leftPaneWidth'), 10);
    return Number.isFinite(stored) ? stored : 260;
  } catch (_) {
    return 260;
  }
}

function loadStoredTheme() {
  try {
    const stored = localStorage.getItem('krowser.theme');
    return stored === 'dark' ? 'dark' : 'light';
  } catch (_) {
    return 'light';
  }
}

document.addEventListener('alpine:init', () => {
  Alpine.store('app', {
    contexts: [],
    context: null,
    namespaces: [],
    namespace: 'default', // '' = "All namespaces"
    resourceTypes: [],
    selectedType: null,
    filterText: '', // graph filter box text -- lives here (not in graphView.js) so it's deep-linkable, see appRoot's syncUrl()
    graph: null,
    selectedResource: null, // { id, kind, namespace, name } | null -- the highlighted tile, set by a plain click
    detailResource: null, // { id, kind, namespace, name, view } | null -- drives the detail pane; set only via the context menu
    detailPaneWidth: loadStoredDetailPaneWidth(),
    leftPaneVisible: loadStoredLeftPaneVisible(),
    leftPaneWidth: loadStoredLeftPaneWidth(),
    theme: loadStoredTheme(),
    loading: false,
    detailLoading: false,
    error: null,
    lastUpdated: null,
    pollMs: 10000,

    // True while any in-flight request the user is waiting on (the main
    // graph refresh or the detail pane's YAML/describe/logs fetch) hasn't
    // resolved yet -- drives a global busy cursor, see index.html/styles.css.
    get busy() {
      return this.loading || this.detailLoading;
    },

    // Namespace filtering only shrinks the result set for namespaced types
    // and the one cluster-scoped exception (PersistentVolume, which
    // fetch_root filters to PVs bound within the chosen namespace). For
    // other cluster-scoped types (Nodes, and any cluster-scoped custom
    // resource) picking a namespace is a no-op, so the truncation banner
    // shouldn't suggest it as a fix.
    get namespaceFilterHelps() {
      if (this.namespace) return false;
      const rt = this.resourceTypes.find((t) => t.id === this.selectedType);
      return !!rt && (rt.namespaced || rt.id === 'storage/persistentvolumes');
    },
  });

  Alpine.data('appRoot', () => ({
    pollTimer: null,
    requestSeq: 0,
    refreshInFlight: false,

    async init() {
      const store = this.$store.app;
      // Read once, up front -- everything below that restores from these
      // params must finish before syncUrl() watchers are registered at the
      // end of this method, or they'd immediately overwrite what we just read.
      const params = new URLSearchParams(window.location.search);
      let pendingResource = null;

      try {
        // Contexts must resolve before resource types can be fetched with
        // the right context: an invalid/stale ?context= from a stale link
        // needs to fall back to the server's current context rather than
        // be sent straight through and error the whole init.
        const contextsRes = await api.getContexts();
        store.contexts = contextsRes.contexts;
        const requestedContext = params.get('context');
        store.context =
          requestedContext && contextsRes.contexts.some((c) => c.name === requestedContext)
            ? requestedContext
            : contextsRes.current;

        const typesRes = await api.getResourceTypes(store.context);
        store.resourceTypes = typesRes.resource_types;
        const requestedType = params.get('type');
        // Pods is selected by default on load (Workloads is pre-expanded in
        // the left-pane menu to match, see resourceList.js), falling back to
        // nothing selected if it's ever missing from the type list.
        store.selectedType =
          requestedType && store.resourceTypes.some((rt) => rt.id === requestedType)
            ? requestedType
            : store.resourceTypes.some((rt) => rt.id === 'workloads/pods')
              ? 'workloads/pods'
              : null;

        // '' (all namespaces) is a meaningful explicit choice, distinct from
        // "no ?ns= param at all" -- has() (not the value) is what tells them apart.
        if (params.has('ns')) store.namespace = params.get('ns');
        if (params.has('filter')) store.filterText = params.get('filter');
        if (params.has('resKind')) {
          pendingResource = {
            kind: params.get('resKind'),
            namespace: params.get('resNs') || null,
            name: params.get('resName'),
          };
        }

        await this.loadNamespaces();
      } catch (e) {
        store.error = e.message;
      }
      this.restartPolling();
      await this.refresh();

      // Deferred until here (not resolved from params directly) since it
      // needs to match against a real node from the graph we just fetched --
      // the URL only carries kind/namespace/name, not the backend's
      // internal node id, which can change if the resource is recreated.
      if (pendingResource) this.resolveDeepLinkedResource(pendingResource);

      this.$watch('$store.app.context', () => this.syncUrl());
      this.$watch('$store.app.namespace', () => this.syncUrl());
      this.$watch('$store.app.selectedType', () => this.syncUrl());
      this.$watch('$store.app.selectedResource', () => this.syncUrl());
      this.$watch('$store.app.filterText', () => this.syncUrl());
      this.syncUrl();
    },

    // Only the plain click-to-highlight selection (selectedResource) is
    // deep-linked, not detailResource (the right-click "Get <kind>"/logs/etc.
    // pane) -- kept out of scope for now, see the URL-only-reflects-a-few
    // top-level view fields the app already had.
    resolveDeepLinkedResource(target) {
      const store = this.$store.app;
      if (!store.graph) return;
      const match = store.graph.nodes.find(
        (n) => n.kind === target.kind && n.name === target.name && (n.namespace || null) === target.namespace
      );
      if (match) {
        store.selectedResource = { id: match.id, kind: match.kind, namespace: match.namespace, name: match.name };
      }
    },

    // Keeps the URL in sync with the handful of top-level view fields, so
    // the current view is bookmarkable/shareable. Always replaceState (never
    // pushState) -- e.g. every filter-box keystroke would otherwise spam
    // browser history; back/forward navigating between views isn't a goal here.
    syncUrl() {
      const store = this.$store.app;
      const params = new URLSearchParams();
      if (store.context) params.set('context', store.context);
      if (store.namespace !== 'default') params.set('ns', store.namespace);
      if (store.selectedType) params.set('type', store.selectedType);
      if (store.filterText) params.set('filter', store.filterText);
      if (store.selectedResource) {
        params.set('resKind', store.selectedResource.kind);
        if (store.selectedResource.namespace) params.set('resNs', store.selectedResource.namespace);
        params.set('resName', store.selectedResource.name);
      }
      const qs = params.toString();
      const url = qs ? `${window.location.pathname}?${qs}` : window.location.pathname;
      history.replaceState(null, '', url);
    },

    async loadNamespaces() {
      const store = this.$store.app;
      const res = await api.getNamespaces(store.context);
      store.namespaces = res.namespaces;
    },

    onTypeSelect(id) {
      this.$store.app.selectedType = id;
      this.$store.app.selectedResource = null;
      this.$store.app.detailResource = null;
      this.restartPolling();
      this.refresh();
    },

    async onContextChange() {
      const store = this.$store.app;
      store.namespace = '';
      store.selectedResource = null;
      store.detailResource = null;
      try {
        await this.loadNamespaces();
        const typesRes = await api.getResourceTypes(store.context);
        store.resourceTypes = typesRes.resource_types;
        if (!store.resourceTypes.some((rt) => rt.id === store.selectedType)) {
          store.selectedType = store.resourceTypes[0]?.id ?? null;
        }
      } catch (e) {
        store.error = e.message;
      }
      this.restartPolling();
      await this.refresh();
    },

    onNamespaceChange() {
      this.$store.app.selectedResource = null;
      this.$store.app.detailResource = null;
      this.restartPolling();
      this.refresh();
    },

    toggleLeftPane() {
      const store = this.$store.app;
      store.leftPaneVisible = !store.leftPaneVisible;
      try {
        localStorage.setItem('krowser.leftPaneVisible', String(store.leftPaneVisible));
      } catch (_) {
        // localStorage unavailable (private browsing, etc.) -- preference just won't persist.
      }
    },

    toggleTheme() {
      const store = this.$store.app;
      store.theme = store.theme === 'dark' ? 'light' : 'dark';
      try {
        localStorage.setItem('krowser.theme', store.theme);
      } catch (_) {
        // localStorage unavailable (private browsing, etc.) -- preference just won't persist.
      }
    },

    restartPolling() {
      if (this.pollTimer) clearInterval(this.pollTimer);
      // Skip a tick while the previous refresh() is still in flight, rather
      // than firing another one -- if a query's latency exceeds pollMs (e.g.
      // a Workloads type across all namespaces on a busy cluster), every
      // tick would otherwise bump requestSeq again before the prior request
      // resolves, so its result is discarded by the staleness guard in
      // refresh() every time and store.graph can never be assigned: a
      // livelock, not just a slow load. User-driven calls to refresh()
      // (onTypeSelect/onContextChange/onNamespaceChange, the manual Refresh
      // button) are untouched and always fire immediately.
      this.pollTimer = setInterval(() => {
        if (!this.refreshInFlight) this.refresh();
      }, this.$store.app.pollMs);
    },

    async refresh() {
      const store = this.$store.app;
      if (!store.selectedType) return;

      this.refreshInFlight = true;

      // Guard against out-of-order responses: if the user changes filters
      // again (or a fast namespace-scoped request outruns a slow, larger
      // in-flight all-namespaces one), only the most recently issued
      // request's result is allowed to update the store.
      const requestId = ++this.requestSeq;

      const spinnerTimer = setTimeout(() => {
        if (requestId === this.requestSeq) store.loading = true;
      }, 200);

      try {
        const graph = await api.getGraph({
          type: store.selectedType,
          namespace: store.namespace,
          context: store.context,
        });
        if (requestId !== this.requestSeq) return;
        store.graph = graph;
        store.error = null;
        store.lastUpdated = new Date();
      } catch (e) {
        if (requestId !== this.requestSeq) return;
        // Keep the last-good graph rendered; surface the error as a dismissible banner.
        store.error = e.message;
      } finally {
        clearTimeout(spinnerTimer);
        if (requestId === this.requestSeq) store.loading = false;
        this.refreshInFlight = false;
      }
    },
  }));
});

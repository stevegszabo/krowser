function loadStoredDetailPaneWidth() {
  try {
    const stored = parseInt(localStorage.getItem('krowser.detailPaneWidth'), 10);
    return Number.isFinite(stored) ? stored : 420;
  } catch (_) {
    return 420;
  }
}

document.addEventListener('alpine:init', () => {
  Alpine.store('app', {
    contexts: [],
    context: null,
    namespaces: [],
    namespace: '', // '' = "All namespaces"
    resourceTypes: [],
    selectedType: null,
    graph: null,
    selectedResource: null, // { kind, namespace, name } | null
    detailPaneWidth: loadStoredDetailPaneWidth(),
    loading: false,
    error: null,
    lastUpdated: null,
    pollMs: 10000,
  });

  Alpine.data('appRoot', () => ({
    pollTimer: null,
    requestSeq: 0,

    async init() {
      const store = this.$store.app;
      try {
        const [contextsRes, typesRes] = await Promise.all([
          api.getContexts(),
          api.getResourceTypes(),
        ]);
        store.contexts = contextsRes.contexts;
        store.context = contextsRes.current;
        store.resourceTypes = typesRes.resource_types;
        store.selectedType = typesRes.resource_types[0]?.id ?? null;
        await this.loadNamespaces();
      } catch (e) {
        store.error = e.message;
      }
      this.restartPolling();
      await this.refresh();
    },

    async loadNamespaces() {
      const store = this.$store.app;
      const res = await api.getNamespaces(store.context);
      store.namespaces = res.namespaces;
    },

    onTypeSelect(id) {
      this.$store.app.selectedType = id;
      this.$store.app.selectedResource = null;
      this.restartPolling();
      this.refresh();
    },

    async onContextChange() {
      const store = this.$store.app;
      store.namespace = '';
      store.selectedResource = null;
      try {
        await this.loadNamespaces();
      } catch (e) {
        store.error = e.message;
      }
      this.restartPolling();
      await this.refresh();
    },

    onNamespaceChange() {
      this.$store.app.selectedResource = null;
      this.restartPolling();
      this.refresh();
    },

    restartPolling() {
      if (this.pollTimer) clearInterval(this.pollTimer);
      this.pollTimer = setInterval(() => this.refresh(), this.$store.app.pollMs);
    },

    async refresh() {
      const store = this.$store.app;
      if (!store.selectedType) return;

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
      }
    },
  }));
});

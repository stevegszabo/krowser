class ApiError extends Error {}

async function apiGet(path, params = {}) {
  const url = new URL(path, window.location.origin);
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') {
      url.searchParams.set(key, value);
    }
  }
  const res = await fetch(url);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch (_) {
      // response body wasn't JSON; fall back to statusText
    }
    throw new ApiError(`${res.status} ${detail}`);
  }
  return res.json();
}

const api = {
  getContexts: () => apiGet('/api/contexts'),
  getNamespaces: (context) => apiGet('/api/namespaces', { context }),
  getResourceTypes: (context) => apiGet('/api/resource-types', { context }),
  getResources: (params) => apiGet('/api/resources', params),
  getGraph: (params) => apiGet('/api/graph', params),
  getResourceYaml: (params) => apiGet('/api/resource-yaml', params),
  getPodDescribe: (params) => apiGet('/api/pod-describe', params),
  getPodLogs: (params) => apiGet('/api/pod-logs', params),
};

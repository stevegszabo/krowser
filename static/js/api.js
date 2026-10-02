class ApiError extends Error {}

async function handleJsonResponse(res) {
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

async function apiGet(path, params = {}) {
  const url = new URL(path, window.location.origin);
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') {
      url.searchParams.set(key, value);
    }
  }
  return handleJsonResponse(await fetch(url));
}

async function apiPost(path, body = {}) {
  const res = await fetch(new URL(path, window.location.origin), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return handleJsonResponse(res);
}

async function apiDelete(path, params = {}) {
  const url = new URL(path, window.location.origin);
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') {
      url.searchParams.set(key, value);
    }
  }
  return handleJsonResponse(await fetch(url, { method: 'DELETE' }));
}

const api = {
  getContexts: () => apiGet('/api/contexts'),
  getNamespaces: (context) => apiGet('/api/namespaces', { context }),
  getResourceTypes: (context) => apiGet('/api/resource-types', { context }),
  getCrds: (context) => apiGet('/api/crds', { context }),
  getResources: (params) => apiGet('/api/resources', params),
  getGraph: (params) => apiGet('/api/graph', params),
  getResourceYaml: (params) => apiGet('/api/resource-yaml', params),
  getPodDescribe: (params) => apiGet('/api/pod-describe', params),
  getResourceEvents: (params) => apiGet('/api/resource-events', params),
  getPodLogs: (params) => apiGet('/api/pod-logs', params),
  getPodVulnScan: (params) => apiGet('/api/pod-vulnscan', params),
  getWorkloadKubescan: (params) => apiGet('/api/workload-kubescan', params),
  getScale: (params) => apiGet('/api/scale', params),
  scaleWorkload: (body) => apiPost('/api/scale', body),
  getRolloutStatus: (params) => apiGet('/api/rollout-status', params),
  terminatePod: (params) => apiDelete('/api/pod', params),
  restartWorkload: (body) => apiPost('/api/restart', body),
  getRolloutHistory: (params) => apiGet('/api/rollout-history', params),
  rollbackWorkload: (body) => apiPost('/api/rollback', body),
};

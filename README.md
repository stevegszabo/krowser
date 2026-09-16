# krowser

A web app for browsing the resources in a Kubernetes cluster and how they relate to each other — pick a resource type, filter by namespace, and see an ArgoCD-style graph of ownership, networking, and storage relationships. Click a node to select it; right-click for actions — view its full YAML, and for Pods, get a description, view container logs, or open an interactive `kubectl exec`-style terminal.

## Screenshots

![StatefulSets view showing the full relationship graph for a vault StatefulSet, with the YAML detail pane open on a pod](krowser-01.png)
*StatefulSets view (`base-vault` namespace) — Services and EndpointSlices routing to the `vault-0` pod, alongside the ConfigMap, Secret, and PersistentVolumeClaim/PersistentVolume it uses, with the resizable read-only YAML pane open.*

## Features

- Browse 13 built-in resource types grouped into **Cluster** (Nodes), **Config** (ConfigMaps, Secrets), **Network** (Ingresses, Services), **Storage** (PersistentVolumes, PersistentVolumeClaims), and **Workloads** (DaemonSets, Deployments, StatefulSets, CronJobs, Jobs, Pods)
- Collapse/expand each left-pane group (CLUSTER, CONFIG, NETWORK, STORAGE, WORKLOADS) independently
- Filter by namespace (or view all namespaces at once) and by kubeconfig context
- Relationship graph derived from real cluster state: owner references (e.g. Deployment→ReplicaSet→Pod), Service↔Pod label-selector matching, Ingress routing, PersistentVolumeClaim/PersistentVolume binding, ConfigMap/Secret usage (volume mounts, `envFrom`, individual env vars), and Service→EndpointSlice→Pod (shown on the Ingresses and Services views)
- Node and Pod tiles show live CPU/memory usage when the cluster has metrics-server installed, fetched from the Metrics API — Node tiles also show each value as a percentage of the node's allocatable capacity (e.g. `768m (4%) / 13147Mi (41%)`), matching `kubectl top node`'s own columns; Pod tiles show plain usage only (e.g. `50m / 1687Mi`), matching `kubectl top pod`
- Right-click any node for a context menu of actions, opening a resizable detail pane:
  - **Get \<kind\>** — the resource's full YAML, fetched fresh from the cluster
  - **Get pod description** (Pods only) — a `kubectl describe`-style summary (containers, conditions, volumes, and recent Events), built entirely via the Kubernetes API
  - **Get pod logs** (Pods only) — the last 100 log lines for the pod's first container, with a dropdown at the top of the pane to switch to any other container, plus a text filter that narrows to matching lines and highlights the matched text
  - **Execute command** (Pods only) — a `kubectl exec`-style terminal: pick a container from the dropdown, type a command (a one-shot command like `date`, or an interactive one like `sh`/`bash`), and press Run to stream it live in a real terminal (powered by xterm.js) over a WebSocket to the Kubernetes exec API, with full TTY resize support
  - **Scan for vulnerabilities** (Pods only) — pick a container from the dropdown and press Scan to check its image for known CVEs via [Trivy](https://github.com/aquasecurity/trivy), showing severity counts and a findings list (CVE ID, package, installed/fixed version); requires `trivy` on `PATH` (see Configuration) and currently only works for publicly pullable images
  - **Scan with Kubescape** (DaemonSets, Deployments, StatefulSets, CronJobs, and Jobs only — not Pods, since [Kubescape](https://github.com/kubescape/kubescape) itself refuses to scan a Pod that has an owner) — press Scan to check the resource's configuration against Kubescape's compliance controls (resource limits, non-root, privilege escalation, network policy, etc.), showing a compliance score, severity counts, and a list of failed controls; requires `kubescape` on `PATH` (see Configuration)
- Auto-refreshes on a polling interval without resetting your pan/zoom or losing your current selection unless the underlying resource set actually changes
- Read-only with respect to cluster resources — no create/edit/delete/scale actions; the exceptions are **Execute command**, which runs a process inside a pod's container exactly like `kubectl exec`, and the two **Scan** actions, which pull the image (Trivy) or read live cluster state (Kubescape) being scanned

## Requirements

- Python 3.11+
- A working kubeconfig with access to the cluster you want to browse
- [Trivy](https://github.com/aquasecurity/trivy) on `PATH`, only if you want to use **Scan for vulnerabilities** — every other feature works without it
- [Kubescape](https://github.com/kubescape/kubescape) on `PATH`, only if you want to use **Scan with Kubescape** — every other feature works without it

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate   # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
./run.sh
```

Then open `http://localhost:8000`.

## Configuration

Environment variables:

| Variable | Default | Description |
|---|---|---|
| `KUBECONFIG` | (system default, usually `~/.kube/config`) | Path to the kubeconfig file to load contexts from |
| `KROWSER_MAX_GRAPH_NODES` | `150` | Caps the number of root nodes shown in a single graph (e.g. when viewing a resource type across all namespaces); the response reports the true count and flags when it's been truncated |
| `KROWSER_VULNSCAN_TRIVY_PATH` | `trivy` | Path to (or name on `PATH` of) the Trivy binary used by **Scan for vulnerabilities** |
| `KROWSER_VULNSCAN_TIMEOUT_SECONDS` | `180` | How long to let a single image scan run before giving up |
| `KROWSER_VULNSCAN_CACHE_TTL_SECONDS` | `3600` | How long a scan result is cached per image, since many pods often share a base image |
| `KROWSER_KUBESCAPE_PATH` | `kubescape` | Path to (or name on `PATH` of) the Kubescape binary used by **Scan with Kubescape** |
| `KROWSER_KUBESCAPE_TIMEOUT_SECONDS` | `180` | How long to let a single Kubescape scan run before giving up |
| `KROWSER_KUBESCAPE_CACHE_TTL_SECONDS` | `3600` | How long a Kubescape scan result is cached per resource |

## Development

Install the test tooling on top of the runtime dependencies, then run the test suite:

```bash
pip install -r requirements-dev.txt
pytest
```

## Project structure

| Path | Description |
|---|---|
| `krowser/main.py` | FastAPI app |
| `krowser/config.py` | Environment-driven settings |
| `krowser/k8s/` | Kubernetes client wrapper, per-kind fetchers/getters, status/health logic |
| `krowser/graph/` | Relationship-derivation and graph-building logic |
| `krowser/api/` | HTTP routes |
| `krowser/vuln_scan.py` | Container image CVE scanning via Trivy |
| `krowser/kubescape_scan.py` | Workload configuration/compliance scanning via Kubescape |
| `krowser/scan_errors.py` | Shared error types for the Trivy/Kubescape scanner integrations |
| `static/index.html` | App shell |
| `static/css/` | Styles |
| `static/js/` | Alpine.js components + Cytoscape.js graph rendering (no build step) |
| `static/vendor/` | Vendored JS dependencies (Alpine, Cytoscape + extensions, xterm.js) |
| `tests/` | Test suite |

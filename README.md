# krowser

A web app for browsing the resources in a Kubernetes cluster and how they relate to each other — pick a resource type, filter by namespace, and see an ArgoCD-style graph of ownership, networking, and storage relationships. Click a node to select it; right-click for actions — view its full YAML, and for Pods, get a description, view container logs, or open an interactive `kubectl exec`-style terminal.

## Screenshots

![StatefulSets view showing the relationship graph for the vault StatefulSet, with the Kubescape compliance scan results open](krowser-01.png)
*StatefulSets view (`base-vault` namespace) — the `vault` StatefulSet's relationship graph (its Pod, ConfigMap, Secret, PersistentVolumeClaim/PersistentVolume, ServiceAccount, and ClusterRoleBinding/ClusterRole), with a Kubescape compliance scan open showing its score and findings by severity.*

![Deployments view showing the relationship graph for the argocd-server Deployment, with the YAML detail pane open](krowser-02.png)
*Deployments view (`base-argocd` namespace) — the full ArgoCD Deployments graph, with the resizable read-only YAML pane open on `argocd-server`.*

## Features

- Browse 17 built-in resource types grouped into **Cluster** (Nodes, Problems, Namespaces, Custom Resources), **Config** (ConfigMaps, Secrets), **Network** (Ingresses, Services, Policies), **Storage** (PersistentVolumes, PersistentVolumeClaims), and **Workloads** (DaemonSets, Deployments, StatefulSets, CronJobs, Jobs, Pods)
- **Problems** view aggregates every unhealthy resource (degraded, or stuck progressing — CrashLoopBackOff pods, failed Jobs, pending PVCs, a Namespace stuck Terminating, a Node under disk/memory/PID pressure, etc.) across every browsable kind and namespace into a single at-a-glance view
- **Namespaces** view shows a namespace's own labels/annotations and status (Active/Terminating), with its ResourceQuota and LimitRange objects (if any) as connected satellite nodes; selecting a namespace filter narrows straight to that one Namespace
- **Policies** view browses NetworkPolicy objects directly instead of only as a Workloads-view satellite, showing every pod each policy restricts and the ingress/egress peers it allows to/from — including a broadly-scoped policy's full "blast radius" across every pod it touches, which the Workloads views deliberately hide to avoid bridging unrelated workloads together
- **Custom Resources** view browses instances of any installed CustomResourceDefinition (pick one from the dropdown in the toolbar) — surfaces whatever a cluster's operators (ArgoCD Applications, cert-manager Certificates, KubeVirt VirtualMachines, etc.) actually manage, which built-in resource types alone can't show; status is reported as "unknown" health (an arbitrary CRD's schema has no universal pass/fail convention krowser can interpret), though the first `status.conditions[]` entry, when present, is surfaced as reference info
- Collapse/expand each left-pane group (CLUSTER, CONFIG, NETWORK, STORAGE, WORKLOADS) independently
- Filter by namespace (or view all namespaces at once) and by kubeconfig context
- Relationship graph derived from real cluster state: owner references (e.g. Deployment→ReplicaSet→Pod), Service↔Pod label-selector matching, Ingress routing, PersistentVolumeClaim/PersistentVolume binding, VolumeAttachment→PersistentVolume and PersistentVolumeClaim/PersistentVolume→StorageClass (shown on the PersistentVolumes/PersistentVolumeClaims views and every Workloads view), ConfigMap/Secret usage (volume mounts, `envFrom`, individual env vars), Service→EndpointSlice→Pod (shown on the Ingresses and Services views), Pod→ServiceAccount→RoleBinding/ClusterRoleBinding→Role/ClusterRole (shown on every Workloads view), NetworkPolicy→Pod podSelector matching plus its ingress/egress peer rules resolved against other pods already in view, using each pod's real namespace labels when available (shown on every Workloads view, and as the primary relationship on the Policies view), Namespace→ResourceQuota/LimitRange (shown on the Namespaces view), HorizontalPodAutoscaler→Deployment/StatefulSet scaling targets (shown on the Deployments and StatefulSets views), and PodDisruptionBudget→Pod podSelector matching, with disruptionsAllowed hitting zero surfaced as a degraded health signal (shown on every Workloads view)
- Node and Pod tiles show live CPU/memory usage when the cluster has metrics-server installed, fetched from the Metrics API — Node tiles also show each value as a percentage of the node's allocatable capacity (e.g. `768m (4%) / 13147Mi (41%)`), matching `kubectl top node`'s own columns; Pod tiles show plain usage only (e.g. `50m / 1687Mi`), matching `kubectl top pod`
- Node tiles also show allocatable ephemeral-storage capacity (e.g. `Disk: 432Gi`) and flag any active DiskPressure/MemoryPressure/PIDPressure condition as a degraded health signal — a node can stay "Ready" while the kubelet is already evicting pods or refusing new ones under pressure, which would otherwise be invisible
- Right-click any node for a context menu of actions, opening a resizable detail pane:
  - **Get events** — a `kubectl describe`-style table of recent Events involving the resource, fetched fresh from the cluster
  - **Get \<kind\>** — the resource's full YAML, fetched fresh from the cluster, with Copy and Download buttons above the view; Role/ClusterRole render their `rules` as a table instead, and ConfigMaps/Secrets offer a Tree/Table toggle to switch between the full YAML and a flat key/value table of just their `data` (Secret values shown base64-decoded)
  - **Get pod description** (Pods only) — a `kubectl describe`-style summary (containers, conditions, volumes, and recent Events), built entirely via the Kubernetes API
  - **Get pod logs** (Pods only) — the last 100 log lines for the pod's first container, with a dropdown at the top of the pane to switch to any other container, plus a text filter that narrows to matching lines and highlights the matched text
  - **Execute command** (Pods only) — a `kubectl exec`-style terminal: pick a container from the dropdown, type a command (a one-shot command like `date`, or an interactive one like `sh`/`bash`), and press Run to stream it live in a real terminal (powered by xterm.js) over a WebSocket to the Kubernetes exec API, with full TTY resize support
  - **Scan for vulnerabilities** (Pods only) — pick a container from the dropdown and press Scan to check its image for known CVEs via [Trivy](https://github.com/aquasecurity/trivy), showing severity counts and a findings list (CVE ID, package, installed/fixed version); requires `trivy` on `PATH` (see Configuration) and currently only works for publicly pullable images. A "Show command" checkbox (off by default) reveals the exact Trivy command that was run, pinned to the bottom of the pane
  - **Scan with Kubescape** (DaemonSets, Deployments, StatefulSets, CronJobs, and Jobs only — not Pods, since [Kubescape](https://github.com/kubescape/kubescape) itself refuses to scan a Pod that has an owner) — press Scan to check the resource's configuration against Kubescape's compliance controls (resource limits, non-root, privilege escalation, network policy, etc.), showing a compliance score, severity counts, and a list of failed controls; requires `kubescape` on `PATH` (see Configuration). Same "Show command" toggle as vulnerability scanning, above
- "Save as image" toolbar button exports the current graph view (including every card's icon, title, and badges, not just the bare edges) as a PNG, named after the resource type, namespace, and timestamp
- Filter box in the toolbar narrows the graph to resources matching the typed name, keeping matches' immediate neighbors visible for context and tightening the remaining layout into the freed space
- Collapsible legend (collapsed by default) in the graph pane's bottom-left corner lists every resource kind actually present in the current view with a checkbox to show/hide it, composing with the name filter above (a hidden kind stays hidden even if it's a filter match's neighbor) and re-tightening the layout into the freed space
- Light/dark theme toggle in the topbar, persisted across reloads
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

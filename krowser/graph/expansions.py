from dataclasses import dataclass


@dataclass(frozen=True)
class GraphExpansion:
    include: tuple[str, ...]  # extra Kinds to fetch alongside the root type's own kind


# Extra kinds every Workloads-group view also pulls in, so a workload's graph
# shows what it uses/is exposed by, not just its ownership chain.
WORKLOAD_RELATED_KINDS = (
    "Service", "ConfigMap", "Secret", "PersistentVolumeClaim", "PersistentVolume", "EndpointSlice"
)

# Keyed by left-pane resource-type id (krowser.k8s.resource_types.RESOURCE_TYPES).
GRAPH_EXPANSIONS: dict[str, GraphExpansion] = {
    "configmaps": GraphExpansion(()),
    "network/ingresses": GraphExpansion(("Service", "Pod", "EndpointSlice")),
    "network/services": GraphExpansion(("Pod", "EndpointSlice")),
    "secrets": GraphExpansion(()),
    "storage/persistentvolumes": GraphExpansion(("PersistentVolumeClaim",)),
    "storage/persistentvolumeclaims": GraphExpansion(("PersistentVolume", "Pod")),
    "workloads/daemonsets": GraphExpansion(("Pod",) + WORKLOAD_RELATED_KINDS),
    "workloads/deployments": GraphExpansion(("ReplicaSet", "Pod") + WORKLOAD_RELATED_KINDS),
    "workloads/statefulsets": GraphExpansion(("Pod",) + WORKLOAD_RELATED_KINDS),
    "workloads/cronjobs": GraphExpansion(("Job", "Pod") + WORKLOAD_RELATED_KINDS),
    "workloads/jobs": GraphExpansion(("Pod",) + WORKLOAD_RELATED_KINDS),
    "workloads/pods": GraphExpansion(WORKLOAD_RELATED_KINDS),
}

from dataclasses import dataclass


@dataclass(frozen=True)
class GraphExpansion:
    include: tuple[str, ...]  # extra Kinds to fetch alongside the root type's own kind


# Extra kinds every Workloads-group view also pulls in, so a workload's graph
# shows what it uses, not just its ownership chain. Service/EndpointSlice are
# deliberately excluded here (unlike the Ingresses expansion below) -- only
# Ingress routing shows the Service/EndpointSlice hop.
WORKLOAD_RELATED_KINDS = ("ConfigMap", "Secret", "PersistentVolumeClaim", "PersistentVolume")

# Keyed by left-pane resource-type id (krowser.k8s.resource_types.RESOURCE_TYPES).
GRAPH_EXPANSIONS: dict[str, GraphExpansion] = {
    "cluster/nodes": GraphExpansion(("Pod",)),
    "configmaps": GraphExpansion(()),
    "network/ingresses": GraphExpansion(("Service", "Pod", "EndpointSlice")),
    "network/services": GraphExpansion(("EndpointSlice", "Pod")),
    "secrets": GraphExpansion(()),
    "storage/persistentvolumes": GraphExpansion(("PersistentVolumeClaim",)),
    "storage/persistentvolumeclaims": GraphExpansion(("PersistentVolume", "Pod")),
    "workloads/daemonsets": GraphExpansion(("Pod",) + WORKLOAD_RELATED_KINDS),
    "workloads/deployments": GraphExpansion(("ReplicaSet", "Pod") + WORKLOAD_RELATED_KINDS),
    "workloads/statefulsets": GraphExpansion(("Pod",) + WORKLOAD_RELATED_KINDS),
    "workloads/cronjobs": GraphExpansion(("Job", "Pod") + WORKLOAD_RELATED_KINDS),
    "workloads/jobs": GraphExpansion(("Pod",) + WORKLOAD_RELATED_KINDS),
    "workloads/pods": GraphExpansion(WORKLOAD_RELATED_KINDS),
    # Dynamic customresources/<crd-name> ids have no static entry here --
    # GraphBuilder.build() falls back to an empty GraphExpansion for those.
}

from dataclasses import dataclass


@dataclass(frozen=True)
class ResourceTypeSpec:
    id: str
    label: str
    group: str | None
    icon: str
    namespaced: bool
    kind: str


# Order here is the exact left-pane display order from the spec. Consecutive
# entries sharing a `group` render under one group header on the frontend;
# entries with `group=None` render as flat top-level rows.
RESOURCE_TYPES: list[ResourceTypeSpec] = [
    ResourceTypeSpec("cluster/nodes", "Nodes", "Cluster", "node", False, "Node"),
    ResourceTypeSpec(
        "cluster/crds", "CustomResourceDefinitions", "Cluster", "crd", False, "CustomResourceDefinition"
    ),
    ResourceTypeSpec("configmaps", "ConfigMaps", "Config", "configmap", True, "ConfigMap"),
    ResourceTypeSpec("secrets", "Secrets", "Config", "secret", True, "Secret"),
    ResourceTypeSpec("network/ingresses", "Ingresses", "Network", "ingress", True, "Ingress"),
    ResourceTypeSpec("network/services", "Services", "Network", "service", True, "Service"),
    ResourceTypeSpec("storage/persistentvolumes", "PersistentVolumes", "Storage", "pv", False, "PersistentVolume"),
    ResourceTypeSpec("storage/persistentvolumeclaims", "PersistentVolumeClaims", "Storage", "pvc", True, "PersistentVolumeClaim"),
    ResourceTypeSpec("workloads/daemonsets", "DaemonSets", "Workloads", "daemonset", True, "DaemonSet"),
    ResourceTypeSpec("workloads/deployments", "Deployments", "Workloads", "deployment", True, "Deployment"),
    ResourceTypeSpec("workloads/statefulsets", "StatefulSets", "Workloads", "statefulset", True, "StatefulSet"),
    ResourceTypeSpec("workloads/cronjobs", "CronJobs", "Workloads", "cronjob", True, "CronJob"),
    ResourceTypeSpec("workloads/jobs", "Jobs", "Workloads", "job", True, "Job"),
    ResourceTypeSpec("workloads/pods", "Pods", "Workloads", "pod", True, "Pod"),
]

_BY_ID = {rt.id: rt for rt in RESOURCE_TYPES}

# Icon lookup by Kind name, for graph nodes of kinds that appear only as
# intermediate hops in the graph (e.g. ReplicaSet has no left-pane row).
ICONS_BY_KIND: dict[str, str] = {rt.kind: rt.icon for rt in RESOURCE_TYPES}
ICONS_BY_KIND.setdefault("ReplicaSet", "replicaset")
ICONS_BY_KIND.setdefault("EndpointSlice", "endpointslice")


class UnknownResourceTypeError(KeyError):
    pass


def get_resource_type(type_id: str) -> ResourceTypeSpec:
    try:
        return _BY_ID[type_id]
    except KeyError:
        raise UnknownResourceTypeError(type_id) from None

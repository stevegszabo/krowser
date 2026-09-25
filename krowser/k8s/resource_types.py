from dataclasses import dataclass


@dataclass(frozen=True)
class ResourceTypeSpec:
    id: str
    label: str
    group: str | None
    icon: str
    namespaced: bool
    kind: str


# Not a real Kubernetes Kind -- this id is special-cased in /api/graph to
# aggregate unhealthy resources across every other type instead of going
# through the normal single-kind GraphBuilder.build() path (see
# krowser/graph/problems.py). Kept in RESOURCE_TYPES (rather than bolted on
# only in the frontend) so it participates in namespace/context selection and
# the left-pane menu exactly like every other type.
PROBLEMS_TYPE_ID = "cluster/problems"

# A namespace filter is a genuine narrowing here (fetch_root matches the
# Namespace by name -- see krowser/graph/builder.py), unlike most other
# cluster-scoped types where it's a no-op, hence namespaced=True.
NAMESPACES_TYPE_ID = "cluster/namespaces"

NETWORK_POLICIES_TYPE_ID = "network/policies"

# Order here is the exact left-pane display order from the spec. Consecutive
# entries sharing a `group` render under one group header on the frontend;
# entries with `group=None` render as flat top-level rows.
RESOURCE_TYPES: list[ResourceTypeSpec] = [
    ResourceTypeSpec("cluster/nodes", "Nodes", "Cluster", "node", False, "Node"),
    ResourceTypeSpec(PROBLEMS_TYPE_ID, "Problems", "Cluster", "problem", True, "Problem"),
    ResourceTypeSpec(NAMESPACES_TYPE_ID, "Namespaces", "Cluster", "namespace", True, "Namespace"),
    ResourceTypeSpec("configmaps", "ConfigMaps", "Config", "configmap", True, "ConfigMap"),
    ResourceTypeSpec("secrets", "Secrets", "Config", "secret", True, "Secret"),
    ResourceTypeSpec("network/ingresses", "Ingresses", "Network", "ingress", True, "Ingress"),
    ResourceTypeSpec("network/services", "Services", "Network", "service", True, "Service"),
    ResourceTypeSpec(NETWORK_POLICIES_TYPE_ID, "Policies", "Network", "networkpolicy", True, "NetworkPolicy"),
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
ICONS_BY_KIND.setdefault("ServiceAccount", "serviceaccount")
ICONS_BY_KIND.setdefault("Role", "role")
ICONS_BY_KIND.setdefault("RoleBinding", "rolebinding")
ICONS_BY_KIND.setdefault("ClusterRole", "clusterrole")
ICONS_BY_KIND.setdefault("ClusterRoleBinding", "clusterrolebinding")
ICONS_BY_KIND.setdefault("HorizontalPodAutoscaler", "hpa")
ICONS_BY_KIND.setdefault("PodDisruptionBudget", "poddisruptionbudget")
ICONS_BY_KIND.setdefault("VolumeAttachment", "volumeattachment")
ICONS_BY_KIND.setdefault("ResourceQuota", "resourcequota")
ICONS_BY_KIND.setdefault("LimitRange", "limitrange")


class UnknownResourceTypeError(KeyError):
    pass


def get_all_resource_types() -> list[ResourceTypeSpec]:
    return RESOURCE_TYPES


def get_resource_type(type_id: str) -> ResourceTypeSpec:
    if type_id in _BY_ID:
        return _BY_ID[type_id]
    raise UnknownResourceTypeError(type_id)

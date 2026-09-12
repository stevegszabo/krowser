from dataclasses import dataclass

from krowser.k8s.client import KubeClientManager
from krowser.k8s.fetchers import list_crds
from krowser.k8s.getters import get_crd


@dataclass(frozen=True)
class ResourceTypeSpec:
    id: str
    label: str
    group: str | None
    icon: str
    namespaced: bool
    kind: str
    # Set only for a dynamic, per-CRD resource type (see crd_resource_types
    # below): the group/version/plural needed to list/get its instances via
    # CustomObjectsApi, since they have no dedicated typed *Api client method.
    api_group: str | None = None
    version: str | None = None
    plural: str | None = None


# Order here is the exact left-pane display order from the spec. Consecutive
# entries sharing a `group` render under one group header on the frontend;
# entries with `group=None` render as flat top-level rows.
RESOURCE_TYPES: list[ResourceTypeSpec] = [
    ResourceTypeSpec("cluster/nodes", "Nodes", "Cluster", "node", False, "Node"),
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

CUSTOM_RESOURCE_ID_PREFIX = "customresources/"

_BY_ID = {rt.id: rt for rt in RESOURCE_TYPES}

# Icon lookup by Kind name, for graph nodes of kinds that appear only as
# intermediate hops in the graph (e.g. ReplicaSet has no left-pane row).
ICONS_BY_KIND: dict[str, str] = {rt.kind: rt.icon for rt in RESOURCE_TYPES}
ICONS_BY_KIND.setdefault("ReplicaSet", "replicaset")
ICONS_BY_KIND.setdefault("EndpointSlice", "endpointslice")


class UnknownResourceTypeError(KeyError):
    pass


def _crd_to_resource_type(crd) -> ResourceTypeSpec:
    # A CRD's own metadata.name is already "<plural>.<group>" (e.g.
    # "virtualmachines.kubevirt.io"), globally unique across the cluster --
    # reused directly as the label and as the dynamic resource-type id.
    name = crd.metadata.name
    versions = crd.spec.versions or []
    version = (
        next((v.name for v in versions if v.storage), None)
        or next((v.name for v in versions if v.served), None)
        or (versions[0].name if versions else "v1")
    )
    return ResourceTypeSpec(
        id=f"{CUSTOM_RESOURCE_ID_PREFIX}{name}",
        label=name,
        group="Custom Resources",
        icon="crd",
        namespaced=crd.spec.scope == "Namespaced",
        kind=crd.spec.names.kind,
        api_group=crd.spec.group,
        version=version,
        plural=crd.spec.names.plural,
    )


def get_all_resource_types(mgr: KubeClientManager, context: str | None) -> list[ResourceTypeSpec]:
    """The built-in types plus one dynamic entry per CRD installed in the
    cluster (grouped under "Custom Resources"), since which CRDs exist is
    cluster- and context-specific and can't be known statically."""
    crds = list_crds(mgr, context)
    dynamic = sorted((_crd_to_resource_type(crd) for crd in crds), key=lambda rt: rt.label)
    return RESOURCE_TYPES + dynamic


def get_resource_type(
    type_id: str, mgr: KubeClientManager | None = None, context: str | None = None
) -> ResourceTypeSpec:
    if type_id in _BY_ID:
        return _BY_ID[type_id]
    if type_id.startswith(CUSTOM_RESOURCE_ID_PREFIX) and mgr is not None:
        crd_name = type_id[len(CUSTOM_RESOURCE_ID_PREFIX) :]
        try:
            crd = get_crd(mgr, context, crd_name)
        except Exception:
            raise UnknownResourceTypeError(type_id) from None
        return _crd_to_resource_type(crd)
    raise UnknownResourceTypeError(type_id)

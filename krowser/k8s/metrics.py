import json
from decimal import Decimal
from typing import NamedTuple

from kubernetes.utils.quantity import parse_quantity

from krowser.k8s.client import KubeClientManager

_METRICS_GROUP = "metrics.k8s.io"
_METRICS_VERSION = "v1beta1"


class Usage(NamedTuple):
    cpu_cores: Decimal
    memory_bytes: Decimal


class VolumeUsage(NamedTuple):
    used_bytes: Decimal
    capacity_bytes: Decimal


def format_volume_usage(usage: VolumeUsage) -> str:
    used_gib = round(usage.used_bytes / (1024**3))
    capacity_gib = round(usage.capacity_bytes / (1024**3))
    text = f"{used_gib}Gi / {capacity_gib}Gi"
    if usage.capacity_bytes:
        text += f" ({int(usage.used_bytes / usage.capacity_bytes * 100)}%)"
    return text


def format_usage(usage: Usage) -> str:
    millicores = round(usage.cpu_cores * 1000)
    mebibytes = round(usage.memory_bytes / (1024**2))
    return f"{millicores}m / {mebibytes}Mi"


def format_node_usage(usage: Usage, allocatable: Usage) -> str:
    """Same as format_usage, but also shows each value as a percentage of the
    node's allocatable capacity -- matching `kubectl top node`'s own
    CPU(cores)/CPU(%)/MEMORY(bytes)/MEMORY(%) columns (percentages are
    against allocatable, not capacity, since that's what's actually
    available for scheduling). Truncated rather than rounded, matching
    kubectl's own integer-division percentage (confirmed against a live
    cluster: kubectl showed 40% where round() would give 41%)."""
    millicores = round(usage.cpu_cores * 1000)
    mebibytes = round(usage.memory_bytes / (1024**2))
    cpu_part = f"{millicores}m"
    if allocatable.cpu_cores:
        cpu_part += f" ({int(usage.cpu_cores / allocatable.cpu_cores * 100)}%)"
    mem_part = f"{mebibytes}Mi"
    if allocatable.memory_bytes:
        mem_part += f" ({int(usage.memory_bytes / allocatable.memory_bytes * 100)}%)"
    return f"{cpu_part} / {mem_part}"


def fetch_node_metrics(mgr: KubeClientManager, context: str | None) -> dict[str, Usage]:
    """Live per-node CPU/memory usage from the cluster's metrics-server, keyed
    by node name. This is a best-effort overlay on top of the Node objects
    already fetched via the core API, not a required field -- metrics-server
    may not be installed, the kubeconfig may lack RBAC access to
    metrics.k8s.io, or (in tests) the manager itself may be None, so any
    failure here is swallowed and simply means no usage badge is shown.
    """
    try:
        result = mgr.custom_objects_api(context).list_cluster_custom_object(
            group=_METRICS_GROUP, version=_METRICS_VERSION, plural="nodes"
        )
    except Exception:
        return {}

    return {
        item["metadata"]["name"]: Usage(
            cpu_cores=parse_quantity(item["usage"]["cpu"]),
            memory_bytes=parse_quantity(item["usage"]["memory"]),
        )
        for item in result.get("items", [])
    }


def fetch_pod_metrics(
    mgr: KubeClientManager, context: str | None, namespace: str | None
) -> dict[tuple[str, str], Usage]:
    """Live per-pod CPU/memory usage (summed across each pod's containers),
    keyed by (namespace, name). Same best-effort semantics as
    fetch_node_metrics -- see its docstring.
    """
    try:
        api = mgr.custom_objects_api(context)
        if namespace:
            result = api.list_namespaced_custom_object(
                group=_METRICS_GROUP, version=_METRICS_VERSION, namespace=namespace, plural="pods"
            )
        else:
            result = api.list_cluster_custom_object(
                group=_METRICS_GROUP, version=_METRICS_VERSION, plural="pods"
            )
    except Exception:
        return {}

    usage_by_pod = {}
    for item in result.get("items", []):
        cpu_total = Decimal(0)
        memory_total = Decimal(0)
        for container in item.get("containers", []):
            cpu_total += parse_quantity(container["usage"]["cpu"])
            memory_total += parse_quantity(container["usage"]["memory"])
        key = (item["metadata"]["namespace"], item["metadata"]["name"])
        usage_by_pod[key] = Usage(cpu_cores=cpu_total, memory_bytes=memory_total)
    return usage_by_pod


def _fetch_node_summaries(mgr: KubeClientManager, context: str | None) -> list[dict]:
    """Fetches every node's own kubelet Summary API response (reached via the
    apiserver's node proxy -- no direct network path to kubelets needed),
    tolerating individual node failures. Shared by fetch_pvc_usage and
    fetch_pod_ephemeral_storage_usage below, which read different fields out
    of the same response shape -- core kubelet functionality always
    available, not an optional install, unlike metrics-server.

    Same best-effort semantics as fetch_node_metrics: any failure (missing
    RBAC for the node proxy subresource is common, as is a single
    unreachable node) is swallowed, and one bad node doesn't lose every
    other node's data.
    """
    try:
        nodes = mgr.core_v1(context).list_node().items
    except Exception:
        return []

    summaries = []
    for node in nodes:
        try:
            # _preload_content=False -- the client's default deserialization
            # for this method's declared `str` return type mangles the
            # kubelet's real JSON response into a Python dict repr (single
            # quotes), which then fails to json.loads at all. Reading the raw
            # bytes ourselves and decoding directly sidesteps that entirely
            # (same trick fetchers.py's _list_endpoint_slices_tolerant uses
            # for a different generated-client quirk).
            response = mgr.core_v1(context).connect_get_node_proxy_with_path(
                node.metadata.name, "stats/summary", _preload_content=False
            )
            summaries.append(json.loads(response.data))
        except Exception:
            continue
    return summaries


def fetch_pvc_usage(mgr: KubeClientManager, context: str | None) -> dict[tuple[str, str], VolumeUsage]:
    """Live per-PVC used/capacity bytes, keyed by (namespace, name). See
    _fetch_node_summaries for why this reads the kubelet Summary API.

    Deliberately not called on every view PersistentVolumeClaim appears in
    (e.g. every Workloads view, via WORKLOAD_RELATED_KINDS) -- unlike the
    metrics-server-backed fetches above (one call total), this is one real
    HTTP call per node in the cluster, so GraphBuilder only calls this for
    the two storage-focused views themselves (see builder.py).
    """
    usage_by_pvc: dict[tuple[str, str], VolumeUsage] = {}
    for summary in _fetch_node_summaries(mgr, context):
        for pod in summary.get("pods", []):
            for volume in pod.get("volume", []):
                pvc_ref = volume.get("pvcRef")
                if not pvc_ref or "usedBytes" not in volume or "capacityBytes" not in volume:
                    continue
                key = (pvc_ref["namespace"], pvc_ref["name"])
                usage_by_pvc[key] = VolumeUsage(
                    used_bytes=Decimal(volume["usedBytes"]),
                    capacity_bytes=Decimal(volume["capacityBytes"]),
                )
    return usage_by_pvc


def fetch_pod_ephemeral_storage_usage(mgr: KubeClientManager, context: str | None) -> dict[tuple[str, str], Decimal]:
    """Live per-pod ephemeral-storage used bytes (writable container layer +
    logs + emptyDir combined -- exactly what the kubelet itself sums to
    decide whether to evict a pod under DiskPressure), keyed by
    (namespace, name). See _fetch_node_summaries for why this reads the
    kubelet Summary API; same one-call-per-node cost and scoping concern as
    fetch_pvc_usage -- GraphBuilder only calls this for the dedicated Pods
    view, not every Workloads view Pod appears in.
    """
    usage_by_pod: dict[tuple[str, str], Decimal] = {}
    for summary in _fetch_node_summaries(mgr, context):
        for pod in summary.get("pods", []):
            pod_ref = pod.get("podRef")
            ephemeral = pod.get("ephemeral-storage")
            if not pod_ref or not ephemeral or "usedBytes" not in ephemeral:
                continue
            key = (pod_ref["namespace"], pod_ref["name"])
            usage_by_pod[key] = Decimal(ephemeral["usedBytes"])
    return usage_by_pod

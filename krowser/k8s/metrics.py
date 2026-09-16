from decimal import Decimal
from typing import NamedTuple

from kubernetes.utils.quantity import parse_quantity

from krowser.k8s.client import KubeClientManager

_METRICS_GROUP = "metrics.k8s.io"
_METRICS_VERSION = "v1beta1"


class Usage(NamedTuple):
    cpu_cores: Decimal
    memory_bytes: Decimal


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

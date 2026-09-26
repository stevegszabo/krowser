import json
from decimal import Decimal

from kubernetes.utils.quantity import parse_quantity

from krowser.k8s.metrics import (
    Usage,
    VolumeUsage,
    fetch_node_metrics,
    fetch_pod_ephemeral_storage_usage,
    fetch_pod_metrics,
    fetch_pvc_usage,
    format_node_usage,
    format_usage,
    format_volume_usage,
)


def test_format_usage_renders_millicores_and_mebibytes():
    usage = Usage(cpu_cores=Decimal("1.234"), memory_bytes=Decimal(300 * 1024**2))
    assert format_usage(usage) == "1234m / 300Mi"


def test_format_usage_rounds_to_nearest_unit():
    usage = Usage(cpu_cores=Decimal("0.0002995"), memory_bytes=Decimal(52953190))
    assert format_usage(usage) == "0m / 51Mi"


def test_format_node_usage_includes_percentage_of_allocatable():
    usage = Usage(cpu_cores=Decimal("0.686"), memory_bytes=Decimal(12959 * 1024**2))
    allocatable = Usage(cpu_cores=Decimal("16"), memory_bytes=Decimal(32705744 * 1024))

    assert format_node_usage(usage, allocatable) == "686m (4%) / 12959Mi (40%)"


def test_format_node_usage_omits_percentage_when_allocatable_is_zero():
    usage = Usage(cpu_cores=Decimal("0.5"), memory_bytes=Decimal(100 * 1024**2))
    allocatable = Usage(cpu_cores=Decimal("0"), memory_bytes=Decimal("0"))

    assert format_node_usage(usage, allocatable) == "500m / 100Mi"


def test_format_volume_usage_includes_percentage():
    usage = VolumeUsage(used_bytes=Decimal(50 * 1024**3), capacity_bytes=Decimal(100 * 1024**3))
    assert format_volume_usage(usage) == "50Gi / 100Gi (50%)"


def test_format_volume_usage_omits_percentage_when_capacity_is_zero():
    usage = VolumeUsage(used_bytes=Decimal(0), capacity_bytes=Decimal(0))
    assert format_volume_usage(usage) == "0Gi / 0Gi"


class _FakeCustomObjectsApi:
    def __init__(self, node_items=(), pod_items=(), error=None):
        self._node_items = list(node_items)
        self._pod_items = list(pod_items)
        self._error = error

    def list_cluster_custom_object(self, group, version, plural):
        if self._error:
            raise self._error
        items = self._node_items if plural == "nodes" else self._pod_items
        return {"items": items}

    def list_namespaced_custom_object(self, group, version, namespace, plural):
        if self._error:
            raise self._error
        items = [i for i in self._pod_items if i["metadata"]["namespace"] == namespace]
        return {"items": items}


class _FakeNode:
    def __init__(self, name):
        self.metadata = type("_Meta", (), {"name": name})()


class _FakeCoreV1Api:
    def __init__(self, node_names=(), summaries=None, list_error=None, proxy_errors=None):
        self._node_names = list(node_names)
        self._summaries = summaries or {}
        self._list_error = list_error
        self._proxy_errors = proxy_errors or {}

    def list_node(self):
        if self._list_error:
            raise self._list_error
        return type("_List", (), {"items": [_FakeNode(n) for n in self._node_names]})()

    def connect_get_node_proxy_with_path(self, name, path, _preload_content=True):
        if name in self._proxy_errors:
            raise self._proxy_errors[name]
        body = json.dumps(self._summaries.get(name, {"pods": []})).encode()
        return type("_Response", (), {"data": body})()


class _FakeManager:
    def __init__(self, api=None, core_v1_api=None):
        self._api = api
        self._core_v1_api = core_v1_api

    def custom_objects_api(self, context):
        return self._api

    def core_v1(self, context):
        return self._core_v1_api


def _node_item(name, cpu, memory):
    return {"metadata": {"name": name}, "usage": {"cpu": cpu, "memory": memory}}


def _pod_item(namespace, name, containers):
    return {
        "metadata": {"namespace": namespace, "name": name},
        "containers": [{"name": cname, "usage": {"cpu": cpu, "memory": mem}} for cname, cpu, mem in containers],
    }


def test_fetch_node_metrics_parses_quantities():
    api = _FakeCustomObjectsApi(node_items=[_node_item("monster", "791241454n", "12897780Ki")])
    mgr = _FakeManager(api)

    result = fetch_node_metrics(mgr, None)

    assert result["monster"].cpu_cores == Decimal("0.791241454")
    assert result["monster"].memory_bytes == Decimal("12897780") * 1024


def test_fetch_pod_metrics_sums_containers_and_uses_namespace_filter():
    api = _FakeCustomObjectsApi(
        pod_items=[
            _pod_item("ns", "web-1", [("app", "299514n", "49080Ki"), ("sidecar", "1000n", "1024Ki")]),
            _pod_item("other-ns", "web-2", [("app", "500n", "2048Ki")]),
        ]
    )
    mgr = _FakeManager(api)

    result = fetch_pod_metrics(mgr, None, "ns")

    assert set(result.keys()) == {("ns", "web-1")}
    usage = result[("ns", "web-1")]
    assert usage.cpu_cores == parse_quantity("299514n") + parse_quantity("1000n")
    assert usage.memory_bytes == parse_quantity("49080Ki") + parse_quantity("1024Ki")


def test_fetch_pod_metrics_all_namespaces_when_namespace_falsy():
    api = _FakeCustomObjectsApi(pod_items=[_pod_item("ns", "web-1", [("app", "1n", "1Ki")])])
    mgr = _FakeManager(api)

    result = fetch_pod_metrics(mgr, None, "")

    assert set(result.keys()) == {("ns", "web-1")}


def test_fetch_node_metrics_returns_empty_on_error():
    api = _FakeCustomObjectsApi(error=RuntimeError("metrics-server not installed"))
    mgr = _FakeManager(api)

    assert fetch_node_metrics(mgr, None) == {}


def test_fetch_pod_metrics_returns_empty_on_error():
    api = _FakeCustomObjectsApi(error=RuntimeError("metrics-server not installed"))
    mgr = _FakeManager(api)

    assert fetch_pod_metrics(mgr, None, "ns") == {}


def test_fetch_metrics_returns_empty_when_manager_is_none():
    # Mirrors the many GraphBuilder(mgr=None) test call sites elsewhere --
    # metrics must degrade silently rather than raise AttributeError.
    assert fetch_node_metrics(None, None) == {}
    assert fetch_pod_metrics(None, None, "ns") == {}


def _summary(pods):
    return {"pods": pods}


def _summary_pod(name, namespace, volumes):
    return {"podRef": {"name": name, "namespace": namespace}, "volume": volumes}


def _pvc_volume(name, namespace, used_bytes, capacity_bytes):
    return {"pvcRef": {"name": name, "namespace": namespace}, "usedBytes": used_bytes, "capacityBytes": capacity_bytes}


def test_fetch_pvc_usage_reads_from_node_proxy_stats_summary():
    summary = _summary([_summary_pod("vault-0", "base-vault", [_pvc_volume("data-vault-0", "base-vault", 50, 100)])])
    core_v1 = _FakeCoreV1Api(node_names=["node-1"], summaries={"node-1": summary})
    mgr = _FakeManager(core_v1_api=core_v1)

    result = fetch_pvc_usage(mgr, None)

    assert result == {("base-vault", "data-vault-0"): VolumeUsage(used_bytes=Decimal(50), capacity_bytes=Decimal(100))}


def test_fetch_pvc_usage_ignores_volumes_without_pvc_ref():
    # emptyDir/configMap/etc. volumes report usage too but have no pvcRef --
    # only volumes actually backed by a PVC are relevant here.
    summary = _summary(
        [_summary_pod("pod-1", "ns", [{"name": "cache", "usedBytes": 10, "capacityBytes": 100}])]
    )
    core_v1 = _FakeCoreV1Api(node_names=["node-1"], summaries={"node-1": summary})
    mgr = _FakeManager(core_v1_api=core_v1)

    assert fetch_pvc_usage(mgr, None) == {}


def test_fetch_pvc_usage_merges_across_multiple_nodes():
    summary_1 = _summary([_summary_pod("pod-1", "ns", [_pvc_volume("data-1", "ns", 10, 100)])])
    summary_2 = _summary([_summary_pod("pod-2", "ns", [_pvc_volume("data-2", "ns", 20, 200)])])
    core_v1 = _FakeCoreV1Api(
        node_names=["node-1", "node-2"], summaries={"node-1": summary_1, "node-2": summary_2}
    )
    mgr = _FakeManager(core_v1_api=core_v1)

    result = fetch_pvc_usage(mgr, None)

    assert set(result.keys()) == {("ns", "data-1"), ("ns", "data-2")}


def test_fetch_pvc_usage_skips_unreachable_node_but_keeps_others():
    summary_2 = _summary([_summary_pod("pod-2", "ns", [_pvc_volume("data-2", "ns", 20, 200)])])
    core_v1 = _FakeCoreV1Api(
        node_names=["node-1", "node-2"],
        summaries={"node-2": summary_2},
        proxy_errors={"node-1": RuntimeError("kubelet unreachable")},
    )
    mgr = _FakeManager(core_v1_api=core_v1)

    result = fetch_pvc_usage(mgr, None)

    assert set(result.keys()) == {("ns", "data-2")}


def test_fetch_pvc_usage_returns_empty_when_list_node_fails():
    core_v1 = _FakeCoreV1Api(list_error=RuntimeError("forbidden"))
    mgr = _FakeManager(core_v1_api=core_v1)

    assert fetch_pvc_usage(mgr, None) == {}


def test_fetch_pvc_usage_returns_empty_when_manager_is_none():
    assert fetch_pvc_usage(None, None) == {}


def test_fetch_pod_ephemeral_storage_usage_reads_from_node_proxy_stats_summary():
    summary = _summary(
        [
            {
                "podRef": {"name": "web-1", "namespace": "ns"},
                "ephemeral-storage": {"usedBytes": 12345, "capacityBytes": 999999},
            }
        ]
    )
    core_v1 = _FakeCoreV1Api(node_names=["node-1"], summaries={"node-1": summary})
    mgr = _FakeManager(core_v1_api=core_v1)

    result = fetch_pod_ephemeral_storage_usage(mgr, None)

    assert result == {("ns", "web-1"): Decimal(12345)}


def test_fetch_pod_ephemeral_storage_usage_ignores_pods_without_the_field():
    summary = _summary([{"podRef": {"name": "web-1", "namespace": "ns"}}])
    core_v1 = _FakeCoreV1Api(node_names=["node-1"], summaries={"node-1": summary})
    mgr = _FakeManager(core_v1_api=core_v1)

    assert fetch_pod_ephemeral_storage_usage(mgr, None) == {}


def test_fetch_pod_ephemeral_storage_usage_merges_across_multiple_nodes():
    summary_1 = _summary(
        [{"podRef": {"name": "web-1", "namespace": "ns"}, "ephemeral-storage": {"usedBytes": 1}}]
    )
    summary_2 = _summary(
        [{"podRef": {"name": "web-2", "namespace": "ns"}, "ephemeral-storage": {"usedBytes": 2}}]
    )
    core_v1 = _FakeCoreV1Api(
        node_names=["node-1", "node-2"], summaries={"node-1": summary_1, "node-2": summary_2}
    )
    mgr = _FakeManager(core_v1_api=core_v1)

    result = fetch_pod_ephemeral_storage_usage(mgr, None)

    assert set(result.keys()) == {("ns", "web-1"), ("ns", "web-2")}


def test_fetch_pod_ephemeral_storage_usage_returns_empty_when_manager_is_none():
    assert fetch_pod_ephemeral_storage_usage(None, None) == {}

from decimal import Decimal

from kubernetes.utils.quantity import parse_quantity

from krowser.k8s.metrics import (
    Usage,
    fetch_node_metrics,
    fetch_pod_metrics,
    format_node_usage,
    format_usage,
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


class _FakeManager:
    def __init__(self, api):
        self._api = api

    def custom_objects_api(self, context):
        return self._api


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

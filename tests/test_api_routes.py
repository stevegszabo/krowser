import pytest
from fastapi.testclient import TestClient
from kubernetes import client as k8s
from kubernetes.client import ApiClient
from kubernetes.client.rest import ApiException

import krowser.api.routes_resource as routes_resource_module
from krowser.api.deps import get_kube_client_manager
from krowser.k8s.client import ContextInfo
from krowser.k8s.fetchers import ResourceAccessError
from krowser.main import app


class FakeManager:
    def __init__(self, crds=(), custom_objects=None):
        self._crds = list(crds)
        # {(group, version, plural): {namespace_or_None: [item, ...]}}
        self._custom_objects = custom_objects or {}

    def list_contexts(self):
        return [ContextInfo(name="test-ctx", cluster="test-cluster", is_current=True)]

    def list_namespaces(self, context):
        return ["default", "kube-system"]

    def api_client_for(self, context):
        return ApiClient()

    def apiextensions_v1(self, context):
        crds = self._crds

        class _Api:
            def list_custom_resource_definition(self):
                return type("_List", (), {"items": crds})()

            def read_custom_resource_definition(self, name):
                for crd in crds:
                    if crd.metadata.name == name:
                        return crd
                raise ApiException(status=404, reason="Not Found")

        return _Api()

    def custom_objects_api(self, context):
        objects_by_key = self._custom_objects

        class _Api:
            def list_namespaced_custom_object(self, group, version, namespace, plural):
                items = objects_by_key.get((group, version, plural), {}).get(namespace, [])
                return {"items": items}

            def list_cluster_custom_object(self, group, version, plural):
                by_ns = objects_by_key.get((group, version, plural), {})
                items = [item for items in by_ns.values() for item in items]
                return {"items": items}

            def get_namespaced_custom_object(self, group, version, namespace, plural, name):
                for item in objects_by_key.get((group, version, plural), {}).get(namespace, []):
                    if item["metadata"]["name"] == name:
                        return item
                raise ApiException(status=404, reason="Not Found")

            def get_cluster_custom_object(self, group, version, plural, name):
                for items in objects_by_key.get((group, version, plural), {}).values():
                    for item in items:
                        if item["metadata"]["name"] == name:
                            return item
                raise ApiException(status=404, reason="Not Found")

        return _Api()


@pytest.fixture
def client():
    app.dependency_overrides[get_kube_client_manager] = lambda: FakeManager()
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def make_client():
    def _make(mgr):
        app.dependency_overrides[get_kube_client_manager] = lambda: mgr
        return TestClient(app)

    yield _make
    app.dependency_overrides.clear()


def _make_crd(name, group, plural, kind, scope="Namespaced"):
    return k8s.V1CustomResourceDefinition(
        metadata=k8s.V1ObjectMeta(name=name),
        spec=k8s.V1CustomResourceDefinitionSpec(
            group=group,
            scope=scope,
            names=k8s.V1CustomResourceDefinitionNames(kind=kind, plural=plural),
            versions=[k8s.V1CustomResourceDefinitionVersion(name="v1", served=True, storage=True)],
        ),
    )


def test_contexts_endpoint(client):
    res = client.get("/api/contexts")
    assert res.status_code == 200
    body = res.json()
    assert body["current"] == "test-ctx"
    assert body["contexts"] == [{"name": "test-ctx", "cluster": "test-cluster", "is_current": True}]


def test_namespaces_endpoint(client):
    res = client.get("/api/namespaces")
    assert res.status_code == 200
    assert res.json() == {"namespaces": ["default", "kube-system"]}


def test_resource_types_endpoint_returns_fixed_order(client):
    res = client.get("/api/resource-types")
    assert res.status_code == 200
    ids = [rt["id"] for rt in res.json()["resource_types"]]
    assert ids[0] == "cluster/nodes"
    assert ids[-1] == "workloads/pods"


def test_resource_types_endpoint_includes_dynamic_crds(make_client):
    crd = _make_crd("widgets.example.com", "example.com", "widgets", "Widget")
    client = make_client(FakeManager(crds=[crd]))

    res = client.get("/api/resource-types")

    assert res.status_code == 200
    types = res.json()["resource_types"]
    assert types[-1] == {
        "id": "customresources/widgets.example.com",
        "label": "widgets.example.com",
        "group": "Custom Resources",
        "icon": "crd",
        "namespaced": True,
    }


def test_graph_unknown_type_is_404(client):
    res = client.get("/api/graph", params={"type": "bogus"})
    assert res.status_code == 404


def test_graph_lists_custom_resource_instances(make_client):
    crd = _make_crd("widgets.example.com", "example.com", "widgets", "Widget")
    widget = {
        "apiVersion": "example.com/v1",
        "kind": "Widget",
        "metadata": {
            "uid": "w-1",
            "name": "my-widget",
            "namespace": "default",
            "creationTimestamp": "2024-01-01T00:00:00Z",
        },
        "spec": {},
    }
    client = make_client(
        FakeManager(crds=[crd], custom_objects={("example.com", "v1", "widgets"): {"default": [widget]}})
    )

    res = client.get(
        "/api/graph", params={"type": "customresources/widgets.example.com", "namespace": "default"}
    )

    assert res.status_code == 200
    body = res.json()
    assert {n["id"] for n in body["nodes"]} == {"w-1"}
    node = body["nodes"][0]
    assert node["kind"] == "Widget"
    assert node["health"] == "unknown"
    assert node["api_group"] == "example.com"
    assert node["api_version"] == "v1"
    assert node["plural"] == "widgets"
    assert body["edges"] == []


def test_resource_yaml_for_custom_resource_instance(make_client):
    crd = _make_crd("widgets.example.com", "example.com", "widgets", "Widget")
    widget = {
        "apiVersion": "example.com/v1",
        "kind": "Widget",
        "metadata": {"name": "my-widget", "namespace": "default"},
        "spec": {"size": "large"},
    }
    client = make_client(
        FakeManager(crds=[crd], custom_objects={("example.com", "v1", "widgets"): {"default": [widget]}})
    )

    res = client.get(
        "/api/resource-yaml",
        params={
            "kind": "Widget",
            "name": "my-widget",
            "namespace": "default",
            "group": "example.com",
            "version": "v1",
            "plural": "widgets",
        },
    )

    assert res.status_code == 200
    assert "size: large" in res.json()["yaml"]


def test_resource_yaml_returns_yaml_text(client, monkeypatch, make_pod):
    pod = make_pod("pod-1", "web-abc", namespace="ns")
    pod.kind = "Pod"
    pod.api_version = "v1"
    monkeypatch.setitem(
        routes_resource_module.GETTERS_BY_KIND, "Pod", lambda mgr, context, namespace, name: pod
    )

    res = client.get("/api/resource-yaml", params={"kind": "Pod", "name": "web-abc", "namespace": "ns"})

    assert res.status_code == 200
    yaml_text = res.json()["yaml"]
    assert "kind: Pod" in yaml_text
    assert "name: web-abc" in yaml_text
    assert "namespace: ns" in yaml_text


def test_resource_yaml_returns_structured_data(client, monkeypatch, make_pod):
    pod = make_pod("pod-1", "web-abc", namespace="ns")
    pod.kind = "Pod"
    pod.api_version = "v1"
    monkeypatch.setitem(
        routes_resource_module.GETTERS_BY_KIND, "Pod", lambda mgr, context, namespace, name: pod
    )

    res = client.get("/api/resource-yaml", params={"kind": "Pod", "name": "web-abc", "namespace": "ns"})

    assert res.status_code == 200
    data = res.json()["data"]
    assert data["kind"] == "Pod"
    assert data["metadata"]["name"] == "web-abc"
    assert data["metadata"]["namespace"] == "ns"


def test_resource_yaml_unknown_kind_is_404(client):
    res = client.get("/api/resource-yaml", params={"kind": "Bogus", "name": "x"})
    assert res.status_code == 404


def test_resource_yaml_maps_resource_access_error(client, monkeypatch):
    def _raise(mgr, context, namespace, name):
        raise ResourceAccessError("Pod", 403, "Forbidden")

    monkeypatch.setitem(routes_resource_module.GETTERS_BY_KIND, "Pod", _raise)

    res = client.get("/api/resource-yaml", params={"kind": "Pod", "name": "x", "namespace": "ns"})

    assert res.status_code == 403


def test_pod_describe_returns_sections(client, monkeypatch, make_pod):
    pod = make_pod("pod-1", "web-abc", namespace="ns")
    monkeypatch.setitem(
        routes_resource_module.GETTERS_BY_KIND, "Pod", lambda mgr, context, namespace, name: pod
    )
    monkeypatch.setattr(routes_resource_module, "get_pod_events", lambda mgr, context, namespace, name: [])

    res = client.get("/api/pod-describe", params={"name": "web-abc", "namespace": "ns"})

    assert res.status_code == 200
    sections = res.json()["sections"]
    titles = [s["title"] for s in sections]
    assert titles == ["Details", "Containers", "Conditions", "Volumes", "Events"]
    details = next(s["text"] for s in sections if s["title"] == "Details")
    assert "Name:           web-abc" in details
    assert "Namespace:      ns" in details
    events = next(s["text"] for s in sections if s["title"] == "Events")
    assert events == "<none>"


def test_pod_describe_maps_resource_access_error(client, monkeypatch):
    def _raise(mgr, context, namespace, name):
        raise ResourceAccessError("Pod", 403, "Forbidden")

    monkeypatch.setitem(routes_resource_module.GETTERS_BY_KIND, "Pod", _raise)

    res = client.get("/api/pod-describe", params={"name": "x", "namespace": "ns"})

    assert res.status_code == 403


def test_pod_logs_returns_text(client, monkeypatch):
    monkeypatch.setattr(
        routes_resource_module,
        "get_pod_logs",
        lambda mgr, context, namespace, name, container, tail_lines=100: "line one\nline two\n",
    )

    res = client.get(
        "/api/pod-logs", params={"name": "web-abc", "namespace": "ns", "container": "app"}
    )

    assert res.status_code == 200
    assert res.json()["logs"] == "line one\nline two\n"


def test_pod_logs_maps_resource_access_error(client, monkeypatch):
    def _raise(mgr, context, namespace, name, container, tail_lines=100):
        raise ResourceAccessError("Pod", 404, "Not Found")

    monkeypatch.setattr(routes_resource_module, "get_pod_logs", _raise)

    res = client.get("/api/pod-logs", params={"name": "x", "namespace": "ns", "container": "app"})

    assert res.status_code == 404

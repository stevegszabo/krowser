import pytest
from fastapi.testclient import TestClient
from kubernetes.client import ApiClient

import krowser.api.routes_resource as routes_resource_module
from krowser.api.deps import get_kube_client_manager
from krowser.k8s.client import ContextInfo
from krowser.k8s.fetchers import ResourceAccessError
from krowser.main import app


class FakeManager:
    def list_contexts(self):
        return [ContextInfo(name="test-ctx", cluster="test-cluster", is_current=True)]

    def list_namespaces(self, context):
        return ["default", "kube-system"]

    def api_client_for(self, context):
        return ApiClient()


@pytest.fixture
def client():
    app.dependency_overrides[get_kube_client_manager] = lambda: FakeManager()
    yield TestClient(app)
    app.dependency_overrides.clear()


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


def test_graph_unknown_type_is_404(client):
    res = client.get("/api/graph", params={"type": "bogus"})
    assert res.status_code == 404


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


def test_resource_yaml_unknown_kind_is_404(client):
    res = client.get("/api/resource-yaml", params={"kind": "Bogus", "name": "x"})
    assert res.status_code == 404


def test_resource_yaml_maps_resource_access_error(client, monkeypatch):
    def _raise(mgr, context, namespace, name):
        raise ResourceAccessError("Pod", 403, "Forbidden")

    monkeypatch.setitem(routes_resource_module.GETTERS_BY_KIND, "Pod", _raise)

    res = client.get("/api/resource-yaml", params={"kind": "Pod", "name": "x", "namespace": "ns"})

    assert res.status_code == 403

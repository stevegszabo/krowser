import pytest

from krowser.k8s.scale import SCALABLE_KINDS, get_replicas, scale_workload


class _FakeScale:
    def __init__(self, replicas):
        self.spec = type("_Spec", (), {"replicas": replicas})()


class _FakeAppsV1Api:
    def __init__(self, replicas=1):
        self.replicas = replicas
        self.patch_calls = []

    def read_namespaced_deployment_scale(self, name, namespace):
        return _FakeScale(self.replicas)

    def read_namespaced_stateful_set_scale(self, name, namespace):
        return _FakeScale(self.replicas)

    def patch_namespaced_deployment_scale(self, name, namespace, body):
        self.patch_calls.append((name, namespace, body))
        self.replicas = body["spec"]["replicas"]
        return _FakeScale(self.replicas)

    def patch_namespaced_stateful_set_scale(self, name, namespace, body):
        self.patch_calls.append((name, namespace, body))
        self.replicas = body["spec"]["replicas"]
        return _FakeScale(self.replicas)


class _FakeManager:
    def __init__(self, api):
        self._api = api

    def apps_v1(self, context):
        return self._api


def test_scalable_kinds_is_deployment_and_statefulset_only():
    assert set(SCALABLE_KINDS) == {"Deployment", "StatefulSet"}


@pytest.mark.parametrize("kind", ["Deployment", "StatefulSet"])
def test_get_replicas_reads_current_count(kind):
    api = _FakeAppsV1Api(replicas=3)
    mgr = _FakeManager(api)

    assert get_replicas(mgr, None, kind, "ns", "web") == 3


def test_get_replicas_treats_none_as_zero():
    api = _FakeAppsV1Api(replicas=None)
    mgr = _FakeManager(api)

    assert get_replicas(mgr, None, "Deployment", "ns", "web") == 0


@pytest.mark.parametrize("kind", ["Deployment", "StatefulSet"])
def test_scale_workload_patches_via_scale_subresource(kind):
    api = _FakeAppsV1Api(replicas=1)
    mgr = _FakeManager(api)

    result = scale_workload(mgr, None, kind, "ns", "web", 5)

    assert result == 5
    assert api.patch_calls == [("web", "ns", {"spec": {"replicas": 5}})]

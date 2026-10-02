import pytest

from krowser.k8s.restart import RESTARTABLE_KINDS, restart_workload


class _FakeAppsV1Api:
    def __init__(self):
        self.patch_calls = []

    def patch_namespaced_deployment(self, name, namespace, body):
        self.patch_calls.append((name, namespace, body))

    def patch_namespaced_stateful_set(self, name, namespace, body):
        self.patch_calls.append((name, namespace, body))

    def patch_namespaced_daemon_set(self, name, namespace, body):
        self.patch_calls.append((name, namespace, body))


class _FakeManager:
    def __init__(self, api):
        self._api = api

    def apps_v1(self, context):
        return self._api


def test_restartable_kinds_is_deployment_statefulset_and_daemonset():
    assert set(RESTARTABLE_KINDS) == {"Deployment", "StatefulSet", "DaemonSet"}


@pytest.mark.parametrize("kind", ["Deployment", "StatefulSet", "DaemonSet"])
def test_restart_workload_patches_pod_template_annotation(kind):
    api = _FakeAppsV1Api()
    mgr = _FakeManager(api)

    restart_workload(mgr, None, kind, "ns", "web")

    assert len(api.patch_calls) == 1
    name, namespace, body = api.patch_calls[0]
    assert (name, namespace) == ("web", "ns")
    annotations = body["spec"]["template"]["metadata"]["annotations"]
    assert "kubectl.kubernetes.io/restartedAt" in annotations

from krowser.k8s.pod_actions import terminate_pod


class _FakeCoreV1Api:
    def __init__(self):
        self.delete_calls = []

    def delete_namespaced_pod(self, name, namespace):
        self.delete_calls.append((name, namespace))


class _FakeManager:
    def __init__(self, api):
        self._api = api

    def core_v1(self, context):
        return self._api


def test_terminate_pod_deletes_via_core_v1():
    api = _FakeCoreV1Api()
    mgr = _FakeManager(api)

    terminate_pod(mgr, None, "ns", "web-1")

    assert api.delete_calls == [("web-1", "ns")]

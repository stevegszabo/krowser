import pytest
from fastapi.testclient import TestClient

import krowser.api.routes_log_stream as routes_log_stream_module
from krowser.api.deps import get_kube_client_manager
from krowser.k8s.pod_logs_stream import open_log_stream
from krowser.main import app
from tests.test_api_routes import FakeManager


class _FakeLogResponse:
    """Stands in for the raw urllib3.HTTPResponse `_preload_content=False`
    returns -- a lazily-readable stream, not something to call .data on."""

    def __init__(self, chunks=()):
        self._chunks = list(chunks)
        self.closed = False

    def stream(self, amt=4096, decode_content=True):
        yield from self._chunks

    def close(self):
        self.closed = True


class _FakeCoreV1Api:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def read_namespaced_pod_log(self, name, namespace, **kwargs):
        self.calls.append((name, namespace, kwargs))
        return self._response


@pytest.fixture
def client():
    app.dependency_overrides[get_kube_client_manager] = lambda: FakeManager()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_open_log_stream_follows_with_preload_disabled():
    response = _FakeLogResponse()
    api = _FakeCoreV1Api(response)

    result = open_log_stream(api, "ns", "web", "app")

    assert result is response
    assert api.calls == [
        ("web", "ns", {"container": "app", "follow": True, "tail_lines": 0, "_preload_content": False})
    ]


def test_pod_logs_stream_streams_chunks_then_exit(client, monkeypatch):
    fake = _FakeLogResponse([b"hello\n", b"world\n"])
    monkeypatch.setattr(routes_log_stream_module, "open_log_stream", lambda *a, **k: fake)

    with client.websocket_connect(
        "/api/pod-logs-stream?name=web&namespace=ns&container=app"
    ) as ws:
        assert ws.receive_json() == {"type": "stdout", "data": "hello\n"}
        assert ws.receive_json() == {"type": "stdout", "data": "world\n"}
        assert ws.receive_json() == {"type": "exit", "data": None}

    assert fake.closed


def test_pod_logs_stream_reports_open_failure(client, monkeypatch):
    def _raise(*a, **k):
        raise RuntimeError("pod not found")

    monkeypatch.setattr(routes_log_stream_module, "open_log_stream", _raise)

    with client.websocket_connect(
        "/api/pod-logs-stream?name=web&namespace=ns&container=app"
    ) as ws:
        assert ws.receive_json() == {"type": "error", "data": "pod not found"}

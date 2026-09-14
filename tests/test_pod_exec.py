import pytest
from fastapi.testclient import TestClient

import krowser.api.routes_exec as routes_exec_module
from krowser.api.deps import get_kube_client_manager
from krowser.main import app
from tests.test_api_routes import FakeManager


class FakeWSClient:
    """Stands in for kubernetes.stream.ws_client.WSClient: a queue of
    (channel, data) chunks drained one per update() call, exactly like the
    real client buffers one frame at a time off the socket."""

    def __init__(self, chunks=(), returncode=0):
        self._chunks = list(chunks)
        self._returncode = returncode
        self._open = True
        self._pending = {"stdout": None, "stderr": None}
        self.stdin_writes = []
        self.channel_writes = []
        self.closed = False

    def is_open(self):
        return self._open

    def update(self, timeout=0):
        if self._chunks:
            channel, data = self._chunks.pop(0)
            self._pending[channel] = data
        else:
            self._open = False

    def peek_stdout(self, timeout=0):
        return self._pending["stdout"]

    def read_stdout(self, timeout=0):
        data = self._pending["stdout"]
        self._pending["stdout"] = None
        return data

    def peek_stderr(self, timeout=0):
        return self._pending["stderr"]

    def read_stderr(self, timeout=0):
        data = self._pending["stderr"]
        self._pending["stderr"] = None
        return data

    def write_stdin(self, data):
        self.stdin_writes.append(data)

    def write_channel(self, channel, data):
        self.channel_writes.append((channel, data))

    def close(self, **kwargs):
        self._open = False
        self.closed = True

    @property
    def returncode(self):
        return self._returncode


@pytest.fixture
def client():
    app.dependency_overrides[get_kube_client_manager] = lambda: FakeManager()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_pod_exec_streams_stdout_then_exit(client, monkeypatch):
    fake = FakeWSClient(chunks=[("stdout", "hello\n")], returncode=0)
    monkeypatch.setattr(routes_exec_module, "open_exec_stream", lambda *a, **k: fake)

    with client.websocket_connect(
        "/api/pod-exec?name=web&namespace=ns&container=app&command=date"
    ) as ws:
        assert ws.receive_json() == {"type": "stdout", "data": "hello\n"}
        assert ws.receive_json() == {"type": "exit", "data": 0}

    assert fake.closed


def test_pod_exec_forwards_stdin_and_resize(client, monkeypatch):
    fake = FakeWSClient(chunks=[])
    monkeypatch.setattr(routes_exec_module, "open_exec_stream", lambda *a, **k: fake)

    with client.websocket_connect(
        "/api/pod-exec?name=web&namespace=ns&container=app&command=sh"
    ) as ws:
        ws.send_json({"type": "resize", "rows": 40, "cols": 100})
        ws.send_json({"type": "stdin", "data": "echo hi\n"})
        assert ws.receive_json() == {"type": "exit", "data": 0}

    assert fake.stdin_writes == ["echo hi\n"]
    assert len(fake.channel_writes) == 1
    channel, payload = fake.channel_writes[0]
    assert channel == 4  # RESIZE_CHANNEL
    assert "40" in payload and "100" in payload


def test_pod_exec_splits_command_string_into_argv(client, monkeypatch):
    captured = {}

    def fake_open(core_v1, namespace, name, container, command):
        captured["command"] = command
        return FakeWSClient(chunks=[])

    monkeypatch.setattr(routes_exec_module, "open_exec_stream", fake_open)

    with client.websocket_connect(
        "/api/pod-exec?name=web&namespace=ns&container=app&command=echo+hello+world"
    ) as ws:
        assert ws.receive_json() == {"type": "exit", "data": 0}

    assert captured["command"] == ["echo", "hello", "world"]


def test_pod_exec_reports_connection_error(client, monkeypatch):
    def fake_open(*a, **k):
        raise RuntimeError("pod not found")

    monkeypatch.setattr(routes_exec_module, "open_exec_stream", fake_open)

    with client.websocket_connect(
        "/api/pod-exec?name=web&namespace=ns&container=app&command=date"
    ) as ws:
        assert ws.receive_json() == {"type": "error", "data": "pod not found"}

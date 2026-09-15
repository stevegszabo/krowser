import json

from kubernetes.client import CoreV1Api
from kubernetes.stream import stream
from kubernetes.stream.ws_client import ERROR_CHANNEL, WSClient


def open_exec_stream(
    core_v1: CoreV1Api,
    namespace: str,
    name: str,
    container: str,
    command: list[str],
) -> WSClient:
    """Opens a live kubectl-exec-style stream to a container via the
    Kubernetes API's exec subresource (a WebSocket upgrade over the API
    server connection) -- never shells out to the kubectl binary.

    Blocks until the WebSocket handshake completes, so callers on an event
    loop should run this in a thread/executor.
    """
    return stream(
        core_v1.connect_get_namespaced_pod_exec,
        name,
        namespace,
        container=container,
        command=command,
        stderr=True,
        stdin=True,
        stdout=True,
        tty=True,
        _preload_content=False,
    )


def read_exec_result(ws_client: WSClient) -> tuple[int | None, str | None]:
    """Reads the final Status object Kubernetes sends on ERROR_CHANNEL when
    an exec session ends, returning (returncode, error_message).

    Mirrors WSClient.returncode's parsing, but never raises, and also
    surfaces the human-readable failure text (e.g. "executable file not
    found") for the case where exec itself never started a process --
    WSClient.returncode assumes the cause message is always a numeric exit
    code and raises ValueError otherwise, silently losing that text.
    """
    raw = ws_client.read_channel(ERROR_CHANNEL)
    if not raw:
        return None, None
    try:
        status = json.loads(raw)
    except ValueError:
        return None, raw

    if status.get("status") == "Success":
        return 0, None

    causes = (status.get("details") or {}).get("causes") or []
    cause_message = causes[0].get("message") if causes else None
    if cause_message and cause_message.lstrip("-").isdigit():
        return int(cause_message), None

    return None, cause_message or status.get("message") or "exec failed"

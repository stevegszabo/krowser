from kubernetes.client import CoreV1Api
from kubernetes.stream import stream
from kubernetes.stream.ws_client import WSClient


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

from kubernetes.client import CoreV1Api
from urllib3 import HTTPResponse


def open_log_stream(core_v1: CoreV1Api, namespace: str, name: str, container: str) -> HTTPResponse:
    """Opens a live `kubectl logs -f`-style stream via the Kubernetes API's
    pod log endpoint with follow=True.

    Unlike exec/attach, the pod log endpoint is a plain HTTP response using
    chunked transfer encoding, not a WebSocket -- so `_preload_content=False`
    is enough to get back the raw, lazily-read urllib3.HTTPResponse instead
    of the generated client's default behavior of blocking until the whole
    body (here, never, since the connection stays open) is read into memory.

    Blocks until the connection opens, so callers on an event loop should
    run this in a thread/executor.

    tail_lines=0 deliberately skips replaying any existing history -- the
    one-shot GET /api/pod-logs the frontend already fetched (and keeps on
    screen) covers that, and Follow only ever appends to it, so starting
    the stream from "now" instead avoids showing the same lines twice.
    """
    return core_v1.read_namespaced_pod_log(
        name,
        namespace,
        container=container,
        follow=True,
        tail_lines=0,
        _preload_content=False,
    )

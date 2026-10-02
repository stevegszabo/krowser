import asyncio
import threading

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from krowser.api.deps import get_kube_client_manager
from krowser.k8s.pod_logs_stream import open_log_stream

router = APIRouter(prefix="/api", tags=["logs"])


@router.websocket("/pod-logs-stream")
async def pod_logs_stream(
    websocket: WebSocket,
    name: str,
    namespace: str,
    container: str,
    context: str | None = None,
):
    await websocket.accept()
    loop = asyncio.get_running_loop()

    mgr = get_kube_client_manager()
    core_v1 = mgr.core_v1(context)

    try:
        response = await loop.run_in_executor(
            None, open_log_stream, core_v1, namespace, name, container
        )
    except Exception as exc:
        await websocket.send_json({"type": "error", "data": str(exc)})
        await websocket.close()
        return

    stop_event = threading.Event()

    def pump_output():
        # Closing the response from the main coroutine's `finally` (below)
        # breaks this out of a blocked read with an exception, same role
        # stop_event plays for routes_exec.py's polling loop -- there's
        # nothing to poll here since each chunk read already blocks until
        # new log data (or the connection closing) arrives.
        try:
            for chunk in response.stream(amt=4096, decode_content=True):
                if stop_event.is_set():
                    break
                if not chunk:
                    continue
                text = chunk.decode("utf-8", errors="replace")
                asyncio.run_coroutine_threadsafe(
                    websocket.send_json({"type": "stdout", "data": text}), loop
                )
        except Exception:
            pass
        finally:
            asyncio.run_coroutine_threadsafe(
                websocket.send_json({"type": "exit", "data": None}), loop
            )

    threading.Thread(target=pump_output, daemon=True).start()

    try:
        # The client never sends anything meaningful -- this just blocks
        # until the browser closes the socket (stopping Follow, switching
        # resources, or closing the pane).
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        stop_event.set()
        # Closing (rather than just releasing) the connection is what
        # actually interrupts pump_output()'s blocking read on the other
        # thread -- the same "close it from here to unblock the reader
        # thread" shape as routes_exec.py's ws_client.close().
        try:
            response.close()
        except Exception:
            pass

import asyncio
import json
import shlex
import threading

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from kubernetes.stream.ws_client import RESIZE_CHANNEL

from krowser.api.deps import get_kube_client_manager
from krowser.k8s.pod_exec import open_exec_stream, read_exec_result

router = APIRouter(prefix="/api", tags=["exec"])


@router.websocket("/pod-exec")
async def pod_exec(
    websocket: WebSocket,
    name: str,
    namespace: str,
    container: str,
    command: str,
    context: str | None = None,
):
    await websocket.accept()
    loop = asyncio.get_running_loop()

    mgr = get_kube_client_manager()
    core_v1 = mgr.core_v1(context)
    argv = shlex.split(command) or ["/bin/sh"]

    try:
        ws_client = await loop.run_in_executor(
            None, open_exec_stream, core_v1, namespace, name, container, argv
        )
    except Exception as exc:
        await websocket.send_json({"type": "error", "data": str(exc)})
        await websocket.close()
        return

    stop_event = threading.Event()

    def pump_output():
        try:
            while not stop_event.is_set() and ws_client.is_open():
                ws_client.update(timeout=1)
                if ws_client.peek_stdout(timeout=0):
                    data = ws_client.read_stdout(timeout=0)
                    if data:
                        asyncio.run_coroutine_threadsafe(
                            websocket.send_json({"type": "stdout", "data": data}), loop
                        )
                if ws_client.peek_stderr(timeout=0):
                    data = ws_client.read_stderr(timeout=0)
                    if data:
                        asyncio.run_coroutine_threadsafe(
                            websocket.send_json({"type": "stdout", "data": data}), loop
                        )
        except Exception:
            pass
        finally:
            try:
                returncode, error_message = read_exec_result(ws_client)
            except Exception:
                returncode, error_message = None, None
            if error_message:
                asyncio.run_coroutine_threadsafe(
                    websocket.send_json({"type": "error", "data": error_message}), loop
                )
            asyncio.run_coroutine_threadsafe(
                websocket.send_json({"type": "exit", "data": returncode}), loop
            )

    threading.Thread(target=pump_output, daemon=True).start()

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type")
            if msg_type == "stdin":
                ws_client.write_stdin(msg.get("data", ""))
            elif msg_type == "resize":
                payload = json.dumps(
                    {"Height": msg.get("rows", 24), "Width": msg.get("cols", 80)}
                )
                ws_client.write_channel(RESIZE_CHANNEL, payload)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        stop_event.set()
        ws_client.close()

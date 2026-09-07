from fastapi import APIRouter, Depends

from krowser.api.deps import get_kube_client_manager
from krowser.api.errors import to_http_exception
from krowser.k8s.client import KubeClientManager

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/contexts")
def get_contexts(mgr: KubeClientManager = Depends(get_kube_client_manager)):
    try:
        contexts = mgr.list_contexts()
    except Exception as exc:
        raise to_http_exception(exc) from exc

    current = next((c.name for c in contexts if c.is_current), None)
    return {
        "contexts": [
            {"name": c.name, "cluster": c.cluster, "is_current": c.is_current} for c in contexts
        ],
        "current": current,
    }


@router.get("/namespaces")
def get_namespaces(
    context: str | None = None, mgr: KubeClientManager = Depends(get_kube_client_manager)
):
    try:
        namespaces = mgr.list_namespaces(context)
    except Exception as exc:
        raise to_http_exception(exc) from exc
    return {"namespaces": namespaces}

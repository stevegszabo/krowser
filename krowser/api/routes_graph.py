from fastapi import APIRouter, Depends, HTTPException

from krowser.api.deps import get_kube_client_manager
from krowser.api.errors import to_http_exception
from krowser.graph.builder import GraphBuilder
from krowser.graph.models import Graph
from krowser.k8s.client import KubeClientManager
from krowser.k8s.resource_types import UnknownResourceTypeError, get_resource_type

router = APIRouter(prefix="/api", tags=["graph"])


@router.get("/graph", response_model=Graph)
def get_graph(
    type: str,
    namespace: str | None = None,
    context: str | None = None,
    mgr: KubeClientManager = Depends(get_kube_client_manager),
) -> Graph:
    try:
        get_resource_type(type)
    except UnknownResourceTypeError:
        raise HTTPException(status_code=404, detail=f"unknown resource type: {type}")

    try:
        return GraphBuilder(mgr).build(type, namespace, context)
    except Exception as exc:
        raise to_http_exception(exc) from exc

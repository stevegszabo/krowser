from fastapi import APIRouter, Depends, HTTPException

from krowser.api.deps import get_kube_client_manager
from krowser.api.errors import to_http_exception
from krowser.graph.builder import fetch_root
from krowser.k8s.client import KubeClientManager
from krowser.k8s.resource_types import RESOURCE_TYPES, ICONS_BY_KIND, UnknownResourceTypeError, get_resource_type
from krowser.k8s.status import build_node

router = APIRouter(prefix="/api", tags=["resources"])


@router.get("/resource-types")
def get_resource_types():
    return {
        "resource_types": [
            {
                "id": rt.id,
                "label": rt.label,
                "group": rt.group,
                "icon": rt.icon,
                "namespaced": rt.namespaced,
            }
            for rt in RESOURCE_TYPES
        ]
    }


@router.get("/resources")
def get_resources(
    type: str,
    namespace: str | None = None,
    context: str | None = None,
    mgr: KubeClientManager = Depends(get_kube_client_manager),
):
    try:
        rt = get_resource_type(type)
    except UnknownResourceTypeError:
        raise HTTPException(status_code=404, detail=f"unknown resource type: {type}")

    try:
        objects = fetch_root(mgr, context, namespace, type, rt.kind)
        icon = ICONS_BY_KIND[rt.kind]
        items = [build_node(obj, rt.kind, icon, True) for obj in objects]
    except Exception as exc:
        raise to_http_exception(exc) from exc

    return {
        "resource_type": type,
        "namespace": namespace,
        "items": [item.model_dump() for item in items],
    }

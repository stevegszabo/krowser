import yaml
from fastapi import APIRouter, Depends, HTTPException

from krowser.api.deps import get_kube_client_manager
from krowser.api.errors import to_http_exception
from krowser.k8s.client import KubeClientManager
from krowser.k8s.getters import GETTERS_BY_KIND

router = APIRouter(prefix="/api", tags=["resource"])


@router.get("/resource-yaml")
def get_resource_yaml(
    kind: str,
    name: str,
    namespace: str | None = None,
    context: str | None = None,
    mgr: KubeClientManager = Depends(get_kube_client_manager),
):
    getter = GETTERS_BY_KIND.get(kind)
    if getter is None:
        raise HTTPException(status_code=404, detail=f"unknown kind: {kind}")

    try:
        obj = getter(mgr, context, namespace, name)
        api_client = mgr.api_client_for(context)
        sanitized = api_client.sanitize_for_serialization(obj)
        yaml_text = yaml.safe_dump(sanitized, sort_keys=False, default_flow_style=False)
    except Exception as exc:
        raise to_http_exception(exc) from exc

    return {"yaml": yaml_text, "data": sanitized}

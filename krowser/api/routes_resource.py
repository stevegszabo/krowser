import yaml
from fastapi import APIRouter, Depends, HTTPException

from krowser.api.deps import get_kube_client_manager
from krowser.api.errors import to_http_exception
from krowser.k8s.client import KubeClientManager
from krowser.k8s.getters import GETTERS_BY_KIND, get_pod_events, get_pod_logs
from krowser.k8s.pod_describe import describe_pod

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


@router.get("/pod-describe")
def get_pod_describe(
    name: str,
    namespace: str,
    context: str | None = None,
    mgr: KubeClientManager = Depends(get_kube_client_manager),
):
    try:
        pod = GETTERS_BY_KIND["Pod"](mgr, context, namespace, name)
        events = get_pod_events(mgr, context, namespace, name)
        sections = describe_pod(pod, events)
    except Exception as exc:
        raise to_http_exception(exc) from exc

    return {"sections": sections}


@router.get("/pod-logs")
def get_pod_logs_route(
    name: str,
    namespace: str,
    container: str,
    context: str | None = None,
    mgr: KubeClientManager = Depends(get_kube_client_manager),
):
    try:
        logs = get_pod_logs(mgr, context, namespace, name, container, tail_lines=100)
    except Exception as exc:
        raise to_http_exception(exc) from exc

    return {"logs": logs}

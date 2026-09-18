from datetime import datetime, timezone

import yaml
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from krowser.api.deps import get_kube_client_manager
from krowser.api.errors import to_http_exception
from krowser.k8s.client import KubeClientManager
from krowser.k8s.getters import GETTERS_BY_KIND, get_pod_logs, get_resource_events
from krowser.k8s.pod_describe import describe_pod, event_rows
from krowser.kubescape_scan import scan_workload
from krowser.vuln_scan import scan_image

router = APIRouter(prefix="/api", tags=["resource"])


def _fetch_resource_yaml(
    mgr: KubeClientManager, context: str | None, kind: str, namespace: str | None, name: str
):
    getter = GETTERS_BY_KIND.get(kind)
    if getter is None:
        raise HTTPException(status_code=404, detail=f"unknown kind: {kind}")

    obj = getter(mgr, context, namespace, name)
    api_client = mgr.api_client_for(context)
    sanitized = api_client.sanitize_for_serialization(obj)
    yaml_text = yaml.safe_dump(sanitized, sort_keys=False, default_flow_style=False)
    return yaml_text, sanitized


@router.get("/resource-yaml")
def get_resource_yaml(
    kind: str,
    name: str,
    namespace: str | None = None,
    context: str | None = None,
    mgr: KubeClientManager = Depends(get_kube_client_manager),
):
    try:
        yaml_text, sanitized = _fetch_resource_yaml(mgr, context, kind, namespace, name)
    except HTTPException:
        raise
    except Exception as exc:
        raise to_http_exception(exc) from exc

    return {"yaml": yaml_text, "data": sanitized}


# A real same-origin file response (not a client-side blob: URL) so the
# browser handles it as an ordinary download with the correct filename from
# Content-Disposition -- a blob: URL triggered from a page served over
# plain HTTP on a non-localhost origin gets flagged by Chrome as an
# "insecure download" and saved under a generic "Unconfirmed ####.crdownload"
# name instead.
@router.get("/resource-yaml-download")
def get_resource_yaml_download(
    kind: str,
    name: str,
    namespace: str | None = None,
    context: str | None = None,
    mgr: KubeClientManager = Depends(get_kube_client_manager),
):
    try:
        yaml_text, _ = _fetch_resource_yaml(mgr, context, kind, namespace, name)
    except HTTPException:
        raise
    except Exception as exc:
        raise to_http_exception(exc) from exc

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H-%M-%S")
    filename = f"krowser-{kind.lower()}-{namespace or 'cluster-scoped'}-{name}-{stamp}.yaml"
    return Response(
        content=yaml_text,
        media_type="application/x-yaml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/pod-describe")
def get_pod_describe(
    name: str,
    namespace: str,
    context: str | None = None,
    mgr: KubeClientManager = Depends(get_kube_client_manager),
):
    try:
        pod = GETTERS_BY_KIND["Pod"](mgr, context, namespace, name)
        events = get_resource_events(mgr, context, namespace, "Pod", name)
        sections = describe_pod(pod, events)
    except Exception as exc:
        raise to_http_exception(exc) from exc

    return {"sections": sections}


@router.get("/resource-events")
def get_resource_events_route(
    kind: str,
    name: str,
    namespace: str | None = None,
    context: str | None = None,
    mgr: KubeClientManager = Depends(get_kube_client_manager),
):
    try:
        events = get_resource_events(mgr, context, namespace, kind, name)
        rows = event_rows(events)
    except Exception as exc:
        raise to_http_exception(exc) from exc

    return {"events": rows}


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


@router.get("/pod-vulnscan")
def get_pod_vulnscan(
    name: str,
    namespace: str,
    container: str,
    context: str | None = None,
    mgr: KubeClientManager = Depends(get_kube_client_manager),
):
    try:
        pod = GETTERS_BY_KIND["Pod"](mgr, context, namespace, name)
        all_containers = list(pod.spec.init_containers or []) + list(pod.spec.containers or [])
        image = next((c.image for c in all_containers if c.name == container), None)
        if image is None:
            raise HTTPException(status_code=404, detail=f"unknown container: {container}")
        result = scan_image(image)
    except HTTPException:
        raise
    except Exception as exc:
        raise to_http_exception(exc) from exc

    return result


@router.get("/workload-kubescan")
def get_workload_kubescan(
    kind: str,
    name: str,
    namespace: str,
    context: str | None = None,
):
    try:
        result = scan_workload(kind, namespace, name, context)
    except Exception as exc:
        raise to_http_exception(exc) from exc

    return result

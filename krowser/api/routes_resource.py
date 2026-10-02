from datetime import datetime, timezone

import yaml
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from krowser import action_log
from krowser.api.deps import get_kube_client_manager
from krowser.api.errors import to_http_exception
from krowser.k8s.access import can_i
from krowser.k8s.client import KubeClientManager
from krowser.k8s.custom_resources import get_custom_resource, resolve_served_version
from krowser.k8s.getters import GETTERS_BY_KIND, get_crd, get_pod_logs, get_resource_events
from krowser.k8s.pod_actions import terminate_pod
from krowser.k8s.pod_describe import describe_pod, event_rows
from krowser.k8s.restart import RESTARTABLE_KINDS, restart_workload
from krowser.k8s.rollback import ROLLBACK_KINDS, get_rollout_history, rollback_workload
from krowser.k8s.rollout import ROLLOUT_STATUS_KINDS, get_rollout_status
from krowser.k8s.scale import SCALABLE_KINDS, get_replicas, scale_workload
from krowser.kubescape_scan import scan_workload
from krowser.vuln_scan import scan_image

router = APIRouter(prefix="/api", tags=["resource"])


def _fetch_resource_yaml(
    mgr: KubeClientManager,
    context: str | None,
    kind: str,
    namespace: str | None,
    name: str,
    crd: str | None = None,
):
    if crd:
        # A custom resource instance -- GETTERS_BY_KIND has no entry for an
        # arbitrary CRD Kind, so `crd` (the CRD's own metadata.name, e.g.
        # "virtualmachines.kubevirt.io", attached to the node by
        # GraphBuilder._build_custom_resource_graph) is resolved back to a
        # group/version/plural here instead. get_custom_resource already
        # returns a plain dict (not a typed object), so sanitize_for_serialization
        # is close to a no-op, but still normalizes it the same way as every
        # other kind for a consistent response shape.
        crd_def = get_crd(mgr, context, crd)
        version = resolve_served_version(crd_def)
        sanitized = get_custom_resource(
            mgr, context, namespace, name, crd_def.spec.group, version, crd_def.spec.names.plural
        )
    else:
        getter = GETTERS_BY_KIND.get(kind)
        if getter is None:
            raise HTTPException(status_code=404, detail=f"unknown kind: {kind}")
        obj = getter(mgr, context, namespace, name)
        sanitized = mgr.api_client_for(context).sanitize_for_serialization(obj)

    yaml_text = yaml.safe_dump(sanitized, sort_keys=False, default_flow_style=False)
    return yaml_text, sanitized


@router.get("/resource-yaml")
def get_resource_yaml(
    kind: str,
    name: str,
    namespace: str | None = None,
    context: str | None = None,
    crd: str | None = None,
    mgr: KubeClientManager = Depends(get_kube_client_manager),
):
    try:
        yaml_text, sanitized = _fetch_resource_yaml(mgr, context, kind, namespace, name, crd)
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
    crd: str | None = None,
    mgr: KubeClientManager = Depends(get_kube_client_manager),
):
    try:
        yaml_text, _ = _fetch_resource_yaml(mgr, context, kind, namespace, name, crd)
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


@router.delete("/pod")
def delete_pod_route(
    name: str,
    namespace: str,
    context: str | None = None,
    mgr: KubeClientManager = Depends(get_kube_client_manager),
):
    try:
        terminate_pod(mgr, context, namespace, name)
    except Exception as exc:
        action_log.record("Terminate", "Pod", namespace, name, context, False, str(exc))
        raise to_http_exception(exc) from exc

    action_log.record("Terminate", "Pod", namespace, name, context, True, "terminated")
    return {"status": "terminated"}


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


def _validate_kind(kind: str, allowed: tuple[str, ...], action: str) -> None:
    if kind not in allowed:
        raise HTTPException(status_code=400, detail=f"{action} is not supported for kind: {kind}")


@router.get("/scale")
def get_scale(
    kind: str,
    name: str,
    namespace: str,
    context: str | None = None,
    mgr: KubeClientManager = Depends(get_kube_client_manager),
):
    _validate_kind(kind, SCALABLE_KINDS, "scaling")
    try:
        replicas = get_replicas(mgr, context, kind, namespace, name)
    except Exception as exc:
        raise to_http_exception(exc) from exc

    return {"replicas": replicas}


class ScaleRequest(BaseModel):
    kind: str
    name: str
    namespace: str
    replicas: int
    context: str | None = None


@router.post("/scale")
def post_scale(
    body: ScaleRequest,
    mgr: KubeClientManager = Depends(get_kube_client_manager),
):
    _validate_kind(body.kind, SCALABLE_KINDS, "scaling")
    if body.replicas < 0:
        raise HTTPException(status_code=400, detail="replicas must be >= 0")
    try:
        replicas = scale_workload(mgr, body.context, body.kind, body.namespace, body.name, body.replicas)
    except Exception as exc:
        action_log.record("Scale", body.kind, body.namespace, body.name, body.context, False, str(exc))
        raise to_http_exception(exc) from exc

    action_log.record(
        "Scale", body.kind, body.namespace, body.name, body.context, True, f"scaled to {replicas} replicas"
    )
    return {"replicas": replicas}


class RestartRequest(BaseModel):
    kind: str
    name: str
    namespace: str
    context: str | None = None


@router.post("/restart")
def post_restart(
    body: RestartRequest,
    mgr: KubeClientManager = Depends(get_kube_client_manager),
):
    _validate_kind(body.kind, RESTARTABLE_KINDS, "restarting")
    try:
        restart_workload(mgr, body.context, body.kind, body.namespace, body.name)
    except Exception as exc:
        action_log.record("Restart", body.kind, body.namespace, body.name, body.context, False, str(exc))
        raise to_http_exception(exc) from exc

    action_log.record("Restart", body.kind, body.namespace, body.name, body.context, True, "restarted")
    return {"status": "restarted"}


@router.get("/rollout-status")
def get_rollout_status_route(
    kind: str,
    name: str,
    namespace: str,
    context: str | None = None,
    mgr: KubeClientManager = Depends(get_kube_client_manager),
):
    _validate_kind(kind, ROLLOUT_STATUS_KINDS, "rollout status")
    try:
        return get_rollout_status(mgr, context, kind, namespace, name)
    except Exception as exc:
        raise to_http_exception(exc) from exc


@router.get("/rollout-history")
def get_rollout_history_route(
    kind: str,
    name: str,
    namespace: str,
    context: str | None = None,
    mgr: KubeClientManager = Depends(get_kube_client_manager),
):
    _validate_kind(kind, ROLLBACK_KINDS, "rollout history")
    try:
        history = get_rollout_history(mgr, context, kind, namespace, name)
    except Exception as exc:
        raise to_http_exception(exc) from exc

    return {"history": history}


class RollbackRequest(BaseModel):
    kind: str
    name: str
    namespace: str
    revision: int
    context: str | None = None


@router.post("/rollback")
def post_rollback(
    body: RollbackRequest,
    mgr: KubeClientManager = Depends(get_kube_client_manager),
):
    _validate_kind(body.kind, ROLLBACK_KINDS, "rollback")
    try:
        revision = rollback_workload(mgr, body.context, body.kind, body.namespace, body.name, body.revision)
    except ValueError as exc:
        action_log.record("Rollback", body.kind, body.namespace, body.name, body.context, False, str(exc))
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        action_log.record("Rollback", body.kind, body.namespace, body.name, body.context, False, str(exc))
        raise to_http_exception(exc) from exc

    action_log.record(
        "Rollback", body.kind, body.namespace, body.name, body.context, True, f"rolled back to revision {revision}"
    )
    return {"revision": revision}


class AccessCheck(BaseModel):
    verb: str
    group: str
    resource: str
    subresource: str | None = None
    namespace: str | None = None


class AccessCheckRequest(BaseModel):
    checks: list[AccessCheck]
    context: str | None = None


@router.post("/can-i")
def post_can_i(
    body: AccessCheckRequest,
    mgr: KubeClientManager = Depends(get_kube_client_manager),
):
    """Batches what would otherwise be one `kubectl auth can-i`-equivalent
    round trip per action -- the frontend calls this once per context menu
    open with every check that menu's visible actions need, so a mutating
    action can be grayed out up front instead of only failing with a 403
    after the fact.
    """
    try:
        allowed = [
            can_i(mgr, body.context, c.verb, c.group, c.resource, c.subresource, c.namespace)
            for c in body.checks
        ]
    except Exception as exc:
        raise to_http_exception(exc) from exc

    return {"allowed": allowed}


@router.get("/action-log")
def get_action_log_route():
    """What krowser itself has done to the cluster this session (Scale,
    Restart, Rollback, Terminate) -- an in-memory log, newest first, reset
    on server restart. Exec is deliberately not included here: it's a
    long-lived interactive session with its own visible exit status in the
    terminal, not a single discrete action with one outcome to record.
    """
    return {"entries": action_log.recent()}

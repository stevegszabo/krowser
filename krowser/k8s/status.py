from datetime import datetime, timezone
from typing import Any

from krowser.graph.models import Badge, GraphNode, Health


def humanize_age(creation_timestamp: datetime | None) -> tuple[str, int]:
    if creation_timestamp is None:
        return "unknown", 0
    now = datetime.now(timezone.utc)
    delta = now - creation_timestamp
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 60:
        return f"{seconds}s", seconds
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m", seconds
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h", seconds
    days = hours // 24
    if days < 365:
        return f"{days}d", seconds
    years = days // 365
    return f"{years}y", seconds


def _age_badge(obj: Any) -> tuple[Badge, str, int]:
    age, age_seconds = humanize_age(obj.metadata.creation_timestamp)
    return Badge(text=age, variant="age"), age, age_seconds


def _describe_pod(obj: Any) -> tuple[Health, str, str | None, list[Badge]]:
    phase = obj.status.phase or "Unknown"
    container_statuses = obj.status.container_statuses or []
    total = len(container_statuses) or len(obj.spec.containers or [])
    ready_count = sum(1 for cs in container_statuses if cs.ready)
    crash_looping = any(
        cs.state and cs.state.waiting and cs.state.waiting.reason == "CrashLoopBackOff"
        for cs in container_statuses
    )

    if crash_looping:
        health: Health = "degraded"
        status_label = "CrashLoopBackOff"
    elif phase in ("Running", "Succeeded"):
        health = "healthy"
        status_label = phase
    elif phase == "Pending":
        health = "progressing"
        status_label = phase
    elif phase == "Failed":
        health = "degraded"
        status_label = phase
    else:
        health = "unknown"
        status_label = phase

    ready = f"{ready_count}/{total}" if total else None
    badges = [
        Badge(text=status_label, variant="status"),
        *([Badge(text=ready, variant="ready")] if ready else []),
    ]
    return health, status_label, ready, badges


def _describe_replica_style(obj: Any, ready_field: str) -> tuple[Health, str, str | None, list[Badge]]:
    """Shared logic for Deployment/StatefulSet, which expose the same
    spec.replicas / status.<ready_field> shape."""
    desired = obj.spec.replicas if obj.spec.replicas is not None else 0
    ready_replicas = getattr(obj.status, ready_field, None) or 0

    if desired == 0:
        health: Health = "suspended"
        status_label = "Scaled to zero"
    elif ready_replicas >= desired:
        health = "healthy"
        status_label = "Available"
    else:
        health = "progressing"
        status_label = "Progressing"

    ready = f"{ready_replicas}/{desired}"
    badges = [Badge(text=status_label, variant="status"), Badge(text=ready, variant="ready")]
    return health, status_label, ready, badges


def _describe_daemon_set(obj: Any) -> tuple[Health, str, str | None, list[Badge]]:
    desired = obj.status.desired_number_scheduled or 0
    ready_count = obj.status.number_ready or 0

    if desired == 0:
        health: Health = "unknown"
        status_label = "No nodes scheduled"
    elif ready_count >= desired:
        health = "healthy"
        status_label = "Available"
    else:
        health = "progressing"
        status_label = "Progressing"

    ready = f"{ready_count}/{desired}"
    badges = [Badge(text=status_label, variant="status"), Badge(text=ready, variant="ready")]
    return health, status_label, ready, badges


def _describe_job(obj: Any) -> tuple[Health, str, str | None, list[Badge]]:
    completions = obj.spec.completions if obj.spec.completions is not None else 1
    succeeded = obj.status.succeeded or 0
    failed = obj.status.failed or 0
    backoff_limit = obj.spec.backoff_limit if obj.spec.backoff_limit is not None else 6

    if succeeded >= completions:
        health: Health = "healthy"
        status_label = "Complete"
    elif failed > backoff_limit:
        health = "degraded"
        status_label = "Failed"
    else:
        health = "progressing"
        status_label = "Running"

    ready = f"{succeeded}/{completions}"
    badges = [Badge(text=status_label, variant="status"), Badge(text=ready, variant="ready")]
    return health, status_label, ready, badges


def _describe_cron_job(obj: Any) -> tuple[Health, str, str | None, list[Badge]]:
    suspended = bool(obj.spec.suspend)
    health: Health = "suspended" if suspended else "healthy"
    status_label = "Suspended" if suspended else "Active"
    badges = [Badge(text=status_label, variant="status"), Badge(text=obj.spec.schedule, variant="misc")]
    if obj.status.last_schedule_time:
        last_run, _ = humanize_age(obj.status.last_schedule_time)
        badges.append(Badge(text=f"last run {last_run} ago", variant="misc"))
    return health, status_label, None, badges


def _describe_service(obj: Any) -> tuple[Health, str, str | None, list[Badge]]:
    svc_type = obj.spec.type or "ClusterIP"
    lb_ingress = (obj.status.load_balancer.ingress if obj.status.load_balancer else None) or []

    if svc_type == "LoadBalancer" and not lb_ingress:
        health: Health = "progressing"
        status_label = "Pending"
    else:
        health = "healthy"
        status_label = "Ready"

    badges = [Badge(text=svc_type, variant="status")]
    if obj.spec.cluster_ip and obj.spec.cluster_ip != "None":
        badges.append(Badge(text=obj.spec.cluster_ip, variant="misc"))
    ports = obj.spec.ports or []
    if ports:
        port_text = ",".join(str(p.port) for p in ports[:3])
        badges.append(Badge(text=port_text, variant="misc"))
    return health, status_label, None, badges


def _describe_ingress(obj: Any) -> tuple[Health, str, str | None, list[Badge]]:
    lb_ingress = (obj.status.load_balancer.ingress if obj.status.load_balancer else None) or []
    health: Health = "healthy" if lb_ingress else "progressing"
    status_label = "Ready" if lb_ingress else "Pending"
    badges = [Badge(text=status_label, variant="status")]
    hosts = [rule.host for rule in (obj.spec.rules or []) if rule.host]
    if hosts:
        badges.append(Badge(text=hosts[0] + ("..." if len(hosts) > 1 else ""), variant="misc"))
    return health, status_label, None, badges


_PVC_PHASE_HEALTH: dict[str, Health] = {
    "Bound": "healthy",
    "Pending": "progressing",
    "Lost": "degraded",
}
_PV_PHASE_HEALTH: dict[str, Health] = {
    "Available": "healthy",
    "Bound": "healthy",
    "Released": "progressing",
    "Pending": "progressing",
    "Failed": "degraded",
}


def _describe_pvc(obj: Any) -> tuple[Health, str, str | None, list[Badge]]:
    phase = obj.status.phase or "Unknown"
    health = _PVC_PHASE_HEALTH.get(phase, "unknown")
    badges = [Badge(text=phase, variant="status")]
    capacity = (obj.status.capacity or {}).get("storage")
    if capacity:
        badges.append(Badge(text=capacity, variant="misc"))
    if obj.spec.storage_class_name:
        badges.append(Badge(text=obj.spec.storage_class_name, variant="misc"))
    return health, phase, None, badges


def _describe_pv(obj: Any) -> tuple[Health, str, str | None, list[Badge]]:
    phase = obj.status.phase or "Unknown"
    health = _PV_PHASE_HEALTH.get(phase, "unknown")
    badges = [Badge(text=phase, variant="status")]
    capacity = (obj.spec.capacity or {}).get("storage")
    if capacity:
        badges.append(Badge(text=capacity, variant="misc"))
    if obj.spec.persistent_volume_reclaim_policy:
        badges.append(Badge(text=obj.spec.persistent_volume_reclaim_policy, variant="misc"))
    return health, phase, None, badges


def _describe_config_map(obj: Any) -> tuple[Health, str, str | None, list[Badge]]:
    key_count = len(obj.data or {}) + len(obj.binary_data or {})
    status_label = f"{key_count} key{'s' if key_count != 1 else ''}"
    return "unknown", status_label, None, [Badge(text=status_label, variant="misc")]


def _describe_secret(obj: Any) -> tuple[Health, str, str | None, list[Badge]]:
    key_count = len(obj.data or {})
    status_label = f"{key_count} key{'s' if key_count != 1 else ''}"
    badges = [Badge(text=status_label, variant="misc")]
    if obj.type:
        badges.append(Badge(text=obj.type, variant="misc"))
    return "unknown", status_label, None, badges


def _describe_endpoint_slice(obj: Any) -> tuple[Health, str, str | None, list[Badge]]:
    endpoints = obj.endpoints or []
    total = len(endpoints)
    ready_count = sum(1 for ep in endpoints if ep.conditions and ep.conditions.ready)

    if total == 0:
        health: Health = "unknown"
    elif ready_count == total:
        health = "healthy"
    elif ready_count == 0:
        health = "degraded"
    else:
        health = "progressing"

    status_label = f"{ready_count}/{total} ready"
    ready = f"{ready_count}/{total}"
    return health, status_label, ready, [Badge(text=status_label, variant="ready")]


def _describe_node(obj: Any) -> tuple[Health, str, str | None, list[Badge]]:
    conditions = obj.status.conditions or []
    ready_cond = next((c for c in conditions if c.type == "Ready"), None)
    unschedulable = bool(obj.spec.unschedulable)

    if ready_cond is None or ready_cond.status == "Unknown":
        health: Health = "unknown"
        status_label = "Unknown"
    elif ready_cond.status == "True":
        health = "suspended" if unschedulable else "healthy"
        status_label = "Cordoned" if unschedulable else "Ready"
    else:
        health = "degraded"
        status_label = "NotReady"

    badges = [Badge(text=status_label, variant="status")]
    node_info = obj.status.node_info
    if node_info and node_info.kubelet_version:
        badges.append(Badge(text=node_info.kubelet_version, variant="misc"))
    return health, status_label, None, badges


def _describe_crd(obj: Any) -> tuple[Health, str, str | None, list[Badge]]:
    conditions = obj.status.conditions or []
    terminating = next((c for c in conditions if c.type == "Terminating"), None)
    established = next((c for c in conditions if c.type == "Established"), None)

    if terminating is not None and terminating.status == "True":
        health: Health = "suspended"
        status_label = "Terminating"
    elif established is None or established.status == "Unknown":
        health = "unknown"
        status_label = "Unknown"
    elif established.status == "True":
        health = "healthy"
        status_label = "Established"
    else:
        health = "progressing"
        status_label = "Pending"

    badges = [Badge(text=status_label, variant="status")]
    if obj.spec.scope:
        badges.append(Badge(text=obj.spec.scope, variant="misc"))
    versions = obj.spec.versions or []
    if versions:
        version_names = ",".join(v.name for v in versions[:3])
        badges.append(Badge(text=version_names, variant="misc"))
    return health, status_label, None, badges


_DESCRIBERS = {
    "Pod": _describe_pod,
    "Deployment": lambda obj: _describe_replica_style(obj, "ready_replicas"),
    "StatefulSet": lambda obj: _describe_replica_style(obj, "ready_replicas"),
    "ReplicaSet": lambda obj: _describe_replica_style(obj, "ready_replicas"),
    "DaemonSet": _describe_daemon_set,
    "Job": _describe_job,
    "CronJob": _describe_cron_job,
    "Service": _describe_service,
    "Ingress": _describe_ingress,
    "PersistentVolumeClaim": _describe_pvc,
    "PersistentVolume": _describe_pv,
    "ConfigMap": _describe_config_map,
    "Secret": _describe_secret,
    "EndpointSlice": _describe_endpoint_slice,
    "Node": _describe_node,
    "CustomResourceDefinition": _describe_crd,
}


def build_node(obj: Any, kind: str, icon: str, is_root: bool) -> GraphNode:
    describer = _DESCRIBERS.get(kind)
    if describer is None:
        raise ValueError(f"no status describer registered for kind {kind!r}")

    health, status_label, ready, extra_badges = describer(obj)
    age_badge, age, age_seconds = _age_badge(obj)
    # Static/mirror pods (e.g. kube-apiserver on a control-plane node) carry a
    # real ownerReference back to their Node -- flag them so the frontend can
    # style them distinctly instead of drawing a second, redundant edge.
    is_static = kind == "Pod" and any(
        ref.kind == "Node" for ref in (obj.metadata.owner_references or [])
    )
    # Init containers first (spec order), then regular containers -- used to
    # build one "Get pod logs" context-menu item per container.
    containers = []
    if kind == "Pod":
        containers = [c.name for c in (obj.spec.init_containers or [])] + [
            c.name for c in (obj.spec.containers or [])
        ]

    return GraphNode(
        id=obj.metadata.uid,
        kind=kind,
        name=obj.metadata.name,
        namespace=obj.metadata.namespace,
        icon=icon,
        health=health,
        status_label=status_label,
        age=age,
        age_seconds=age_seconds,
        ready=ready,
        badges=[age_badge, *extra_badges],
        is_root=is_root,
        is_static=is_static,
        containers=containers,
    )

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from kubernetes.utils.quantity import parse_quantity

from krowser.graph.models import Badge, GraphNode, Health
from krowser.k8s.metrics import Usage, VolumeUsage, format_node_usage, format_usage, format_volume_usage


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


def _pod_ephemeral_storage_limit_bytes(obj: Any) -> Decimal | None:
    # Summed across containers, matching how the kubelet itself evaluates a
    # pod's total ephemeral-storage limit for eviction purposes. None (not
    # 0) when no container sets one, so the caller can tell "no limit
    # configured" apart from "limit is zero" -- the former shows plain
    # usage, the latter would nonsensically divide by zero.
    total = None
    for container in obj.spec.containers or []:
        limit = (container.resources.limits or {}).get("ephemeral-storage") if container.resources else None
        if limit:
            total = (total or Decimal(0)) + parse_quantity(limit)
    return total


def _describe_pod(
    obj: Any,
    pod_metrics: dict[tuple[str, str], Usage] | None = None,
    ephemeral_storage_usage: dict[tuple[str, str], Decimal] | None = None,
) -> tuple[Health, str, str | None, list[Badge]]:
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
    usage = (pod_metrics or {}).get((obj.metadata.namespace, obj.metadata.name))
    if usage:
        badges.append(Badge(text=format_usage(usage), variant="metrics"))

    eph_used = (ephemeral_storage_usage or {}).get((obj.metadata.namespace, obj.metadata.name))
    if eph_used is not None:
        # The kubelet sums exactly this (writable container layer + logs +
        # emptyDir) against exactly this limit to decide whether to evict the
        # pod under DiskPressure -- when a limit is actually set, this is a
        # real, precise "about to be evicted" signal, not a heuristic.
        mebibytes = round(eph_used / (1024**2))
        limit = _pod_ephemeral_storage_limit_bytes(obj)
        if limit:
            pct = int(eph_used / limit * 100)
            badges.append(Badge(text=f"Disk: {mebibytes}Mi ({pct}%)", variant="metrics"))
            if health == "healthy" and eph_used / limit >= _VOLUME_NEARLY_FULL_THRESHOLD:
                health = "degraded"
                badges.append(Badge(text="Disk nearly full", variant="warning"))
        else:
            badges.append(Badge(text=f"Disk: {mebibytes}Mi", variant="metrics"))

    restart_count = sum(cs.restart_count for cs in container_statuses) + sum(
        cs.restart_count for cs in (obj.status.init_container_statuses or [])
    )
    if restart_count:
        label = f"{restart_count} restart{'s' if restart_count != 1 else ''}"
        badges.append(Badge(text=label, variant="warning"))
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


def _describe_hpa(obj: Any) -> tuple[Health, str, str | None, list[Badge]]:
    min_replicas = obj.spec.min_replicas if obj.spec.min_replicas is not None else 1
    max_replicas = obj.spec.max_replicas or 0
    current = obj.status.current_replicas
    desired = obj.status.desired_replicas
    conditions = obj.status.conditions or []
    scaling_active = next((c for c in conditions if c.type == "ScalingActive"), None)

    if scaling_active is not None and scaling_active.status == "False":
        health: Health = "degraded"
        status_label = scaling_active.reason or "Unable to scale"
    elif current is None:
        health = "progressing"
        status_label = "Pending"
    else:
        health = "healthy"
        status_label = "Active"

    ready = f"{current if current is not None else '?'}/{desired if desired is not None else '?'}"
    badges = [
        Badge(text=status_label, variant="status"),
        Badge(text=ready, variant="ready"),
        Badge(text=f"min {min_replicas} · max {max_replicas}", variant="misc"),
    ]
    return health, status_label, ready, badges


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


# A volume (or, in _describe_pod above, a pod's ephemeral-storage limit)
# above this fraction used is flagged as a real problem (degraded, not just
# "healthy" with a big number) -- "pod stuck/evicted, disk full" is a common
# incident this makes visible instead of silent until something actually
# breaks. Only escalates a genuinely healthy starting point; a PVC already
# Pending/Lost/Failed (or a pod already CrashLoopBackOff/Failed) keeps that
# more specific health as-is.
_VOLUME_NEARLY_FULL_THRESHOLD = 0.9


def _describe_pvc(obj: Any, usage: VolumeUsage | None = None) -> tuple[Health, str, str | None, list[Badge]]:
    phase = obj.status.phase or "Unknown"
    health = _PVC_PHASE_HEALTH.get(phase, "unknown")
    badges = [Badge(text=phase, variant="status")]
    if usage and usage.capacity_bytes:
        badges.append(Badge(text=format_volume_usage(usage), variant="metrics"))
        if health == "healthy" and usage.used_bytes / usage.capacity_bytes >= _VOLUME_NEARLY_FULL_THRESHOLD:
            health = "degraded"
            badges.append(Badge(text="Nearly full", variant="warning"))
    else:
        capacity = (obj.status.capacity or {}).get("storage")
        if capacity:
            badges.append(Badge(text=capacity, variant="misc"))
    if obj.spec.storage_class_name:
        badges.append(Badge(text=obj.spec.storage_class_name, variant="misc"))
    return health, phase, None, badges


def _describe_pv(obj: Any, usage: VolumeUsage | None = None) -> tuple[Health, str, str | None, list[Badge]]:
    phase = obj.status.phase or "Unknown"
    health = _PV_PHASE_HEALTH.get(phase, "unknown")
    badges = [Badge(text=phase, variant="status")]
    if usage and usage.capacity_bytes:
        badges.append(Badge(text=format_volume_usage(usage), variant="metrics"))
        if health == "healthy" and usage.used_bytes / usage.capacity_bytes >= _VOLUME_NEARLY_FULL_THRESHOLD:
            health = "degraded"
            badges.append(Badge(text="Nearly full", variant="warning"))
    else:
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


def _describe_service_account(obj: Any) -> tuple[Health, str, str | None, list[Badge]]:
    # No status/condition concept -- "unknown" health matches the existing
    # ConfigMap/Secret precedent.
    pull_secret_count = len(obj.image_pull_secrets or [])
    if pull_secret_count:
        status_label = f"{pull_secret_count} image pull secret{'s' if pull_secret_count != 1 else ''}"
        return "unknown", status_label, None, [Badge(text=status_label, variant="misc")]
    return "unknown", "ServiceAccount", None, []


def _describe_role(obj: Any) -> tuple[Health, str, str | None, list[Badge]]:
    # RBAC policy objects carry no status/condition concept -- "unknown"
    # health matches the existing ConfigMap/Secret precedent.
    rule_count = len(obj.rules or [])
    status_label = f"{rule_count} rule{'s' if rule_count != 1 else ''}"
    return "unknown", status_label, None, [Badge(text=status_label, variant="misc")]


def _describe_role_binding(obj: Any) -> tuple[Health, str, str | None, list[Badge]]:
    subject_count = len(obj.subjects or [])
    status_label = f"{subject_count} subject{'s' if subject_count != 1 else ''}"
    return "unknown", status_label, None, [Badge(text=status_label, variant="misc")]


def _describe_network_policy(obj: Any) -> tuple[Health, str, str | None, list[Badge]]:
    # No status/condition concept -- "unknown" health matches the existing
    # ConfigMap/Secret/Role precedent. The API server always populates
    # spec.policyTypes server-side (defaulting it from whether ingress/egress
    # rules are present) by the time a real object is fetched, so there's no
    # need to re-derive that default client-side here.
    policy_types = obj.spec.policy_types or []
    status_label = ", ".join(policy_types) or "No policy types"
    badges = [Badge(text=status_label, variant="status")]
    if "Ingress" in policy_types:
        ingress_count = len(obj.spec.ingress or [])
        badges.append(
            Badge(text=f"{ingress_count} ingress rule{'s' if ingress_count != 1 else ''}", variant="misc")
        )
    if "Egress" in policy_types:
        egress_count = len(obj.spec.egress or [])
        badges.append(
            Badge(text=f"{egress_count} egress rule{'s' if egress_count != 1 else ''}", variant="misc")
        )
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


def _format_node_usage(obj: Any, usage: Usage) -> str:
    # Percentages need the node's own allocatable capacity, which lives on
    # the Node object itself (no extra fetch) -- fall back to plain usage if
    # it's ever missing rather than showing a bogus 0%/division error.
    allocatable = obj.status.allocatable or {}
    if "cpu" not in allocatable or "memory" not in allocatable:
        return format_usage(usage)
    return format_node_usage(
        usage,
        Usage(
            cpu_cores=parse_quantity(allocatable["cpu"]),
            memory_bytes=parse_quantity(allocatable["memory"]),
        ),
    )


# A node reports these independently of its own Ready condition -- the
# kubelet starts evicting pods (DiskPressure/MemoryPressure) or refusing new
# ones (PIDPressure) long before it would ever flip Ready to False, so a
# node can sit at "healthy" Ready while already in real, actionable trouble.
_PRESSURE_CONDITIONS = ("DiskPressure", "MemoryPressure", "PIDPressure")


def _describe_node(
    obj: Any, node_metrics: dict[str, Usage] | None = None
) -> tuple[Health, str, str | None, list[Badge]]:
    conditions = obj.status.conditions or []
    conditions_by_type = {c.type: c for c in conditions}
    ready_cond = conditions_by_type.get("Ready")
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

    active_pressures = []
    for cond_type in _PRESSURE_CONDITIONS:
        cond = conditions_by_type.get(cond_type)
        if cond and cond.status == "True":
            active_pressures.append(cond_type)
    if active_pressures:
        # Overrides "healthy"/"unknown" -- a confirmed pressure condition is
        # always more informative than either. Doesn't touch status_label
        # (still "Ready"/"Cordoned"/etc., which remains literally true) --
        # the pressure itself gets its own badge below instead.
        health = "degraded"
        badges.append(Badge(text=", ".join(active_pressures), variant="warning"))

    node_info = obj.status.node_info
    if node_info and node_info.kubelet_version:
        badges.append(Badge(text=node_info.kubelet_version, variant="misc"))

    # Capacity only (not live usage) -- metrics-server's Metrics API (used
    # for the CPU/memory badge below) doesn't expose ephemeral-storage at
    # all; that would need the kubelet's separate Summary API, which krowser
    # doesn't integrate with. Still meaningfully more than nothing: at least
    # shows how much disk a node has to work with, alongside the pressure
    # condition above for whether it's currently a problem.
    allocatable = obj.status.allocatable or {}
    ephemeral_storage = allocatable.get("ephemeral-storage")
    if ephemeral_storage:
        gib = round(parse_quantity(ephemeral_storage) / (1024**3))
        badges.append(Badge(text=f"Disk: {gib}Gi", variant="misc"))

    usage = (node_metrics or {}).get(obj.metadata.name)
    if usage:
        badges.append(Badge(text=_format_node_usage(obj, usage), variant="metrics"))
    return health, status_label, None, badges


def _describe_namespace(obj: Any) -> tuple[Health, str, str | None, list[Badge]]:
    phase = obj.status.phase if obj.status else None
    phase = phase or "Unknown"
    # Terminating usually resolves in seconds; a namespace stuck there is a
    # real, common problem (finalizers that never complete), so it's flagged
    # "degraded" rather than a normal transient state -- unlike a Pod's own
    # "Pending", which is progressing/expected.
    health: Health = "healthy" if phase == "Active" else "degraded" if phase == "Terminating" else "unknown"
    return health, phase, None, [Badge(text=phase, variant="status")]


def _describe_volume_attachment(obj: Any) -> tuple[Health, str, str | None, list[Badge]]:
    status = obj.status
    attached = status.attached if status else False
    error = (status.attach_error or status.detach_error) if status else None

    if error:
        health: Health = "degraded"
        status_label = "Attach error" if status.attach_error else "Detach error"
    elif attached:
        health = "healthy"
        status_label = "Attached"
    else:
        health = "progressing"
        status_label = "Attaching"

    badges = [
        Badge(text=status_label, variant="status"),
        Badge(text=obj.spec.node_name, variant="misc"),
    ]
    return health, status_label, None, badges


def _describe_storage_class(obj: Any) -> tuple[Health, str, str | None, list[Badge]]:
    # No status/condition concept -- "unknown" health matches the existing
    # ConfigMap/NetworkPolicy/ResourceQuota precedent. reclaimPolicy defaults
    # to "Delete" server-side when unset, same as the API's own default.
    provisioner = obj.provisioner
    reclaim_policy = obj.reclaim_policy or "Delete"
    status_label = provisioner
    badges = [
        Badge(text=provisioner, variant="misc"),
        Badge(text=f"Reclaim: {reclaim_policy}", variant="misc"),
    ]
    if obj.volume_binding_mode:
        badges.append(Badge(text=obj.volume_binding_mode, variant="misc"))
    return "unknown", status_label, None, badges


def _describe_pod_disruption_budget(obj: Any) -> tuple[Health, str, str | None, list[Badge]]:
    # disruptionsAllowed hitting 0 is a real, meaningful health signal (not
    # just a number like ResourceQuota/LimitRange) -- it means the very next
    # voluntary eviction (a node drain, a rollout) will be blocked until
    # currentHealthy recovers above the budget, a common source of stuck
    # drains that's otherwise invisible without reading this object directly.
    status = obj.status
    disruptions_allowed = status.disruptions_allowed if status else 0
    current_healthy = status.current_healthy if status else 0
    desired_healthy = status.desired_healthy if status else 0

    if disruptions_allowed > 0:
        health: Health = "healthy"
        status_label = f"{disruptions_allowed} disruption{'s' if disruptions_allowed != 1 else ''} allowed"
    else:
        health = "degraded"
        status_label = "No disruptions allowed"

    ready = f"{current_healthy}/{desired_healthy}"
    badges = [
        Badge(text=status_label, variant="status"),
        Badge(text=ready, variant="ready"),
    ]
    return health, status_label, ready, badges


def _describe_resource_quota(obj: Any) -> tuple[Health, str, str | None, list[Badge]]:
    # No status/condition concept of its own (used/hard are just numbers, not
    # a pass/fail signal) -- "unknown" health matches the ConfigMap/Role
    # precedent. Full hard/used detail is one click away via "Get resourcequota".
    hard = obj.spec.hard or {}
    count = len(hard)
    status_label = f"{count} constraint{'s' if count != 1 else ''}"
    return "unknown", status_label, None, [Badge(text=status_label, variant="misc")]


def _describe_limit_range(obj: Any) -> tuple[Health, str, str | None, list[Badge]]:
    # Same "unknown" precedent as ResourceQuota -- a LimitRange is pure
    # policy/config, no runtime status to report.
    limits = obj.spec.limits or []
    count = len(limits)
    status_label = f"{count} limit{'s' if count != 1 else ''}"
    return "unknown", status_label, None, [Badge(text=status_label, variant="misc")]


def _describe_custom_resource(obj: Any) -> tuple[Health, str, str | None, list[Badge]]:
    # Arbitrary CRD schemas have no well-known status shape krowser can
    # interpret -- "unknown" health, like ConfigMap/Secret, rather than
    # guessing at conventions that don't universally hold. Many CRDs do
    # follow the common status.conditions[] convention (ArgoCD Application,
    # cert-manager Certificate, KubeVirt VirtualMachine, ...), so the first
    # condition's type/status is surfaced as reference info -- but never used
    # to decide health, since a given condition's "False" can mean healthy or
    # unhealthy entirely depending on that CRD's own semantics.
    conditions = (obj.status.conditions if obj.status else None) or []
    if conditions:
        first = conditions[0]
        status_label = f"{first.type}: {first.status}"
        badges = [Badge(text=status_label, variant="misc")]
    else:
        status_label = "Custom resource"
        badges = []
    return "unknown", status_label, None, badges


_DESCRIBERS = {
    "Deployment": lambda obj: _describe_replica_style(obj, "ready_replicas"),
    "StatefulSet": lambda obj: _describe_replica_style(obj, "ready_replicas"),
    "ReplicaSet": lambda obj: _describe_replica_style(obj, "ready_replicas"),
    "DaemonSet": _describe_daemon_set,
    "Job": _describe_job,
    "CronJob": _describe_cron_job,
    "Service": _describe_service,
    "Ingress": _describe_ingress,
    "ConfigMap": _describe_config_map,
    "Secret": _describe_secret,
    "ServiceAccount": _describe_service_account,
    "Role": _describe_role,
    "ClusterRole": _describe_role,
    "RoleBinding": _describe_role_binding,
    "ClusterRoleBinding": _describe_role_binding,
    "EndpointSlice": _describe_endpoint_slice,
    "HorizontalPodAutoscaler": _describe_hpa,
    "NetworkPolicy": _describe_network_policy,
    "Namespace": _describe_namespace,
    "ResourceQuota": _describe_resource_quota,
    "LimitRange": _describe_limit_range,
    "PodDisruptionBudget": _describe_pod_disruption_budget,
    "VolumeAttachment": _describe_volume_attachment,
    "StorageClass": _describe_storage_class,
}


def build_node(
    obj: Any,
    kind: str,
    icon: str,
    is_root: bool,
    *,
    node_metrics: dict[str, Usage] | None = None,
    pod_metrics: dict[tuple[str, str], Usage] | None = None,
    pod_ephemeral_storage_usage: dict[tuple[str, str], Decimal] | None = None,
    pvc_usage: dict[tuple[str, str], VolumeUsage] | None = None,
    crd: str | None = None,
) -> GraphNode:
    # Node/Pod/PersistentVolumeClaim/PersistentVolume take extra (metrics)
    # arguments the other describers don't, so they're special-cased here
    # rather than threading an unused param through every entry in _DESCRIBERS.
    if kind == "Node":
        health, status_label, ready, extra_badges = _describe_node(obj, node_metrics)
    elif kind == "Pod":
        health, status_label, ready, extra_badges = _describe_pod(obj, pod_metrics, pod_ephemeral_storage_usage)
    elif kind == "PersistentVolumeClaim":
        usage = (pvc_usage or {}).get((obj.metadata.namespace, obj.metadata.name))
        health, status_label, ready, extra_badges = _describe_pvc(obj, usage)
    elif kind == "PersistentVolume":
        claim_ref = obj.spec.claim_ref
        key = (claim_ref.namespace, claim_ref.name) if claim_ref else None
        usage = (pvc_usage or {}).get(key) if key else None
        health, status_label, ready, extra_badges = _describe_pv(obj, usage)
    else:
        health, status_label, ready, extra_badges = _DESCRIBERS.get(kind, _describe_custom_resource)(obj)
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
        crd=crd,
    )

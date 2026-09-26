from decimal import Decimal

from kubernetes import client as k8s

from krowser.k8s.custom_resources import AttrDict
from krowser.k8s.metrics import Usage, VolumeUsage
from krowser.k8s.status import build_node


def test_pod_crashloop_overrides_running_phase_as_degraded(make_pod):
    pod = make_pod("pod-1", "app", phase="Running")
    pod.status.container_statuses[0].ready = False
    pod.status.container_statuses[0].state = k8s.V1ContainerState(
        waiting=k8s.V1ContainerStateWaiting(reason="CrashLoopBackOff")
    )

    node = build_node(pod, "Pod", "pod", is_root=True)

    assert node.health == "degraded"
    assert node.status_label == "CrashLoopBackOff"
    assert node.ready == "0/1"


def test_pod_shows_usage_badge_when_metrics_available(make_pod):
    pod = make_pod("pod-1", "app", namespace="ns")
    pod_metrics = {("ns", "app"): Usage(cpu_cores=Decimal("0.12"), memory_bytes=Decimal(340 * 1024**2))}

    node = build_node(pod, "Pod", "pod", is_root=True, pod_metrics=pod_metrics)

    assert any(b.variant == "metrics" and b.text == "120m / 340Mi" for b in node.badges)


def test_pod_has_no_usage_badge_when_metrics_unavailable(make_pod):
    pod = make_pod("pod-1", "app", namespace="ns")

    node = build_node(pod, "Pod", "pod", is_root=True)

    assert not any(b.variant == "metrics" for b in node.badges)

    node_with_empty_metrics = build_node(pod, "Pod", "pod", is_root=True, pod_metrics={})
    assert not any(b.variant == "metrics" for b in node_with_empty_metrics.badges)


def test_pod_shows_plain_ephemeral_storage_badge_without_a_limit(make_pod):
    pod = make_pod("pod-1", "app", namespace="ns")

    node = build_node(
        pod, "Pod", "pod", is_root=True, pod_ephemeral_storage_usage={("ns", "app"): Decimal(50 * 1024**2)}
    )

    assert any(b.variant == "metrics" and b.text == "Disk: 50Mi" for b in node.badges)
    assert node.health == "healthy"


def test_pod_shows_percentage_and_degrades_when_ephemeral_storage_limit_nearly_full(make_pod):
    container = k8s.V1Container(
        name="app",
        image="nginx",
        resources=k8s.V1ResourceRequirements(limits={"ephemeral-storage": "100Mi"}),
    )
    pod = make_pod("pod-1", "app", namespace="ns", containers=[container])

    node = build_node(
        pod, "Pod", "pod", is_root=True, pod_ephemeral_storage_usage={("ns", "app"): Decimal(95 * 1024**2)}
    )

    assert any(b.variant == "metrics" and b.text == "Disk: 95Mi (95%)" for b in node.badges)
    assert any(b.variant == "warning" and b.text == "Disk nearly full" for b in node.badges)
    assert node.health == "degraded"


def test_pod_with_ephemeral_storage_below_threshold_stays_healthy(make_pod):
    container = k8s.V1Container(
        name="app",
        image="nginx",
        resources=k8s.V1ResourceRequirements(limits={"ephemeral-storage": "100Mi"}),
    )
    pod = make_pod("pod-1", "app", namespace="ns", containers=[container])

    node = build_node(
        pod, "Pod", "pod", is_root=True, pod_ephemeral_storage_usage={("ns", "app"): Decimal(10 * 1024**2)}
    )

    assert any(b.variant == "metrics" and b.text == "Disk: 10Mi (10%)" for b in node.badges)
    assert not any(b.variant == "warning" for b in node.badges)
    assert node.health == "healthy"


def test_pod_crashloop_not_overridden_healthy_by_low_ephemeral_storage_usage(make_pod):
    # Nearly-full only escalates a genuinely healthy pod -- it must never
    # improve an already-bad health signal back to "healthy".
    pod = make_pod("pod-1", "app", namespace="ns", phase="Running")
    pod.status.container_statuses[0].state = k8s.V1ContainerState(
        waiting=k8s.V1ContainerStateWaiting(reason="CrashLoopBackOff")
    )

    node = build_node(
        pod, "Pod", "pod", is_root=True, pod_ephemeral_storage_usage={("ns", "app"): Decimal(1024)}
    )

    assert node.health == "degraded"


def test_pod_has_no_ephemeral_storage_badge_without_usage_data(make_pod):
    pod = make_pod("pod-1", "app", namespace="ns")

    node = build_node(pod, "Pod", "pod", is_root=True)

    assert not any("Disk" in b.text for b in node.badges)


def test_pod_shows_restart_badge_when_container_has_restarted(make_pod):
    pod = make_pod("pod-1", "app")
    pod.status.container_statuses[0].restart_count = 3

    node = build_node(pod, "Pod", "pod", is_root=True)

    assert any(b.variant == "warning" and b.text == "3 restarts" for b in node.badges)


def test_pod_restart_badge_uses_singular_for_one_restart(make_pod):
    pod = make_pod("pod-1", "app")
    pod.status.container_statuses[0].restart_count = 1

    node = build_node(pod, "Pod", "pod", is_root=True)

    assert any(b.variant == "warning" and b.text == "1 restart" for b in node.badges)


def test_pod_has_no_restart_badge_when_never_restarted(make_pod):
    pod = make_pod("pod-1", "app")

    node = build_node(pod, "Pod", "pod", is_root=True)

    assert not any(b.variant == "warning" for b in node.badges)


def test_pod_restart_badge_sums_init_container_restarts(make_pod):
    pod = make_pod("pod-1", "app")
    pod.status.container_statuses[0].restart_count = 1
    pod.status.init_container_statuses = [
        k8s.V1ContainerStatus(
            name="init",
            ready=True,
            restart_count=2,
            image="busybox",
            image_id="",
            state=k8s.V1ContainerState(terminated=k8s.V1ContainerStateTerminated(exit_code=0)),
        )
    ]

    node = build_node(pod, "Pod", "pod", is_root=True)

    assert any(b.variant == "warning" and b.text == "3 restarts" for b in node.badges)


def test_deployment_scaled_to_zero_is_suspended(make_deployment):
    deploy = make_deployment("dep-1", "web", replicas=0, ready_replicas=0)
    node = build_node(deploy, "Deployment", "deployment", is_root=True)
    assert node.health == "suspended"
    assert node.ready == "0/0"


def test_deployment_progressing_when_not_fully_ready(make_deployment):
    deploy = make_deployment("dep-1", "web", replicas=3, ready_replicas=1)
    node = build_node(deploy, "Deployment", "deployment", is_root=True)
    assert node.health == "progressing"
    assert node.ready == "1/3"


def test_configmap_and_secret_report_unknown_health_not_a_false_positive(make_pod):
    cm = k8s.V1ConfigMap(
        metadata=k8s.V1ObjectMeta(uid="cm-1", name="cfg", namespace="ns"),
        data={"a": "1", "b": "2"},
    )
    node = build_node(cm, "ConfigMap", "configmap", is_root=True)
    assert node.health == "unknown"
    assert node.status_label == "2 keys"


def test_pvc_phase_health_mapping(make_pvc):
    bound = build_node(make_pvc("p1", "d1", phase="Bound"), "PersistentVolumeClaim", "pvc", True)
    pending = build_node(make_pvc("p2", "d2", phase="Pending"), "PersistentVolumeClaim", "pvc", True)
    lost = build_node(make_pvc("p3", "d3", phase="Lost"), "PersistentVolumeClaim", "pvc", True)

    assert (bound.health, pending.health, lost.health) == ("healthy", "progressing", "degraded")


def test_pvc_shows_usage_badge_and_degrades_when_nearly_full(make_pvc):
    pvc_obj = make_pvc("p1", "data-1", phase="Bound")
    usage = VolumeUsage(used_bytes=Decimal(95 * 1024**3), capacity_bytes=Decimal(100 * 1024**3))

    node = build_node(pvc_obj, "PersistentVolumeClaim", "pvc", True, pvc_usage={("ns", "data-1"): usage})

    assert node.health == "degraded"
    assert any(b.variant == "metrics" and b.text == "95Gi / 100Gi (95%)" for b in node.badges)
    assert any(b.variant == "warning" and b.text == "Nearly full" for b in node.badges)


def test_pvc_with_usage_below_threshold_stays_healthy(make_pvc):
    pvc_obj = make_pvc("p1", "data-1", phase="Bound")
    usage = VolumeUsage(used_bytes=Decimal(50 * 1024**3), capacity_bytes=Decimal(100 * 1024**3))

    node = build_node(pvc_obj, "PersistentVolumeClaim", "pvc", True, pvc_usage={("ns", "data-1"): usage})

    assert node.health == "healthy"
    assert any(b.variant == "metrics" and b.text == "50Gi / 100Gi (50%)" for b in node.badges)
    assert not any(b.variant == "warning" for b in node.badges)


def test_pvc_pending_with_high_usage_does_not_override_progressing(make_pvc):
    # Nearly-full only escalates a genuinely healthy Bound phase -- a
    # Pending/Lost PVC already has a more specific, real health signal.
    pvc_obj = make_pvc("p1", "data-1", phase="Pending")
    usage = VolumeUsage(used_bytes=Decimal(99 * 1024**3), capacity_bytes=Decimal(100 * 1024**3))

    node = build_node(pvc_obj, "PersistentVolumeClaim", "pvc", True, pvc_usage={("ns", "data-1"): usage})

    assert node.health == "progressing"


def test_pvc_falls_back_to_plain_capacity_badge_without_usage(make_pvc):
    pvc_obj = make_pvc("p1", "data-1", phase="Bound")

    node = build_node(pvc_obj, "PersistentVolumeClaim", "pvc", True)

    assert node.health == "healthy"
    assert any(b.variant == "misc" and b.text == "1Gi" for b in node.badges)
    assert not any(b.variant == "metrics" for b in node.badges)


def test_pv_shows_usage_via_its_bound_claim_ref(make_pv):
    pv_obj = make_pv("v1", "pv-1", phase="Bound", claim_ref_namespace="ns", claim_ref_name="data-1")
    usage = VolumeUsage(used_bytes=Decimal(95 * 1024**3), capacity_bytes=Decimal(100 * 1024**3))

    node = build_node(pv_obj, "PersistentVolume", "pv", True, pvc_usage={("ns", "data-1"): usage})

    assert node.health == "degraded"
    assert any(b.variant == "metrics" and b.text == "95Gi / 100Gi (95%)" for b in node.badges)
    assert any(b.variant == "warning" and b.text == "Nearly full" for b in node.badges)


def test_pv_without_claim_ref_falls_back_to_plain_capacity_badge(make_pv):
    pv_obj = make_pv("v1", "pv-1", phase="Available")

    node = build_node(pv_obj, "PersistentVolume", "pv", True, pvc_usage={("ns", "data-1"): VolumeUsage(Decimal(1), Decimal(1))})

    assert node.health == "healthy"
    assert any(b.variant == "misc" and b.text == "1Gi" for b in node.badges)
    assert not any(b.variant == "metrics" for b in node.badges)


def test_node_condition_health_mapping(make_node):
    ready = build_node(make_node("n1", "worker-1", ready="True"), "Node", "node", True)
    not_ready = build_node(make_node("n2", "worker-2", ready="False"), "Node", "node", True)
    unknown = build_node(make_node("n3", "worker-3", ready="Unknown"), "Node", "node", True)
    cordoned = build_node(
        make_node("n4", "worker-4", ready="True", unschedulable=True), "Node", "node", True
    )

    assert (ready.health, not_ready.health, unknown.health, cordoned.health) == (
        "healthy",
        "degraded",
        "unknown",
        "suspended",
    )
    assert (ready.status_label, cordoned.status_label) == ("Ready", "Cordoned")


def test_node_under_disk_pressure_is_degraded_despite_ready_true(make_node):
    node_obj = make_node(
        "n1", "worker-1", ready="True",
        extra_conditions=[k8s.V1NodeCondition(type="DiskPressure", status="True")],
    )

    node = build_node(node_obj, "Node", "node", True)

    assert node.health == "degraded"
    # status_label itself stays "Ready" -- it's still literally true -- the
    # pressure gets its own separate badge instead.
    assert node.status_label == "Ready"
    assert any(b.variant == "warning" and b.text == "DiskPressure" for b in node.badges)


def test_node_reports_multiple_active_pressures_in_one_badge(make_node):
    node_obj = make_node(
        "n1", "worker-1", ready="True",
        extra_conditions=[
            k8s.V1NodeCondition(type="DiskPressure", status="True"),
            k8s.V1NodeCondition(type="MemoryPressure", status="True"),
            k8s.V1NodeCondition(type="PIDPressure", status="False"),
        ],
    )

    node = build_node(node_obj, "Node", "node", True)

    assert node.health == "degraded"
    assert any(b.variant == "warning" and b.text == "DiskPressure, MemoryPressure" for b in node.badges)


def test_node_without_active_pressure_has_no_warning_badge(make_node):
    node_obj = make_node(
        "n1", "worker-1", ready="True",
        extra_conditions=[k8s.V1NodeCondition(type="DiskPressure", status="False")],
    )

    node = build_node(node_obj, "Node", "node", True)

    assert node.health == "healthy"
    assert not any(b.variant == "warning" for b in node.badges)


def test_node_shows_ephemeral_storage_capacity_badge(make_node):
    node_obj = make_node(
        "n1", "worker-1", ready="True", allocatable={"ephemeral-storage": "104845292Ki"}
    )

    node = build_node(node_obj, "Node", "node", True)

    assert any(b.variant == "misc" and b.text == "Disk: 100Gi" for b in node.badges)


def test_node_shows_usage_badge_with_percentage_of_allocatable(make_node):
    node_obj = make_node("n1", "worker-1", ready="True", allocatable={"cpu": "8", "memory": "8192Mi"})
    node_metrics = {"worker-1": Usage(cpu_cores=Decimal("1.5"), memory_bytes=Decimal(2048 * 1024**2))}

    node = build_node(node_obj, "Node", "node", True, node_metrics=node_metrics)

    assert any(b.variant == "metrics" and b.text == "1500m (18%) / 2048Mi (25%)" for b in node.badges)


def test_node_shows_usage_badge_without_percentage_when_allocatable_missing(make_node):
    node_obj = make_node("n1", "worker-1", ready="True")
    node_metrics = {"worker-1": Usage(cpu_cores=Decimal("1.5"), memory_bytes=Decimal(2048 * 1024**2))}

    node = build_node(node_obj, "Node", "node", True, node_metrics=node_metrics)

    assert any(b.variant == "metrics" and b.text == "1500m / 2048Mi" for b in node.badges)


def test_node_has_no_usage_badge_when_metrics_unavailable(make_node):
    node_obj = make_node("n1", "worker-1", ready="True")

    node = build_node(node_obj, "Node", "node", True)

    assert not any(b.variant == "metrics" for b in node.badges)


def test_static_pod_flagged_is_static(make_pod):
    static_pod = make_pod(
        "pod-1", "kube-apiserver-worker-1",
        owner_refs=[k8s.V1OwnerReference(kind="Node", name="worker-1", uid="node-1", api_version="v1")],
    )
    regular_pod = make_pod("pod-2", "web")

    assert build_node(static_pod, "Pod", "pod", True).is_static is True
    assert build_node(regular_pod, "Pod", "pod", True).is_static is False


def test_pod_node_lists_containers_init_first(make_pod, make_deployment):
    pod = make_pod(
        "pod-1", "web",
        containers=[k8s.V1Container(name="app", image="nginx"), k8s.V1Container(name="sidecar", image="envoy")],
        init_containers=[k8s.V1Container(name="init-setup", image="busybox")],
    )
    assert build_node(pod, "Pod", "pod", True).containers == ["init-setup", "app", "sidecar"]

    deploy = make_deployment("dep-1", "web")
    assert build_node(deploy, "Deployment", "deployment", True).containers == []


def test_service_account_reports_unknown_health(make_service_account):
    node = build_node(make_service_account("sa-1", "my-sa"), "ServiceAccount", "serviceaccount", True)
    assert node.health == "unknown"
    assert node.status_label == "ServiceAccount"


def test_role_reports_rule_count_badge(make_role):
    node = build_node(make_role("role-1", "view"), "Role", "role", True)
    assert node.health == "unknown"
    assert node.status_label == "1 rule"
    assert node.badges[-1].text == "1 rule"


def test_cluster_role_reports_rule_count_badge(make_cluster_role):
    node = build_node(make_cluster_role("cr-1", "view"), "ClusterRole", "clusterrole", True)
    assert node.health == "unknown"
    assert node.status_label == "1 rule"


def test_role_binding_reports_subject_count_badge(make_role_binding):
    node = build_node(
        make_role_binding("rb-1", "view-binding"), "RoleBinding", "rolebinding", True
    )
    assert node.health == "unknown"
    assert node.status_label == "1 subject"


def test_cluster_role_binding_reports_subject_count_badge(make_cluster_role_binding):
    node = build_node(
        make_cluster_role_binding("crb-1", "view-binding"), "ClusterRoleBinding", "clusterrolebinding", True
    )
    assert node.health == "unknown"
    assert node.status_label == "1 subject"


def test_hpa_reports_active_when_reconciled(make_hpa):
    hpa = make_hpa("hpa-1", "web", min_replicas=2, max_replicas=6, current_replicas=3, desired_replicas=4)

    node = build_node(hpa, "HorizontalPodAutoscaler", "hpa", True)

    assert node.health == "healthy"
    assert node.status_label == "Active"
    assert any(b.variant == "ready" and b.text == "3/4" for b in node.badges)
    assert any(b.variant == "misc" and b.text == "min 2 · max 6" for b in node.badges)


def test_hpa_reports_pending_before_first_reconcile(make_hpa):
    # currentReplicas is legitimately omitted by the real API before the
    # first reconcile; desiredReplicas is always present (defaults to 0).
    hpa = make_hpa("hpa-1", "web", current_replicas=None, desired_replicas=0)

    node = build_node(hpa, "HorizontalPodAutoscaler", "hpa", True)

    assert node.health == "progressing"
    assert node.status_label == "Pending"
    assert any(b.variant == "ready" and b.text == "?/0" for b in node.badges)


def test_hpa_reports_degraded_when_scaling_inactive(make_hpa):
    hpa = make_hpa(
        "hpa-1",
        "web",
        conditions=[
            k8s.V2HorizontalPodAutoscalerCondition(
                type="ScalingActive", status="False", reason="FailedGetResourceMetric"
            )
        ],
    )

    node = build_node(hpa, "HorizontalPodAutoscaler", "hpa", True)

    assert node.health == "degraded"
    assert node.status_label == "FailedGetResourceMetric"


def test_network_policy_reports_unknown_health_with_rule_count_badges(make_network_policy):
    policy = make_network_policy(
        "np-1",
        "allow-web",
        policy_types=["Ingress", "Egress"],
        ingress=[k8s.V1NetworkPolicyIngressRule()],
        egress=[k8s.V1NetworkPolicyEgressRule(), k8s.V1NetworkPolicyEgressRule()],
    )

    node = build_node(policy, "NetworkPolicy", "networkpolicy", True)

    assert node.health == "unknown"
    assert node.status_label == "Ingress, Egress"
    assert any(b.variant == "misc" and b.text == "1 ingress rule" for b in node.badges)
    assert any(b.variant == "misc" and b.text == "2 egress rules" for b in node.badges)


def test_network_policy_with_no_policy_types_shows_fallback_label(make_network_policy):
    policy = make_network_policy("np-1", "empty", policy_types=[])

    node = build_node(policy, "NetworkPolicy", "networkpolicy", True)

    assert node.status_label == "No policy types"


def test_namespace_active_is_healthy(make_namespace):
    ns = make_namespace("ns-1", "base-vault", phase="Active")

    node = build_node(ns, "Namespace", "namespace", True)

    assert node.health == "healthy"
    assert node.status_label == "Active"


def test_namespace_terminating_is_degraded(make_namespace):
    ns = make_namespace("ns-1", "old-namespace", phase="Terminating")

    node = build_node(ns, "Namespace", "namespace", True)

    assert node.health == "degraded"
    assert node.status_label == "Terminating"


def test_resource_quota_reports_unknown_health_with_constraint_count(make_resource_quota):
    rq = make_resource_quota("rq-1", "compute-quota", hard={"pods": "10", "cpu": "4"})

    node = build_node(rq, "ResourceQuota", "resourcequota", True)

    assert node.health == "unknown"
    assert node.status_label == "2 constraints"


def test_limit_range_reports_unknown_health_with_limit_count(make_limit_range):
    lr = make_limit_range("lr-1", "defaults", limits=[])

    node = build_node(lr, "LimitRange", "limitrange", True)

    assert node.health == "unknown"
    assert node.status_label == "0 limits"


def test_pod_disruption_budget_with_disruptions_allowed_is_healthy(make_pod_disruption_budget):
    pdb = make_pod_disruption_budget(
        "pdb-1", "web-pdb", disruptions_allowed=2, current_healthy=3, desired_healthy=2
    )

    node = build_node(pdb, "PodDisruptionBudget", "poddisruptionbudget", True)

    assert node.health == "healthy"
    assert node.status_label == "2 disruptions allowed"
    assert node.ready == "3/2"


def test_pod_disruption_budget_with_no_disruptions_allowed_is_degraded(make_pod_disruption_budget):
    pdb = make_pod_disruption_budget(
        "pdb-1", "web-pdb", disruptions_allowed=0, current_healthy=1, desired_healthy=2
    )

    node = build_node(pdb, "PodDisruptionBudget", "poddisruptionbudget", True)

    assert node.health == "degraded"
    assert node.status_label == "No disruptions allowed"


def test_volume_attachment_attached_is_healthy(make_volume_attachment):
    va = make_volume_attachment("va-1", "csi-attach-1", pv_name="pv-1", node_name="worker-1", attached=True)

    node = build_node(va, "VolumeAttachment", "volumeattachment", True)

    assert node.health == "healthy"
    assert node.status_label == "Attached"


def test_volume_attachment_not_yet_attached_is_progressing(make_volume_attachment):
    va = make_volume_attachment("va-1", "csi-attach-1", pv_name="pv-1", attached=False)

    node = build_node(va, "VolumeAttachment", "volumeattachment", True)

    assert node.health == "progressing"
    assert node.status_label == "Attaching"


def test_volume_attachment_with_attach_error_is_degraded(make_volume_attachment):
    va = make_volume_attachment(
        "va-1", "csi-attach-1", pv_name="pv-1", attached=False,
        attach_error=k8s.V1VolumeError(message="mount failed"),
    )

    node = build_node(va, "VolumeAttachment", "volumeattachment", True)

    assert node.health == "degraded"
    assert node.status_label == "Attach error"


def test_storage_class_reports_unknown_health_with_provisioner_and_reclaim_policy(make_storage_class):
    sc = make_storage_class("sc-1", "fast", provisioner="csi.example.com", reclaim_policy="Retain")

    node = build_node(sc, "StorageClass", "storageclass", True)

    assert node.health == "unknown"
    assert node.status_label == "csi.example.com"
    assert any(b.text == "Reclaim: Retain" for b in node.badges)


def test_storage_class_defaults_reclaim_policy_to_delete(make_storage_class):
    sc = make_storage_class("sc-1", "fast", reclaim_policy=None)

    node = build_node(sc, "StorageClass", "storageclass", True)

    assert any(b.text == "Reclaim: Delete" for b in node.badges)


def test_custom_resource_kind_falls_back_to_unknown_health_and_carries_crd_name():
    # A custom resource instance's Kind is arbitrary (from a CRD) and never
    # registered in _DESCRIBERS -- build_node must fall back to
    # _describe_custom_resource instead of raising KeyError, and thread the
    # crd field the frontend needs to fetch this object's raw YAML later.
    obj = AttrDict(
        {
            "metadata": {
                "uid": "cr-1",
                "name": "my-widget",
                "namespace": "ns",
                "creationTimestamp": "2026-01-15T10:00:00Z",
            }
        }
    )

    node = build_node(obj, "Widget", "crd", True, crd="widgets.example.com")

    assert node.health == "unknown"
    assert node.status_label == "Custom resource"
    assert node.crd == "widgets.example.com"
    assert node.kind == "Widget"


def test_custom_resource_surfaces_first_condition_without_using_it_for_health():
    obj = AttrDict(
        {
            "metadata": {"uid": "cr-1", "name": "my-widget", "creationTimestamp": "2026-01-15T10:00:00Z"},
            "status": {"conditions": [{"type": "Ready", "status": "False"}]},
        }
    )

    node = build_node(obj, "Widget", "crd", True, crd="widgets.example.com")

    assert node.health == "unknown"
    assert node.status_label == "Ready: False"

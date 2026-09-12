from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from kubernetes import client as k8s

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


def test_cluster_role_reports_rule_count_badge(make_cluster_role):
    node = build_node(make_cluster_role("cr-1", "view"), "ClusterRole", "clusterrole", True)
    assert node.health == "unknown"
    assert node.status_label == "1 rule"
    assert node.badges[-1].text == "1 rule"


def test_cluster_role_binding_reports_subject_count_badge(make_cluster_role_binding):
    node = build_node(
        make_cluster_role_binding("crb-1", "view-binding", role_name="view"),
        "ClusterRoleBinding",
        "clusterrolebinding",
        True,
    )
    assert node.health == "unknown"
    assert node.status_label == "1 subject"


def test_service_account_reports_unknown_health(make_service_account):
    node = build_node(make_service_account("sa-1", "my-sa"), "ServiceAccount", "serviceaccount", True)
    assert node.health == "unknown"
    assert node.status_label == "ServiceAccount"


def test_custom_resource_kind_falls_back_to_unknown_health_and_carries_api_fields():
    # A custom resource instance's Kind is arbitrary/dynamic (from a CRD) and
    # never registered in _DESCRIBERS -- build_node must fall back cleanly
    # instead of raising, and thread the group/version/plural fields the
    # frontend needs to later fetch this object's raw YAML.
    obj = SimpleNamespace(
        metadata=SimpleNamespace(
            uid="cr-1",
            name="my-widget",
            namespace="ns",
            creation_timestamp=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
    )

    node = build_node(obj, "Widget", "crd", True, api_group="example.com", api_version="v1", plural="widgets")

    assert node.health == "unknown"
    assert node.status_label == "Unknown"
    assert len(node.badges) == 1  # just the age badge -- no kind-specific extras
    assert node.api_group == "example.com"
    assert node.api_version == "v1"
    assert node.plural == "widgets"


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

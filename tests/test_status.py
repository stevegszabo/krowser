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


def test_crd_condition_health_mapping(make_crd):
    established = build_node(make_crd("c1", "widgets.example.com", established="True"), "CustomResourceDefinition", "crd", True)
    pending = build_node(make_crd("c2", "gadgets.example.com", established="False"), "CustomResourceDefinition", "crd", True)
    unknown = build_node(make_crd("c3", "gizmos.example.com", established="Unknown"), "CustomResourceDefinition", "crd", True)

    assert (established.health, pending.health, unknown.health) == ("healthy", "progressing", "unknown")
    assert established.status_label == "Established"


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

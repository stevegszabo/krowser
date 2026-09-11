from datetime import datetime, timedelta, timezone

from kubernetes import client as k8s

from krowser.k8s.pod_describe import describe_pod


def _meta(name="web-1", namespace="ns", labels=None, annotations=None, owner_refs=None):
    return k8s.V1ObjectMeta(
        name=name,
        namespace=namespace,
        labels=labels,
        annotations=annotations,
        owner_references=owner_refs,
    )


def _section(sections, title):
    return next(s["text"] for s in sections if s["title"] == title)


def _titles(sections):
    return [s["title"] for s in sections]


def _full_pod():
    now = datetime.now(timezone.utc) - timedelta(minutes=10)
    return k8s.V1Pod(
        metadata=_meta(labels={"app": "web"}, annotations={"note": "hello"}),
        spec=k8s.V1PodSpec(
            containers=[
                k8s.V1Container(
                    name="app",
                    image="nginx:1.25",
                    ports=[k8s.V1ContainerPort(container_port=80, protocol="TCP")],
                    env=[
                        k8s.V1EnvVar(name="PLAIN", value="hi"),
                        k8s.V1EnvVar(
                            name="FROM_CM",
                            value_from=k8s.V1EnvVarSource(
                                config_map_key_ref=k8s.V1ConfigMapKeySelector(name="app-cm", key="k")
                            ),
                        ),
                    ],
                    volume_mounts=[k8s.V1VolumeMount(name="data", mount_path="/data", read_only=True)],
                    resources=k8s.V1ResourceRequirements(
                        limits={"cpu": "500m"}, requests={"cpu": "100m", "memory": "64Mi"}
                    ),
                )
            ],
            init_containers=[k8s.V1Container(name="init-setup", image="busybox")],
            node_name="worker-1",
            node_selector={"disktype": "ssd"},
            tolerations=[k8s.V1Toleration(key="dedicated", operator="Equal", value="db", effect="NoSchedule")],
            priority=0,
            volumes=[
                k8s.V1Volume(name="data", config_map=k8s.V1ConfigMapVolumeSource(name="app-cm")),
                k8s.V1Volume(name="creds", secret=k8s.V1SecretVolumeSource(secret_name="app-secret")),
            ],
        ),
        status=k8s.V1PodStatus(
            phase="Running",
            pod_ip="10.0.0.5",
            host_ip="192.168.1.10",
            start_time=now,
            qos_class="Burstable",
            conditions=[k8s.V1PodCondition(type="Ready", status="True")],
            container_statuses=[
                k8s.V1ContainerStatus(
                    name="app",
                    ready=True,
                    restart_count=2,
                    image="nginx:1.25",
                    image_id="nginx@sha256:abc",
                    container_id="containerd://xyz",
                    state=k8s.V1ContainerState(running=k8s.V1ContainerStateRunning(started_at=now)),
                )
            ],
        ),
    )


def _make_event(reason, message, event_type="Normal", count=1, component="kubelet", when=None):
    when = when or datetime.now(timezone.utc)
    return k8s.CoreV1Event(
        metadata=k8s.V1ObjectMeta(name=f"evt-{reason}"),
        involved_object=k8s.V1ObjectReference(kind="Pod", name="web-1", namespace="ns"),
        reason=reason,
        message=message,
        type=event_type,
        count=count,
        last_timestamp=when,
        source=k8s.V1EventSource(component=component),
    )


def test_describe_pod_returns_expected_section_titles():
    sections = describe_pod(_full_pod(), [])
    assert _titles(sections) == ["Details", "Init Containers", "Containers", "Conditions", "Volumes", "Events"]


def test_describe_pod_omits_init_containers_section_when_none():
    pod = _full_pod()
    pod.spec.init_containers = None
    sections = describe_pod(pod, [])
    assert _titles(sections) == ["Details", "Containers", "Conditions", "Volumes", "Events"]


def test_describe_pod_details_section():
    text = _section(describe_pod(_full_pod(), []), "Details")

    assert "Name:           web-1" in text
    assert "Namespace:      ns" in text
    assert "Node:           worker-1/192.168.1.10" in text
    assert "app=web" in text
    assert "note: hello" in text
    assert "Status:         Running" in text
    assert "IP:             10.0.0.5" in text
    assert "QoS Class:      Burstable" in text
    assert "disktype=ssd" in text
    assert "dedicated Equal db:NoSchedule" in text


def test_describe_pod_container_details():
    text = _section(describe_pod(_full_pod(), []), "Containers")

    assert "app:" in text
    assert "Image:" in text and "nginx:1.25" in text
    assert "Ready:" in text and "True" in text
    assert "Restart Count:" in text and "2" in text
    assert "PLAIN:  hi" in text
    assert "<set to the key 'k' in config map 'app-cm'>" in text
    assert "/data from data (ro)" in text


def test_describe_pod_init_containers_section():
    text = _section(describe_pod(_full_pod(), []), "Init Containers")
    assert "init-setup:" in text
    assert "busybox" in text


def test_describe_pod_conditions_section():
    text = _section(describe_pod(_full_pod(), []), "Conditions")
    assert "Ready" in text
    assert "True" in text


def test_describe_pod_conditions_column_gap_survives_long_type_name():
    # Real clusters have condition types longer than the 20-char column
    # (e.g. "PodReadyToStartContainers"); the Status column must not run
    # into it with no separating space.
    pod = _full_pod()
    pod.status.conditions = [k8s.V1PodCondition(type="PodReadyToStartContainers", status="True")]
    text = _section(describe_pod(pod, []), "Conditions")
    assert "PodReadyToStartContainers  True" in text


def test_describe_pod_volumes_section():
    text = _section(describe_pod(_full_pod(), []), "Volumes")
    assert "Type:      ConfigMap" in text
    assert "Type:      Secret" in text
    assert "SecretName:  app-secret" in text


def test_describe_pod_no_events_shows_none():
    text = _section(describe_pod(_full_pod(), []), "Events")
    assert text == "<none>"


def test_describe_pod_events_sorted_and_counted():
    older = _make_event("Scheduled", "Successfully assigned", when=datetime.now(timezone.utc) - timedelta(hours=1))
    newer = _make_event("Pulled", "already present", count=3, when=datetime.now(timezone.utc) - timedelta(minutes=1))

    text = _section(describe_pod(_full_pod(), [newer, older]), "Events")

    scheduled_idx = text.index("Scheduled")
    pulled_idx = text.index("Pulled")
    assert scheduled_idx < pulled_idx  # older event listed first
    assert "(x3)" in text


def test_describe_pod_static_pod_controlled_by_node():
    pod = _full_pod()
    pod.metadata.owner_references = [
        k8s.V1OwnerReference(kind="Node", name="worker-1", uid="node-1", api_version="v1")
    ]
    text = _section(describe_pod(pod, []), "Details")
    assert "Controlled By:  Node/worker-1" in text


def test_describe_pod_handles_minimal_pod_without_optional_fields():
    pod = k8s.V1Pod(
        metadata=_meta(),
        spec=k8s.V1PodSpec(containers=[k8s.V1Container(name="app", image="nginx")]),
        status=k8s.V1PodStatus(),
    )

    sections = describe_pod(pod, [])
    details = _section(sections, "Details")

    assert _titles(sections) == ["Details", "Containers", "Conditions", "Volumes", "Events"]
    assert "Name:           web-1" in details
    assert "Labels:         <none>" in details
    assert "Annotations:    <none>" in details
    assert "Node-Selectors: <none>" in details
    assert "Tolerations:    <none>" in details
    assert _section(sections, "Volumes") == "<none>"
    assert _section(sections, "Events") == "<none>"

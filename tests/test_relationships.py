from kubernetes import client as k8s

from krowser.graph.relationships import (
    build_edges,
    link_endpointslice_to_pods,
    link_ingress_to_services,
    link_owner_references,
    link_pod_to_configmap,
    link_pod_to_node,
    link_pod_to_pvc,
    link_pod_to_secret,
    link_pvc_to_pv,
    link_service_to_endpointslices,
)


def test_owner_references_links_deployment_replicaset_pod(make_deployment, make_replica_set, make_pod):
    deploy = make_deployment("dep-1", "web")
    rs = make_replica_set(
        "rs-1", "web-abc123", owner_refs=[k8s.V1OwnerReference(kind="Deployment", name="web", uid="dep-1", api_version="apps/v1")]
    )
    pod = make_pod(
        "pod-1", "web-abc123-xyz", owner_refs=[k8s.V1OwnerReference(kind="ReplicaSet", name="web-abc123", uid="rs-1", api_version="apps/v1")]
    )

    world = {"Deployment": [deploy], "ReplicaSet": [rs], "Pod": [pod]}
    edges = link_owner_references(world)

    assert {(e.source, e.target, e.relation) for e in edges} == {
        ("dep-1", "rs-1", "owns"),
        ("rs-1", "pod-1", "owns"),
    }


def test_owner_references_ignores_dangling_owner_not_in_world(make_replica_set):
    rs = make_replica_set(
        "rs-1", "orphan", owner_refs=[k8s.V1OwnerReference(kind="Deployment", name="gone", uid="not-fetched", api_version="apps/v1")]
    )
    edges = link_owner_references({"ReplicaSet": [rs]})
    assert edges == []


def test_owner_references_excludes_endpointslice_to_avoid_duplicate_exposes_edge(
    make_service, make_endpoint_slice
):
    # Real EndpointSlices carry an ownerReference back to their Service, but
    # that relationship already has its own "exposes" edge -- link_owner_references
    # must not also emit a duplicate "owns" edge for the same pair.
    svc = make_service("svc-1", "web")
    eps = make_endpoint_slice(
        "eps-1", "web-abcde",
        service_name="web",
        owner_refs=[k8s.V1OwnerReference(kind="Service", name="web", uid="svc-1", api_version="v1")],
    )

    edges = link_owner_references({"Service": [svc], "EndpointSlice": [eps]})

    assert edges == []


def test_ingress_routes_to_named_backend_service(make_ingress, make_service):
    svc = make_service("svc-1", "web")
    other_svc = make_service("svc-2", "other")
    ingress = make_ingress("ing-1", "web-ingress", backend_service_names=["web"])

    world = {"Ingress": [ingress], "Service": [svc, other_svc]}
    edges = link_ingress_to_services(world)

    assert [(e.source, e.target) for e in edges] == [("ing-1", "svc-1")]


def test_pod_claims_pvc_by_name_and_namespace(make_pod, make_pvc):
    pvc = make_pvc("pvc-1", "data")
    other_ns_pvc = make_pvc("pvc-2", "data", namespace="other")
    pod = make_pod(
        "pod-1",
        "app",
        volumes=[
            k8s.V1Volume(
                name="vol", persistent_volume_claim=k8s.V1PersistentVolumeClaimVolumeSource(claim_name="data")
            )
        ],
    )

    world = {"Pod": [pod], "PersistentVolumeClaim": [pvc, other_ns_pvc]}
    edges = link_pod_to_pvc(world)

    assert [(e.source, e.target) for e in edges] == [("pod-1", "pvc-1")]


def test_pvc_binds_to_pv_only_when_bound(make_pvc, make_pv):
    bound_pvc = make_pvc("pvc-1", "data", phase="Bound", volume_name="pv-1")
    pending_pvc = make_pvc("pvc-2", "data2", phase="Pending", volume_name="pv-2")
    pv1 = make_pv("pv-1", "pv-1")
    pv2 = make_pv("pv-2", "pv-2")

    world = {"PersistentVolumeClaim": [bound_pvc, pending_pvc], "PersistentVolume": [pv1, pv2]}
    edges = link_pvc_to_pv(world)

    assert [(e.source, e.target) for e in edges] == [("pvc-1", "pv-1")]


def test_pod_runs_on_matching_node(make_pod, make_node):
    node = make_node("node-1", "worker-1")
    pod = make_pod("pod-1", "web", node_name="worker-1")

    world = {"Pod": [pod], "Node": [node]}
    edges = link_pod_to_node(world)

    assert [(e.source, e.target, e.relation) for e in edges] == [("pod-1", "node-1", "runs-on")]


def test_pod_not_linked_to_node_missing_from_world(make_pod):
    pod = make_pod("pod-1", "web", node_name="worker-1")

    edges = link_pod_to_node({"Pod": [pod]})

    assert edges == []


def test_unscheduled_pod_has_no_node_edge(make_pod, make_node):
    node = make_node("node-1", "worker-1")
    pod = make_pod("pod-1", "web")  # node_name defaults to None

    edges = link_pod_to_node({"Pod": [pod], "Node": [node]})

    assert edges == []


def test_static_pod_owned_by_node_has_no_duplicate_runs_on_edge(make_pod, make_node):
    # Static/mirror pods (e.g. kube-apiserver) carry a real ownerReference
    # back to their Node; link_owner_references already covers that as an
    # "owns" edge, so link_pod_to_node must not also emit "runs-on".
    node = make_node("node-1", "worker-1")
    static_pod = make_pod(
        "pod-1", "kube-apiserver-worker-1", node_name="worker-1",
        owner_refs=[k8s.V1OwnerReference(kind="Node", name="worker-1", uid="node-1", api_version="v1")],
    )

    edges = link_pod_to_node({"Pod": [static_pod], "Node": [node]})

    assert edges == []


def test_build_edges_chains_cronjob_job_pod(make_cron_job, make_job, make_pod):
    cron = make_cron_job("cron-1", "nightly")
    job = make_job(
        "job-1", "nightly-12345",
        owner_refs=[k8s.V1OwnerReference(kind="CronJob", name="nightly", uid="cron-1", api_version="batch/v1")],
    )
    pod = make_pod(
        "pod-1", "nightly-12345-abcde",
        owner_refs=[k8s.V1OwnerReference(kind="Job", name="nightly-12345", uid="job-1", api_version="batch/v1")],
    )

    world = {"CronJob": [cron], "Job": [job], "Pod": [pod]}
    edges = build_edges(world)

    assert {(e.source, e.target, e.relation) for e in edges} == {
        ("cron-1", "job-1", "owns"),
        ("job-1", "pod-1", "owns"),
    }


def test_pod_uses_configmap_via_volume_mount(make_pod, make_config_map):
    cm = make_config_map("cm-1", "app-config")
    pod = make_pod(
        "pod-1", "app",
        volumes=[k8s.V1Volume(name="cfg", config_map=k8s.V1ConfigMapVolumeSource(name="app-config"))],
    )

    edges = link_pod_to_configmap({"Pod": [pod], "ConfigMap": [cm]})

    assert [(e.source, e.target, e.relation) for e in edges] == [("pod-1", "cm-1", "uses")]


def test_pod_uses_configmap_via_env_from(make_pod, make_config_map):
    cm = make_config_map("cm-1", "app-config")
    container = k8s.V1Container(
        name="app",
        image="nginx",
        env_from=[k8s.V1EnvFromSource(config_map_ref=k8s.V1ConfigMapEnvSource(name="app-config"))],
    )
    pod = make_pod("pod-1", "app", containers=[container])

    edges = link_pod_to_configmap({"Pod": [pod], "ConfigMap": [cm]})

    assert [(e.source, e.target, e.relation) for e in edges] == [("pod-1", "cm-1", "uses")]


def test_pod_uses_configmap_via_single_env_var(make_pod, make_config_map):
    cm = make_config_map("cm-1", "app-config")
    container = k8s.V1Container(
        name="app",
        image="nginx",
        env=[
            k8s.V1EnvVar(
                name="SOME_KEY",
                value_from=k8s.V1EnvVarSource(
                    config_map_key_ref=k8s.V1ConfigMapKeySelector(name="app-config", key="some-key")
                ),
            )
        ],
    )
    pod = make_pod("pod-1", "app", containers=[container])

    edges = link_pod_to_configmap({"Pod": [pod], "ConfigMap": [cm]})

    assert [(e.source, e.target, e.relation) for e in edges] == [("pod-1", "cm-1", "uses")]


def test_pod_does_not_use_configmap_in_other_namespace(make_pod, make_config_map):
    cm = make_config_map("cm-1", "app-config", namespace="other")
    pod = make_pod(
        "pod-1", "app",
        volumes=[k8s.V1Volume(name="cfg", config_map=k8s.V1ConfigMapVolumeSource(name="app-config"))],
    )

    edges = link_pod_to_configmap({"Pod": [pod], "ConfigMap": [cm]})

    assert edges == []


def test_pod_uses_secret_via_volume_mount(make_pod, make_secret):
    secret = make_secret("secret-1", "app-tls")
    pod = make_pod(
        "pod-1", "app",
        volumes=[k8s.V1Volume(name="tls", secret=k8s.V1SecretVolumeSource(secret_name="app-tls"))],
    )

    edges = link_pod_to_secret({"Pod": [pod], "Secret": [secret]})

    assert [(e.source, e.target, e.relation) for e in edges] == [("pod-1", "secret-1", "uses")]


def test_pod_uses_secret_via_env_from(make_pod, make_secret):
    secret = make_secret("secret-1", "app-secret")
    container = k8s.V1Container(
        name="app",
        image="nginx",
        env_from=[k8s.V1EnvFromSource(secret_ref=k8s.V1SecretEnvSource(name="app-secret"))],
    )
    pod = make_pod("pod-1", "app", containers=[container])

    edges = link_pod_to_secret({"Pod": [pod], "Secret": [secret]})

    assert [(e.source, e.target, e.relation) for e in edges] == [("pod-1", "secret-1", "uses")]


def test_pod_uses_secret_via_single_env_var(make_pod, make_secret):
    secret = make_secret("secret-1", "app-secret")
    container = k8s.V1Container(
        name="app",
        image="nginx",
        env=[
            k8s.V1EnvVar(
                name="SOME_KEY",
                value_from=k8s.V1EnvVarSource(
                    secret_key_ref=k8s.V1SecretKeySelector(name="app-secret", key="some-key")
                ),
            )
        ],
    )
    pod = make_pod("pod-1", "app", containers=[container])

    edges = link_pod_to_secret({"Pod": [pod], "Secret": [secret]})

    assert [(e.source, e.target, e.relation) for e in edges] == [("pod-1", "secret-1", "uses")]


def test_pod_uses_secret_via_image_pull_secret(make_pod, make_secret):
    secret = make_secret("secret-1", "registry-creds")
    pod = make_pod(
        "pod-1", "app",
        image_pull_secrets=[k8s.V1LocalObjectReference(name="registry-creds")],
    )

    edges = link_pod_to_secret({"Pod": [pod], "Secret": [secret]})

    assert [(e.source, e.target, e.relation) for e in edges] == [("pod-1", "secret-1", "uses")]


def test_pod_does_not_use_secret_in_other_namespace(make_pod, make_secret):
    secret = make_secret("secret-1", "app-tls", namespace="other")
    pod = make_pod(
        "pod-1", "app",
        volumes=[k8s.V1Volume(name="tls", secret=k8s.V1SecretVolumeSource(secret_name="app-tls"))],
    )

    edges = link_pod_to_secret({"Pod": [pod], "Secret": [secret]})

    assert edges == []


def test_endpointslice_exposed_by_service_via_label(make_service, make_endpoint_slice):
    svc = make_service("svc-1", "web")
    eps = make_endpoint_slice("eps-1", "web-abcde", service_name="web")

    edges = link_service_to_endpointslices({"Service": [svc], "EndpointSlice": [eps]})

    assert [(e.source, e.target, e.relation) for e in edges] == [("svc-1", "eps-1", "exposes")]


def test_endpointslice_not_exposed_when_service_name_label_missing(make_service, make_endpoint_slice):
    svc = make_service("svc-1", "web")
    eps = make_endpoint_slice("eps-1", "web-abcde", service_name=None)

    edges = link_service_to_endpointslices({"Service": [svc], "EndpointSlice": [eps]})

    assert edges == []


def test_endpointslice_not_exposed_by_service_in_other_namespace(make_service, make_endpoint_slice):
    svc = make_service("svc-1", "web", namespace="other")
    eps = make_endpoint_slice("eps-1", "web-abcde", namespace="ns", service_name="web")

    edges = link_service_to_endpointslices({"Service": [svc], "EndpointSlice": [eps]})

    assert edges == []


def test_endpointslice_targets_pods_by_uid(make_pod, make_endpoint_slice):
    pod1 = make_pod("pod-1", "web-1")
    pod2 = make_pod("pod-2", "web-2")
    eps = make_endpoint_slice(
        "eps-1", "web-abcde",
        pod_targets=[("web-1", "pod-1", True), ("web-2", "pod-2", False)],
    )

    edges = link_endpointslice_to_pods({"Pod": [pod1, pod2], "EndpointSlice": [eps]})

    assert {(e.source, e.target, e.relation) for e in edges} == {
        ("eps-1", "pod-1", "targets"),
        ("eps-1", "pod-2", "targets"),
    }


def test_endpointslice_ignores_target_not_in_fetched_pods(make_pod, make_endpoint_slice):
    pod1 = make_pod("pod-1", "web-1")
    eps = make_endpoint_slice(
        "eps-1", "web-abcde",
        pod_targets=[("web-1", "pod-1", True), ("gone", "pod-uid-not-fetched", True)],
    )

    edges = link_endpointslice_to_pods({"Pod": [pod1], "EndpointSlice": [eps]})

    assert [(e.source, e.target) for e in edges] == [("eps-1", "pod-1")]


def test_build_edges_chains_service_endpointslice_pod(make_service, make_endpoint_slice, make_pod):
    svc = make_service("svc-1", "web", selector={"app": "web"})
    pod = make_pod("pod-1", "web-1", labels={"app": "web"})
    eps = make_endpoint_slice("eps-1", "web-abcde", service_name="web", pod_targets=[("web-1", "pod-1", True)])

    world = {"Service": [svc], "EndpointSlice": [eps], "Pod": [pod]}
    edges = build_edges(world)

    assert {(e.source, e.target, e.relation) for e in edges} == {
        ("svc-1", "eps-1", "exposes"),
        ("eps-1", "pod-1", "targets"),
    }


def test_build_edges_static_pod_has_single_owns_edge_to_node(make_pod, make_node):
    node = make_node("node-1", "worker-1")
    static_pod = make_pod(
        "pod-1", "kube-apiserver-worker-1", namespace="kube-system", node_name="worker-1",
        owner_refs=[k8s.V1OwnerReference(kind="Node", name="worker-1", uid="node-1", api_version="v1")],
    )
    regular_pod = make_pod("pod-2", "web", node_name="worker-1")

    world = {"Node": [node], "Pod": [static_pod, regular_pod]}
    edges = build_edges(world)

    assert {(e.source, e.target, e.relation) for e in edges} == {
        ("node-1", "pod-1", "owns"),
        ("pod-2", "node-1", "runs-on"),
    }

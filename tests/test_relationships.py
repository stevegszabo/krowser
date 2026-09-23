from kubernetes import client as k8s

from krowser.graph.relationships import (
    build_edges,
    link_clusterrolebinding_to_clusterrole,
    link_clusterrolebinding_to_serviceaccount_subjects,
    link_endpointslice_to_pods,
    link_hpa_to_target,
    link_ingress_to_services,
    link_namespace_to_resourcequota_and_limitrange,
    link_networkpolicy_peers,
    link_networkpolicy_to_pods,
    link_owner_references,
    link_pod_to_configmap,
    link_pod_to_node,
    link_pod_to_pvc,
    link_pod_to_secret,
    link_pod_to_serviceaccount,
    link_pvc_to_pv,
    link_rolebinding_to_role_or_clusterrole,
    link_rolebinding_to_serviceaccount_subjects,
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


def test_link_pod_to_serviceaccount_explicit_name(make_pod, make_service_account):
    pod = make_pod("pod-1", "web", service_account_name="my-sa")
    sa = make_service_account("sa-1", "my-sa")
    other_sa = make_service_account("sa-2", "other-sa")

    edges = link_pod_to_serviceaccount({"Pod": [pod], "ServiceAccount": [sa, other_sa]})

    assert [(e.source, e.target, e.relation) for e in edges] == [("pod-1", "sa-1", "runs-as")]


def test_link_pod_to_serviceaccount_defaults_to_default_sa(make_pod, make_service_account):
    pod = make_pod("pod-1", "web")  # no service_account_name set
    default_sa = make_service_account("sa-1", "default")

    edges = link_pod_to_serviceaccount({"Pod": [pod], "ServiceAccount": [default_sa]})

    assert [(e.source, e.target, e.relation) for e in edges] == [("pod-1", "sa-1", "runs-as")]


def test_link_rolebinding_to_role(make_role_binding, make_role):
    binding = make_role_binding("rb-1", "view-binding", role_ref_kind="Role", role_ref_name="view")
    role = make_role("role-1", "view")
    unrelated_role = make_role("role-2", "other")

    edges = link_rolebinding_to_role_or_clusterrole(
        {"RoleBinding": [binding], "Role": [role, unrelated_role]}
    )

    assert [(e.source, e.target, e.relation) for e in edges] == [("rb-1", "role-1", "grants")]


def test_link_rolebinding_to_clusterrole(make_role_binding, make_cluster_role):
    binding = make_role_binding("rb-1", "view-binding", role_ref_kind="ClusterRole", role_ref_name="view")
    cluster_role = make_cluster_role("cr-1", "view")

    edges = link_rolebinding_to_role_or_clusterrole(
        {"RoleBinding": [binding], "ClusterRole": [cluster_role]}
    )

    assert [(e.source, e.target, e.relation) for e in edges] == [("rb-1", "cr-1", "grants")]


def test_link_rolebinding_to_role_no_match_is_silent(make_role_binding):
    binding = make_role_binding("rb-1", "view-binding", role_ref_kind="Role", role_ref_name="nope")

    edges = link_rolebinding_to_role_or_clusterrole({"RoleBinding": [binding], "Role": []})

    assert edges == []


def test_link_clusterrolebinding_to_clusterrole(make_cluster_role, make_cluster_role_binding):
    role = make_cluster_role("cr-1", "view")
    bound_binding = make_cluster_role_binding("crb-1", "view-binding", role_name="view")
    unrelated_binding = make_cluster_role_binding("crb-2", "other-binding", role_name="other-role")

    edges = link_clusterrolebinding_to_clusterrole(
        {"ClusterRole": [role], "ClusterRoleBinding": [bound_binding, unrelated_binding]}
    )

    assert [(e.source, e.target, e.relation) for e in edges] == [("crb-1", "cr-1", "grants")]


def test_link_rolebinding_to_serviceaccount_subjects(make_role_binding, make_service_account):
    binding = make_role_binding(
        "rb-1", "view-binding", role_ref_kind="Role",
        subjects=[k8s.RbacV1Subject(kind="ServiceAccount", name="my-sa", namespace="ns")],
    )
    sa = make_service_account("sa-1", "my-sa", namespace="ns")

    edges = link_rolebinding_to_serviceaccount_subjects({"RoleBinding": [binding], "ServiceAccount": [sa]})

    assert [(e.source, e.target, e.relation) for e in edges] == [("rb-1", "sa-1", "binds")]


def test_link_rolebinding_to_serviceaccount_subjects_defaults_to_binding_namespace(
    make_role_binding, make_service_account
):
    # A RoleBinding's ServiceAccount subject with no explicit namespace
    # implicitly refers to one in the binding's own namespace.
    binding = make_role_binding(
        "rb-1", "view-binding", namespace="ns", role_ref_kind="Role",
        subjects=[k8s.RbacV1Subject(kind="ServiceAccount", name="my-sa", namespace=None)],
    )
    sa = make_service_account("sa-1", "my-sa", namespace="ns")

    edges = link_rolebinding_to_serviceaccount_subjects({"RoleBinding": [binding], "ServiceAccount": [sa]})

    assert [(e.source, e.target, e.relation) for e in edges] == [("rb-1", "sa-1", "binds")]


def test_link_clusterrolebinding_to_serviceaccount_subjects_cross_namespace(
    make_cluster_role_binding, make_service_account
):
    binding = make_cluster_role_binding(
        "crb-1", "view-binding",
        subjects=[k8s.RbacV1Subject(kind="ServiceAccount", name="my-sa", namespace="other-ns")],
    )
    sa_in_other_ns = make_service_account("sa-1", "my-sa", namespace="other-ns")
    sa_in_default_ns = make_service_account("sa-2", "my-sa", namespace="ns")

    edges = link_clusterrolebinding_to_serviceaccount_subjects(
        {"ClusterRoleBinding": [binding], "ServiceAccount": [sa_in_other_ns, sa_in_default_ns]}
    )

    assert [(e.source, e.target, e.relation) for e in edges] == [("crb-1", "sa-1", "binds")]


def test_multiple_bindings_to_same_shared_clusterrole_each_get_a_grants_edge(
    make_cluster_role, make_cluster_role_binding
):
    # Regression test: a well-known built-in ClusterRole like
    # "system:auth-delegator" is commonly referenced by many unrelated
    # ClusterRoleBindings. This linker/module has no reachability filtering
    # of its own (that's krowser.graph.builder._reachable_uids's job, tested
    # separately) -- it should just correctly emit a "grants" edge for every
    # binding that references the shared role, not silently drop any.
    shared_role = make_cluster_role("cr-1", "system:auth-delegator")
    our_binding = make_cluster_role_binding("crb-1", "vault", role_name="system:auth-delegator")
    other_binding = make_cluster_role_binding("crb-2", "some-other-component", role_name="system:auth-delegator")

    edges = link_clusterrolebinding_to_clusterrole(
        {"ClusterRole": [shared_role], "ClusterRoleBinding": [our_binding, other_binding]}
    )

    assert {(e.source, e.target, e.relation) for e in edges} == {
        ("crb-1", "cr-1", "grants"),
        ("crb-2", "cr-1", "grants"),
    }


def test_link_hpa_to_deployment_target(make_hpa, make_deployment):
    hpa = make_hpa("hpa-1", "web", scale_target_kind="Deployment", scale_target_name="web")
    deploy = make_deployment("dep-1", "web")

    edges = link_hpa_to_target({"HorizontalPodAutoscaler": [hpa], "Deployment": [deploy]})

    assert [(e.source, e.target, e.relation) for e in edges] == [("hpa-1", "dep-1", "scales")]


def test_link_hpa_to_statefulset_target(make_hpa, make_stateful_set):
    hpa = make_hpa("hpa-1", "cache", scale_target_kind="StatefulSet", scale_target_name="cache")
    sts = make_stateful_set("sts-1", "cache")

    edges = link_hpa_to_target({"HorizontalPodAutoscaler": [hpa], "StatefulSet": [sts]})

    assert [(e.source, e.target, e.relation) for e in edges] == [("hpa-1", "sts-1", "scales")]


def test_link_hpa_to_target_no_match_is_silent(make_hpa):
    hpa = make_hpa("hpa-1", "web", scale_target_kind="Deployment", scale_target_name="nope")

    edges = link_hpa_to_target({"HorizontalPodAutoscaler": [hpa], "Deployment": []})

    assert edges == []


def test_networkpolicy_targets_pod_matching_labels(make_network_policy, make_pod):
    policy = make_network_policy(
        "np-1", "allow-web", pod_selector=k8s.V1LabelSelector(match_labels={"app": "web"})
    )
    matching_pod = make_pod("pod-1", "web-1", labels={"app": "web"})
    other_pod = make_pod("pod-2", "cache-1", labels={"app": "cache"})

    edges = link_networkpolicy_to_pods(
        {"NetworkPolicy": [policy], "Pod": [matching_pod, other_pod]}
    )

    assert [(e.source, e.target, e.relation) for e in edges] == [("np-1", "pod-1", "restricts")]


def test_networkpolicy_empty_selector_targets_every_pod_in_namespace(make_network_policy, make_pod):
    policy = make_network_policy("np-1", "deny-all", pod_selector=k8s.V1LabelSelector())
    pod_a = make_pod("pod-1", "a", labels={"app": "a"})
    pod_b = make_pod("pod-2", "b", labels={})

    edges = link_networkpolicy_to_pods({"NetworkPolicy": [policy], "Pod": [pod_a, pod_b]})

    assert {(e.source, e.target) for e in edges} == {("np-1", "pod-1"), ("np-1", "pod-2")}


def test_networkpolicy_does_not_target_pod_in_other_namespace(make_network_policy, make_pod):
    policy = make_network_policy(
        "np-1", "allow-web", namespace="ns-a", pod_selector=k8s.V1LabelSelector(match_labels={"app": "web"})
    )
    other_ns_pod = make_pod("pod-1", "web-1", namespace="ns-b", labels={"app": "web"})

    edges = link_networkpolicy_to_pods({"NetworkPolicy": [policy], "Pod": [other_ns_pod]})

    assert edges == []


def test_networkpolicy_match_expressions_in_and_not_in(make_network_policy, make_pod):
    policy = make_network_policy(
        "np-1",
        "allow-tier",
        pod_selector=k8s.V1LabelSelector(
            match_expressions=[
                k8s.V1LabelSelectorRequirement(key="tier", operator="In", values=["frontend", "backend"]),
                k8s.V1LabelSelectorRequirement(key="env", operator="NotIn", values=["dev"]),
            ]
        ),
    )
    matching_pod = make_pod("pod-1", "web-1", labels={"tier": "frontend", "env": "prod"})
    wrong_tier_pod = make_pod("pod-2", "db-1", labels={"tier": "database", "env": "prod"})
    dev_pod = make_pod("pod-3", "web-dev", labels={"tier": "frontend", "env": "dev"})

    edges = link_networkpolicy_to_pods(
        {"NetworkPolicy": [policy], "Pod": [matching_pod, wrong_tier_pod, dev_pod]}
    )

    assert [(e.source, e.target) for e in edges] == [("np-1", "pod-1")]


def test_networkpolicy_match_expressions_exists_and_does_not_exist(make_network_policy, make_pod):
    policy = make_network_policy(
        "np-1",
        "allow-labeled",
        pod_selector=k8s.V1LabelSelector(
            match_expressions=[
                k8s.V1LabelSelectorRequirement(key="monitored", operator="Exists"),
                k8s.V1LabelSelectorRequirement(key="excluded", operator="DoesNotExist"),
            ]
        ),
    )
    matching_pod = make_pod("pod-1", "web-1", labels={"monitored": "true"})
    missing_label_pod = make_pod("pod-2", "web-2", labels={})
    excluded_pod = make_pod("pod-3", "web-3", labels={"monitored": "true", "excluded": "true"})

    edges = link_networkpolicy_to_pods(
        {"NetworkPolicy": [policy], "Pod": [matching_pod, missing_label_pod, excluded_pod]}
    )

    assert [(e.source, e.target) for e in edges] == [("np-1", "pod-1")]


def test_networkpolicy_ingress_peer_podselector_same_namespace(make_network_policy, make_pod):
    policy = make_network_policy(
        "np-1",
        "allow-web",
        namespace="ns",
        ingress=[
            k8s.V1NetworkPolicyIngressRule(
                _from=[k8s.V1NetworkPolicyPeer(pod_selector=k8s.V1LabelSelector(match_labels={"role": "client"}))]
            )
        ],
    )
    client_pod = make_pod("pod-1", "client-1", namespace="ns", labels={"role": "client"})
    other_pod = make_pod("pod-2", "other-1", namespace="ns", labels={"role": "other"})

    edges = link_networkpolicy_peers({"NetworkPolicy": [policy], "Pod": [client_pod, other_pod]})

    assert [(e.source, e.target, e.relation) for e in edges] == [("np-1", "pod-1", "allows-from")]


def test_networkpolicy_egress_peer_podselector_produces_allows_to(make_network_policy, make_pod):
    policy = make_network_policy(
        "np-1",
        "allow-web",
        namespace="ns",
        egress=[
            k8s.V1NetworkPolicyEgressRule(
                to=[k8s.V1NetworkPolicyPeer(pod_selector=k8s.V1LabelSelector(match_labels={"role": "db"}))]
            )
        ],
    )
    db_pod = make_pod("pod-1", "db-1", namespace="ns", labels={"role": "db"})

    edges = link_networkpolicy_peers({"NetworkPolicy": [policy], "Pod": [db_pod]})

    assert [(e.source, e.target, e.relation) for e in edges] == [("np-1", "pod-1", "allows-to")]


def test_networkpolicy_podselector_peer_ignores_other_namespace(make_network_policy, make_pod):
    policy = make_network_policy(
        "np-1",
        "allow-web",
        namespace="ns-a",
        ingress=[
            k8s.V1NetworkPolicyIngressRule(
                _from=[k8s.V1NetworkPolicyPeer(pod_selector=k8s.V1LabelSelector(match_labels={"role": "client"}))]
            )
        ],
    )
    other_ns_pod = make_pod("pod-1", "client-1", namespace="ns-b", labels={"role": "client"})

    edges = link_networkpolicy_peers({"NetworkPolicy": [policy], "Pod": [other_ns_pod]})

    assert edges == []


def test_networkpolicy_namespaceselector_only_peer_matches_every_pod_in_namespace(
    make_network_policy, make_pod
):
    policy = make_network_policy(
        "np-1",
        "allow-kube-system",
        namespace="ns",
        ingress=[
            k8s.V1NetworkPolicyIngressRule(
                _from=[
                    k8s.V1NetworkPolicyPeer(
                        namespace_selector=k8s.V1LabelSelector(
                            match_labels={"kubernetes.io/metadata.name": "kube-system"}
                        )
                    )
                ]
            )
        ],
    )
    matching_pod_a = make_pod("pod-1", "coredns-1", namespace="kube-system", labels={})
    matching_pod_b = make_pod("pod-2", "coredns-2", namespace="kube-system", labels={})
    other_ns_pod = make_pod("pod-3", "app-1", namespace="ns", labels={})

    edges = link_networkpolicy_peers(
        {"NetworkPolicy": [policy], "Pod": [matching_pod_a, matching_pod_b, other_ns_pod]}
    )

    assert {(e.source, e.target) for e in edges} == {("np-1", "pod-1"), ("np-1", "pod-2")}


def test_networkpolicy_empty_namespaceselector_peer_matches_every_namespace(
    make_network_policy, make_pod
):
    policy = make_network_policy(
        "np-1",
        "allow-all-namespaces",
        namespace="ns",
        ingress=[
            k8s.V1NetworkPolicyIngressRule(
                _from=[k8s.V1NetworkPolicyPeer(namespace_selector=k8s.V1LabelSelector())]
            )
        ],
    )
    pod_a = make_pod("pod-1", "a", namespace="ns", labels={})
    pod_b = make_pod("pod-2", "b", namespace="other-ns", labels={})

    edges = link_networkpolicy_peers({"NetworkPolicy": [policy], "Pod": [pod_a, pod_b]})

    assert {(e.source, e.target) for e in edges} == {("np-1", "pod-1"), ("np-1", "pod-2")}


def test_networkpolicy_combined_podselector_and_namespaceselector_peer(make_network_policy, make_pod):
    policy = make_network_policy(
        "np-1",
        "allow-dns",
        namespace="ns",
        egress=[
            k8s.V1NetworkPolicyEgressRule(
                to=[
                    k8s.V1NetworkPolicyPeer(
                        namespace_selector=k8s.V1LabelSelector(
                            match_labels={"kubernetes.io/metadata.name": "kube-system"}
                        ),
                        pod_selector=k8s.V1LabelSelector(match_labels={"k8s-app": "kube-dns"}),
                    )
                ]
            )
        ],
    )
    coredns_pod = make_pod("pod-1", "coredns-1", namespace="kube-system", labels={"k8s-app": "kube-dns"})
    other_kube_system_pod = make_pod("pod-2", "other-1", namespace="kube-system", labels={"k8s-app": "other"})

    edges = link_networkpolicy_peers(
        {"NetworkPolicy": [policy], "Pod": [coredns_pod, other_kube_system_pod]}
    )

    assert [(e.source, e.target) for e in edges] == [("np-1", "pod-1")]


def test_networkpolicy_ipblock_peer_produces_no_edges(make_network_policy, make_pod):
    policy = make_network_policy(
        "np-1",
        "allow-cidr",
        namespace="ns",
        ingress=[
            k8s.V1NetworkPolicyIngressRule(
                _from=[k8s.V1NetworkPolicyPeer(ip_block=k8s.V1IPBlock(cidr="10.0.0.0/24"))]
            )
        ],
    )
    pod = make_pod("pod-1", "app-1", namespace="ns", labels={})

    edges = link_networkpolicy_peers({"NetworkPolicy": [policy], "Pod": [pod]})

    assert edges == []


def test_networkpolicy_rule_with_no_from_produces_no_peer_edges(make_network_policy, make_pod):
    # A rule with no `from`/`to` at all means "allow from/to anywhere" -- not
    # a specific peer, so there's nothing concrete to draw an edge to.
    policy = make_network_policy(
        "np-1", "allow-anywhere", namespace="ns", ingress=[k8s.V1NetworkPolicyIngressRule()]
    )
    pod = make_pod("pod-1", "app-1", namespace="ns", labels={})

    edges = link_networkpolicy_peers({"NetworkPolicy": [policy], "Pod": [pod]})

    assert edges == []


def test_networkpolicy_peer_matching_multiple_rules_dedupes_to_one_edge(make_network_policy, make_pod):
    policy = make_network_policy(
        "np-1",
        "allow-web-twice",
        namespace="ns",
        ingress=[
            k8s.V1NetworkPolicyIngressRule(
                _from=[k8s.V1NetworkPolicyPeer(pod_selector=k8s.V1LabelSelector(match_labels={"role": "client"}))]
            ),
            k8s.V1NetworkPolicyIngressRule(
                _from=[k8s.V1NetworkPolicyPeer(pod_selector=k8s.V1LabelSelector())]
            ),
        ],
    )
    pod = make_pod("pod-1", "client-1", namespace="ns", labels={"role": "client"})

    edges = link_networkpolicy_peers({"NetworkPolicy": [policy], "Pod": [pod]})

    assert [(e.source, e.target, e.relation) for e in edges] == [("np-1", "pod-1", "allows-from")]


def test_networkpolicy_namespaceselector_matches_real_custom_namespace_label(
    make_network_policy, make_pod, make_namespace
):
    # With a real Namespace object fetched (unlike the kubernetes.io/metadata.name-
    # only fallback), a namespaceSelector can match an arbitrary custom label,
    # not just the namespace's own name.
    policy = make_network_policy(
        "np-1",
        "allow-prod",
        namespace="ns",
        ingress=[
            k8s.V1NetworkPolicyIngressRule(
                _from=[
                    k8s.V1NetworkPolicyPeer(
                        namespace_selector=k8s.V1LabelSelector(match_labels={"env": "prod"})
                    )
                ]
            )
        ],
    )
    prod_ns = make_namespace("ns-a-uid", "team-a", labels={"env": "prod"})
    staging_ns = make_namespace("ns-b-uid", "team-b", labels={"env": "staging"})
    prod_pod = make_pod("pod-1", "app-1", namespace="team-a", labels={})
    staging_pod = make_pod("pod-2", "app-2", namespace="team-b", labels={})

    edges = link_networkpolicy_peers(
        {
            "NetworkPolicy": [policy],
            "Pod": [prod_pod, staging_pod],
            "Namespace": [prod_ns, staging_ns],
        }
    )

    assert [(e.source, e.target) for e in edges] == [("np-1", "pod-1")]


def test_networkpolicy_namespaceselector_falls_back_to_name_when_namespace_not_fetched(
    make_network_policy, make_pod
):
    # No "Namespace" key in world at all (e.g. a view that doesn't fetch it)
    # -- matching must still work via the kubernetes.io/metadata.name
    # convention, exactly as before Namespace objects were fetchable.
    policy = make_network_policy(
        "np-1",
        "allow-kube-system",
        namespace="ns",
        ingress=[
            k8s.V1NetworkPolicyIngressRule(
                _from=[
                    k8s.V1NetworkPolicyPeer(
                        namespace_selector=k8s.V1LabelSelector(
                            match_labels={"kubernetes.io/metadata.name": "kube-system"}
                        )
                    )
                ]
            )
        ],
    )
    pod = make_pod("pod-1", "coredns-1", namespace="kube-system", labels={})

    edges = link_networkpolicy_peers({"NetworkPolicy": [policy], "Pod": [pod]})

    assert [(e.source, e.target) for e in edges] == [("np-1", "pod-1")]


def test_link_namespace_to_resourcequota_and_limitrange(
    make_namespace, make_resource_quota, make_limit_range
):
    ns = make_namespace("ns-1", "team-a")
    other_ns = make_namespace("ns-2", "team-b")
    rq = make_resource_quota("rq-1", "compute-quota", namespace="team-a")
    other_rq = make_resource_quota("rq-2", "other-quota", namespace="team-b")
    lr = make_limit_range("lr-1", "defaults", namespace="team-a")

    edges = link_namespace_to_resourcequota_and_limitrange(
        {"Namespace": [ns, other_ns], "ResourceQuota": [rq, other_rq], "LimitRange": [lr]}
    )

    assert {(e.source, e.target, e.relation) for e in edges} == {
        ("ns-1", "rq-1", "owns"),
        ("ns-1", "lr-1", "owns"),
        ("ns-2", "rq-2", "owns"),
    }

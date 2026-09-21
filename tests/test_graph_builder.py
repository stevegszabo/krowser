from decimal import Decimal

from kubernetes import client as k8s

import krowser.graph.builder as builder_module
from krowser.graph.builder import GraphBuilder
from krowser.k8s.metrics import Usage


# Every Kind the builder might look up across any resource-type view. Tests only
# care about a few of these at a time, so _patch_fetchers defaults the rest to
# an empty list rather than requiring every test to enumerate the full set.
ALL_KINDS = [
    "Pod", "Service", "ConfigMap", "Secret", "PersistentVolumeClaim", "PersistentVolume",
    "Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "CronJob", "Ingress",
    "EndpointSlice", "Node", "ServiceAccount", "Role", "RoleBinding", "ClusterRole",
    "ClusterRoleBinding", "HorizontalPodAutoscaler", "NetworkPolicy",
]


def _patch_fetchers(monkeypatch, by_kind):
    combined = {kind: [] for kind in ALL_KINDS}
    combined.update(by_kind)
    monkeypatch.setattr(
        builder_module,
        "FETCHERS_BY_KIND",
        {kind: (lambda objs: (lambda mgr, context, namespace: objs))(objs) for kind, objs in combined.items()},
    )


def test_deployment_graph_excludes_unrelated_pods(monkeypatch, make_deployment, make_replica_set, make_pod):
    deploy = make_deployment("dep-1", "web")
    rs = make_replica_set(
        "rs-1", "web-abc", owner_refs=[k8s.V1OwnerReference(kind="Deployment", name="web", uid="dep-1", api_version="apps/v1")]
    )
    owned_pod = make_pod(
        "pod-1", "web-abc-xyz", owner_refs=[k8s.V1OwnerReference(kind="ReplicaSet", name="web-abc", uid="rs-1", api_version="apps/v1")]
    )
    unrelated_pod = make_pod("pod-2", "standalone")

    _patch_fetchers(
        monkeypatch,
        {"Deployment": [deploy], "ReplicaSet": [rs], "Pod": [owned_pod, unrelated_pod]},
    )

    graph = GraphBuilder(mgr=None).build("workloads/deployments", namespace="ns", context=None)

    node_ids = {n.id for n in graph.nodes}
    assert node_ids == {"dep-1", "rs-1", "pod-1"}
    assert "pod-2" not in node_ids
    assert graph.resource_count == 1
    assert graph.truncated is False


def test_deployment_graph_includes_used_configmap_secret_pvc_excludes_service_and_eps(
    monkeypatch,
    make_deployment,
    make_replica_set,
    make_pod,
    make_service,
    make_config_map,
    make_secret,
    make_pvc,
    make_endpoint_slice,
):
    deploy = make_deployment("dep-1", "web")
    rs = make_replica_set(
        "rs-1", "web-abc",
        owner_refs=[k8s.V1OwnerReference(kind="Deployment", name="web", uid="dep-1", api_version="apps/v1")],
    )
    pod = make_pod(
        "pod-1", "web-abc-xyz",
        owner_refs=[k8s.V1OwnerReference(kind="ReplicaSet", name="web-abc", uid="rs-1", api_version="apps/v1")],
        labels={"app": "web"},
        volumes=[
            k8s.V1Volume(name="cfg", config_map=k8s.V1ConfigMapVolumeSource(name="web-config")),
            k8s.V1Volume(
                name="data", persistent_volume_claim=k8s.V1PersistentVolumeClaimVolumeSource(claim_name="web-data")
            ),
        ],
        containers=[
            k8s.V1Container(
                name="app",
                image="nginx",
                env_from=[k8s.V1EnvFromSource(secret_ref=k8s.V1SecretEnvSource(name="web-secret"))],
            )
        ],
    )
    svc = make_service("svc-1", "web", selector={"app": "web"})
    used_cm = make_config_map("cm-1", "web-config")
    unrelated_cm = make_config_map("cm-2", "other-config")
    used_secret = make_secret("secret-1", "web-secret")
    used_pvc = make_pvc("pvc-1", "web-data")
    eps = make_endpoint_slice("eps-1", "web-abcde", service_name="web", pod_targets=[("web-abc-xyz", "pod-1", True)])

    _patch_fetchers(
        monkeypatch,
        {
            "Deployment": [deploy],
            "ReplicaSet": [rs],
            "Pod": [pod],
            "Service": [svc],
            "ConfigMap": [used_cm, unrelated_cm],
            "Secret": [used_secret],
            "PersistentVolumeClaim": [used_pvc],
            "EndpointSlice": [eps],
        },
    )

    graph = GraphBuilder(mgr=None).build("workloads/deployments", namespace="ns", context=None)

    # Service/EndpointSlice are fetched here (see _patch_fetchers above) but
    # must not appear -- this proves the Workloads expansion excludes them,
    # not merely that no data was available.
    node_ids = {n.id for n in graph.nodes}
    assert node_ids == {"dep-1", "rs-1", "pod-1", "cm-1", "secret-1", "pvc-1"}
    assert "cm-2" not in node_ids
    assert "svc-1" not in node_ids
    assert "eps-1" not in node_ids

    relations = {(e.source, e.target, e.relation) for e in graph.edges}
    assert ("pod-1", "cm-1", "uses") in relations
    assert ("pod-1", "secret-1", "uses") in relations
    assert ("pod-1", "pvc-1", "claims") in relations
    assert ("svc-1", "eps-1", "exposes") not in relations
    assert ("eps-1", "pod-1", "targets") not in relations


def test_shared_configmap_does_not_bridge_unrelated_pod_into_view(
    monkeypatch, make_deployment, make_replica_set, make_pod, make_config_map
):
    # Reproduces a real ArgoCD namespace: a Deployment's pod and a
    # StatefulSet's pod both mount the same ConfigMap (e.g.
    # argocd-cmd-params-cm). The StatefulSet itself isn't fetched for the
    # Deployments view, so its pod has no "owns" edge into this graph at
    # all -- the shared ConfigMap must not become a backdoor connection.
    deploy = make_deployment("dep-1", "web")
    rs = make_replica_set(
        "rs-1", "web-abc",
        owner_refs=[k8s.V1OwnerReference(kind="Deployment", name="web", uid="dep-1", api_version="apps/v1")],
    )
    deploy_pod = make_pod(
        "pod-1", "web-abc-xyz",
        owner_refs=[k8s.V1OwnerReference(kind="ReplicaSet", name="web-abc", uid="rs-1", api_version="apps/v1")],
        volumes=[k8s.V1Volume(name="cfg", config_map=k8s.V1ConfigMapVolumeSource(name="shared-cm"))],
    )
    unrelated_pod = make_pod(
        "pod-2", "other-0",
        owner_refs=[k8s.V1OwnerReference(kind="StatefulSet", name="other", uid="sts-1", api_version="apps/v1")],
        volumes=[k8s.V1Volume(name="cfg", config_map=k8s.V1ConfigMapVolumeSource(name="shared-cm"))],
    )
    shared_cm = make_config_map("cm-1", "shared-cm")

    _patch_fetchers(
        monkeypatch,
        {
            "Deployment": [deploy],
            "ReplicaSet": [rs],
            "Pod": [deploy_pod, unrelated_pod],
            "ConfigMap": [shared_cm],
        },
    )

    graph = GraphBuilder(mgr=None).build("workloads/deployments", namespace="ns", context=None)

    node_ids = {n.id for n in graph.nodes}
    assert node_ids == {"dep-1", "rs-1", "pod-1", "cm-1"}
    assert "pod-2" not in node_ids


def test_configmaps_have_no_edges(monkeypatch):
    cm = k8s.V1ConfigMap(
        metadata=k8s.V1ObjectMeta(uid="cm-1", name="cfg", namespace="ns"),
        data={"key": "value"},
    )
    _patch_fetchers(monkeypatch, {"ConfigMap": [cm]})

    graph = GraphBuilder(mgr=None).build("configmaps", namespace="ns", context=None)

    assert len(graph.nodes) == 1
    assert graph.edges == []


def test_truncation_caps_roots_and_reports_true_count(monkeypatch, make_deployment):
    from dataclasses import replace

    monkeypatch.setattr(builder_module, "settings", replace(builder_module.settings, max_graph_nodes=2))
    deployments = [make_deployment(f"dep-{i}", f"web-{i}") for i in range(5)]
    _patch_fetchers(monkeypatch, {"Deployment": deployments, "ReplicaSet": [], "Pod": []})

    graph = GraphBuilder(mgr=None).build("workloads/deployments", namespace="ns", context=None)

    assert graph.resource_count == 5
    assert graph.truncated is True
    assert len(graph.nodes) == 2


def test_services_root_includes_all_service_types(monkeypatch, make_service):
    lb = make_service("svc-lb", "lb-svc", svc_type="LoadBalancer")
    clusterip = make_service("svc-cip", "regular-svc", svc_type="ClusterIP")
    _patch_fetchers(monkeypatch, {"Service": [lb, clusterip], "Pod": []})

    graph = GraphBuilder(mgr=None).build("network/services", namespace="ns", context=None)

    assert {n.id for n in graph.nodes} == {"svc-lb", "svc-cip"}


def test_services_graph_includes_endpointslice_and_pod(monkeypatch, make_service, make_endpoint_slice, make_pod):
    svc = make_service("svc-1", "web", selector={"app": "web"})
    pod = make_pod("pod-1", "web-abc")
    unrelated_pod = make_pod("pod-2", "standalone")
    eps = make_endpoint_slice(
        "eps-1", "web-xyz", service_name="web", pod_targets=[("web-abc", "pod-1", True)]
    )
    _patch_fetchers(monkeypatch, {"Service": [svc], "EndpointSlice": [eps], "Pod": [pod, unrelated_pod]})

    graph = GraphBuilder(mgr=None).build("network/services", namespace="ns", context=None)

    assert {n.id for n in graph.nodes} == {"svc-1", "eps-1", "pod-1"}
    assert "pod-2" not in {n.id for n in graph.nodes}

    relations = {(e.source, e.target, e.relation) for e in graph.edges}
    assert ("svc-1", "eps-1", "exposes") in relations
    assert ("eps-1", "pod-1", "targets") in relations


def test_nodes_have_no_edges(monkeypatch, make_node):
    node_a = make_node("node-1", "worker-1")
    node_b = make_node("node-2", "worker-2", ready="False")
    _patch_fetchers(monkeypatch, {"Node": [node_a, node_b]})

    graph = GraphBuilder(mgr=None).build("cluster/nodes", namespace="ns", context=None)

    assert {n.id for n in graph.nodes} == {"node-1", "node-2"}
    assert graph.edges == []


def test_node_graph_includes_its_pods(monkeypatch, make_node, make_pod):
    node_a = make_node("node-1", "worker-1")
    node_b = make_node("node-2", "worker-2")
    pod_on_a = make_pod("pod-1", "web-a", node_name="worker-1")
    pod_on_b = make_pod("pod-2", "web-b", node_name="worker-2")
    _patch_fetchers(monkeypatch, {"Node": [node_a, node_b], "Pod": [pod_on_a, pod_on_b]})

    graph = GraphBuilder(mgr=None).build("cluster/nodes", namespace="ns", context=None)

    assert {n.id for n in graph.nodes} == {"node-1", "node-2", "pod-1", "pod-2"}
    relations = {(e.source, e.target, e.relation) for e in graph.edges}
    assert relations == {("pod-1", "node-1", "runs-on"), ("pod-2", "node-2", "runs-on")}


def test_node_graph_shows_single_edge_for_static_pod(monkeypatch, make_node, make_pod):
    node = make_node("node-1", "worker-1")
    static_pod = make_pod(
        "pod-1", "kube-apiserver-worker-1", namespace="kube-system", node_name="worker-1",
        owner_refs=[k8s.V1OwnerReference(kind="Node", name="worker-1", uid="node-1", api_version="v1")],
    )
    _patch_fetchers(monkeypatch, {"Node": [node], "Pod": [static_pod]})

    graph = GraphBuilder(mgr=None).build("cluster/nodes", namespace="ns", context=None)

    assert {(e.source, e.target, e.relation) for e in graph.edges} == {("node-1", "pod-1", "owns")}
    static_node = next(n for n in graph.nodes if n.id == "pod-1")
    assert static_node.is_static is True


def test_node_and_pod_tiles_include_usage_badge_from_metrics(monkeypatch, make_node, make_pod):
    node_obj = make_node("node-1", "worker-1")
    pod_obj = make_pod("pod-1", "web-a", namespace="ns", node_name="worker-1")
    _patch_fetchers(monkeypatch, {"Node": [node_obj], "Pod": [pod_obj]})
    monkeypatch.setattr(
        builder_module,
        "fetch_node_metrics",
        lambda mgr, context: {"worker-1": Usage(cpu_cores=Decimal("1.5"), memory_bytes=Decimal(2048 * 1024**2))},
    )
    monkeypatch.setattr(
        builder_module,
        "fetch_pod_metrics",
        lambda mgr, context, namespace: {
            ("ns", "web-a"): Usage(cpu_cores=Decimal("0.12"), memory_bytes=Decimal(340 * 1024**2))
        },
    )

    graph = GraphBuilder(mgr=None).build("cluster/nodes", namespace="ns", context=None)

    node_tile = next(n for n in graph.nodes if n.id == "node-1")
    pod_tile = next(n for n in graph.nodes if n.id == "pod-1")
    assert any(b.variant == "metrics" and b.text == "1500m / 2048Mi" for b in node_tile.badges)
    assert any(b.variant == "metrics" and b.text == "120m / 340Mi" for b in pod_tile.badges)


def test_persistent_volumes_filtered_to_namespace_bound_claim(monkeypatch, make_pv):
    pv_in_ns = make_pv("pv-1", "pv-1", claim_ref_namespace="ns", claim_ref_name="data")
    pv_other_ns = make_pv("pv-2", "pv-2", claim_ref_namespace="other", claim_ref_name="data")
    _patch_fetchers(
        monkeypatch, {"PersistentVolume": [pv_in_ns, pv_other_ns], "PersistentVolumeClaim": []}
    )

    graph = GraphBuilder(mgr=None).build("storage/persistentvolumes", namespace="ns", context=None)

    assert {n.id for n in graph.nodes} == {"pv-1"}


def test_persistent_volumes_all_namespaces_shows_everything(monkeypatch, make_pv):
    pv1 = make_pv("pv-1", "pv-1", claim_ref_namespace="ns", claim_ref_name="data")
    pv2 = make_pv("pv-2", "pv-2")
    _patch_fetchers(monkeypatch, {"PersistentVolume": [pv1, pv2], "PersistentVolumeClaim": []})

    graph = GraphBuilder(mgr=None).build("storage/persistentvolumes", namespace=None, context=None)

    assert {n.id for n in graph.nodes} == {"pv-1", "pv-2"}


def test_pod_graph_includes_serviceaccount_role_and_clusterrole(
    monkeypatch, make_pod, make_service_account, make_role, make_role_binding,
    make_cluster_role, make_cluster_role_binding,
):
    pod = make_pod("pod-1", "app", service_account_name="my-sa")
    sa = make_service_account("sa-1", "my-sa")
    unrelated_sa = make_service_account("sa-2", "other-sa")

    role = make_role("role-1", "view")
    role_binding = make_role_binding(
        "rb-1", "view-binding", role_ref_kind="Role", role_ref_name="view",
        subjects=[k8s.RbacV1Subject(kind="ServiceAccount", name="my-sa", namespace="ns")],
    )
    # Deliberately a different role name (not "view") and a different subject
    # SA, so it shares no node with the pod's own graph -- reusing "view"
    # here would legitimately bridge the two via the shared Role node.
    unrelated_role_binding = make_role_binding(
        "rb-2", "other-binding", role_ref_kind="Role", role_ref_name="unrelated-role",
        subjects=[k8s.RbacV1Subject(kind="ServiceAccount", name="other-sa", namespace="ns")],
    )

    cluster_role = make_cluster_role("cr-1", "cluster-view")
    cluster_role_binding = make_cluster_role_binding(
        "crb-1", "cluster-view-binding", role_name="cluster-view",
        subjects=[k8s.RbacV1Subject(kind="ServiceAccount", name="my-sa", namespace="ns")],
    )
    unrelated_cluster_role_binding = make_cluster_role_binding(
        "crb-2", "other-cluster-binding", role_name="unrelated-cluster-role",
        subjects=[k8s.RbacV1Subject(kind="ServiceAccount", name="other-sa", namespace="ns")],
    )

    _patch_fetchers(
        monkeypatch,
        {
            "Pod": [pod],
            "ServiceAccount": [sa, unrelated_sa],
            "Role": [role],
            "RoleBinding": [role_binding, unrelated_role_binding],
            "ClusterRole": [cluster_role],
            "ClusterRoleBinding": [cluster_role_binding, unrelated_cluster_role_binding],
        },
    )

    graph = GraphBuilder(mgr=None).build("workloads/pods", namespace="ns", context=None)

    node_ids = {n.id for n in graph.nodes}
    assert node_ids == {"pod-1", "sa-1", "role-1", "rb-1", "cr-1", "crb-1"}
    assert "sa-2" not in node_ids
    assert "rb-2" not in node_ids
    assert "crb-2" not in node_ids

    relations = {(e.source, e.target, e.relation) for e in graph.edges}
    assert ("pod-1", "sa-1", "runs-as") in relations
    assert ("rb-1", "role-1", "grants") in relations
    assert ("rb-1", "sa-1", "binds") in relations
    assert ("crb-1", "cr-1", "grants") in relations
    assert ("crb-1", "sa-1", "binds") in relations


def test_pod_graph_includes_matching_networkpolicy_excludes_non_matching(
    monkeypatch, make_pod, make_network_policy
):
    # workloads/pods fetches every Pod as a root, so pod-2 is always in the
    # graph regardless of NetworkPolicy -- what's under test here is that
    # np-1 (matching pod-1's labels) is pulled in via its "restricts" edge,
    # while np-2 (matching neither pod) is reachable from nothing and so
    # gets filtered out entirely, same as any other disconnected expansion node.
    pod = make_pod("pod-1", "web", labels={"app": "web"})
    other_pod = make_pod("pod-2", "cache", labels={"app": "cache"})
    matching_policy = make_network_policy(
        "np-1", "allow-web", pod_selector=k8s.V1LabelSelector(match_labels={"app": "web"})
    )
    non_matching_policy = make_network_policy(
        "np-2", "allow-db", pod_selector=k8s.V1LabelSelector(match_labels={"app": "db"})
    )

    _patch_fetchers(
        monkeypatch,
        {
            "Pod": [pod, other_pod],
            "NetworkPolicy": [matching_policy, non_matching_policy],
        },
    )

    graph = GraphBuilder(mgr=None).build("workloads/pods", namespace="ns", context=None)

    node_ids = {n.id for n in graph.nodes}
    assert node_ids == {"pod-1", "pod-2", "np-1"}
    assert "np-2" not in node_ids

    relations = {(e.source, e.target, e.relation) for e in graph.edges}
    assert ("np-1", "pod-1", "restricts") in relations


def test_shared_networkpolicy_does_not_bridge_unrelated_pod_into_view(
    monkeypatch, make_deployment, make_replica_set, make_pod, make_network_policy
):
    # Reproduces a real base-vault-style namespace: a namespace-wide
    # default-deny NetworkPolicy (empty podSelector) restricts every pod in
    # the namespace, including both a Deployment's pod and an unrelated
    # StatefulSet's pod. The StatefulSet itself isn't fetched for the
    # Deployments view, so its pod has no "owns" edge into this graph at all
    # -- the shared NetworkPolicy must not become a backdoor connection, the
    # same bug link_pod_to_configmap already guards against for ConfigMap.
    deploy = make_deployment("dep-1", "web")
    rs = make_replica_set(
        "rs-1", "web-abc",
        owner_refs=[k8s.V1OwnerReference(kind="Deployment", name="web", uid="dep-1", api_version="apps/v1")],
    )
    deploy_pod = make_pod(
        "pod-1", "web-abc-xyz",
        owner_refs=[k8s.V1OwnerReference(kind="ReplicaSet", name="web-abc", uid="rs-1", api_version="apps/v1")],
    )
    unrelated_pod = make_pod(
        "pod-2", "other-0",
        owner_refs=[k8s.V1OwnerReference(kind="StatefulSet", name="other", uid="sts-1", api_version="apps/v1")],
    )
    default_deny = make_network_policy("np-1", "default-deny-all", pod_selector=k8s.V1LabelSelector())

    _patch_fetchers(
        monkeypatch,
        {
            "Deployment": [deploy],
            "ReplicaSet": [rs],
            "Pod": [deploy_pod, unrelated_pod],
            "NetworkPolicy": [default_deny],
        },
    )

    graph = GraphBuilder(mgr=None).build("workloads/deployments", namespace="ns", context=None)

    node_ids = {n.id for n in graph.nodes}
    assert node_ids == {"dep-1", "rs-1", "pod-1", "np-1"}
    assert "pod-2" not in node_ids


def test_shared_builtin_clusterrole_does_not_bridge_unrelated_bindings(
    monkeypatch, make_pod, make_service_account, make_cluster_role, make_cluster_role_binding,
):
    # Regression test for a real bug: viewing a StatefulSet whose pod's
    # ServiceAccount is bound to a widely-shared built-in ClusterRole (e.g.
    # "system:auth-delegator") pulled in every *other* ClusterRoleBinding
    # that also references that same role, even though they name completely
    # unrelated ServiceAccounts -- reachability was flowing backward through
    # the shared ClusterRole node. Fixed by making that edge "grants"
    # (one-directional), not "binds" (bidirectional).
    pod = make_pod("pod-1", "vault-0", service_account_name="vault")
    sa = make_service_account("sa-1", "vault")
    other_sa_1 = make_service_account("sa-2", "some-controller")
    other_sa_2 = make_service_account("sa-3", "another-controller")
    other_sa_3 = make_service_account("sa-4", "yet-another-controller")

    shared_role = make_cluster_role("cr-1", "system:auth-delegator")
    our_binding = make_cluster_role_binding(
        "crb-1", "vault", role_name="system:auth-delegator",
        subjects=[k8s.RbacV1Subject(kind="ServiceAccount", name="vault", namespace="ns")],
    )
    other_binding_1 = make_cluster_role_binding(
        "crb-2", "some-controller", role_name="system:auth-delegator",
        subjects=[k8s.RbacV1Subject(kind="ServiceAccount", name="some-controller", namespace="ns")],
    )
    other_binding_2 = make_cluster_role_binding(
        "crb-3", "another-controller", role_name="system:auth-delegator",
        subjects=[k8s.RbacV1Subject(kind="ServiceAccount", name="another-controller", namespace="ns")],
    )
    other_binding_3 = make_cluster_role_binding(
        "crb-4", "yet-another-controller", role_name="system:auth-delegator",
        subjects=[k8s.RbacV1Subject(kind="ServiceAccount", name="yet-another-controller", namespace="ns")],
    )

    _patch_fetchers(
        monkeypatch,
        {
            "Pod": [pod],
            "ServiceAccount": [sa, other_sa_1, other_sa_2, other_sa_3],
            "ClusterRole": [shared_role],
            "ClusterRoleBinding": [our_binding, other_binding_1, other_binding_2, other_binding_3],
        },
    )

    graph = GraphBuilder(mgr=None).build("workloads/pods", namespace="ns", context=None)

    node_ids = {n.id for n in graph.nodes}
    assert node_ids == {"pod-1", "sa-1", "cr-1", "crb-1"}
    assert "crb-2" not in node_ids
    assert "crb-3" not in node_ids
    assert "crb-4" not in node_ids
    assert "sa-2" not in node_ids
    assert "sa-3" not in node_ids
    assert "sa-4" not in node_ids

from kubernetes import client as k8s

import krowser.graph.builder as builder_module
from krowser.graph.builder import GraphBuilder


# Every Kind the builder might look up across any resource-type view. Tests only
# care about a few of these at a time, so _patch_fetchers defaults the rest to
# an empty list rather than requiring every test to enumerate the full set.
ALL_KINDS = [
    "Pod", "Service", "ConfigMap", "Secret", "PersistentVolumeClaim", "PersistentVolume",
    "Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "CronJob", "Ingress",
    "EndpointSlice",
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


def test_deployment_graph_includes_used_service_configmap_secret_pvc(
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

    node_ids = {n.id for n in graph.nodes}
    assert node_ids == {"dep-1", "rs-1", "pod-1", "svc-1", "cm-1", "secret-1", "pvc-1", "eps-1"}
    assert "cm-2" not in node_ids

    relations = {(e.source, e.target, e.relation) for e in graph.edges}
    assert ("svc-1", "pod-1", "selects") in relations
    assert ("pod-1", "cm-1", "uses") in relations
    assert ("pod-1", "secret-1", "uses") in relations
    assert ("pod-1", "pvc-1", "claims") in relations
    assert ("svc-1", "eps-1", "exposes") in relations
    assert ("eps-1", "pod-1", "targets") in relations


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

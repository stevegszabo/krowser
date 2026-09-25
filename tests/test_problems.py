from kubernetes import client as k8s

import krowser.graph.builder as builder_module
from krowser.graph.problems import find_problems

# Every Kind find_problems() might look up while scanning every browsable
# type. Tests only care about a few of these at a time, so _patch_fetchers
# defaults the rest to an empty list rather than requiring every test to
# enumerate the full set -- same pattern as tests/test_graph_builder.py.
ALL_KINDS = [
    "Pod", "Service", "ConfigMap", "Secret", "PersistentVolumeClaim", "PersistentVolume",
    "Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "CronJob", "Ingress",
    "EndpointSlice", "Node", "ServiceAccount", "Role", "RoleBinding", "ClusterRole",
    "ClusterRoleBinding", "HorizontalPodAutoscaler", "Namespace", "NetworkPolicy",
]


def _patch_fetchers(monkeypatch, by_kind):
    combined = {kind: [] for kind in ALL_KINDS}
    combined.update(by_kind)
    monkeypatch.setattr(
        builder_module,
        "FETCHERS_BY_KIND",
        {kind: (lambda objs: (lambda mgr, context, namespace: objs))(objs) for kind, objs in combined.items()},
    )


def test_find_problems_includes_only_unhealthy_resources_across_kinds(
    monkeypatch, make_pod, make_pvc, make_job
):
    healthy_pod = make_pod("pod-1", "web-healthy", phase="Running")
    crashing_pod = make_pod("pod-2", "web-crash", phase="Running")
    crashing_pod.status.container_statuses[0].state = k8s.V1ContainerState(
        waiting=k8s.V1ContainerStateWaiting(reason="CrashLoopBackOff")
    )
    healthy_pvc = make_pvc("pvc-1", "data-1", phase="Bound")
    pending_pvc = make_pvc("pvc-2", "data-2", phase="Pending")
    healthy_job = make_job("job-1", "backup-ok", completions=1, succeeded=1)
    failed_job = make_job("job-2", "backup-bad", failed=7)

    _patch_fetchers(
        monkeypatch,
        {
            "Pod": [healthy_pod, crashing_pod],
            "PersistentVolumeClaim": [healthy_pvc, pending_pvc],
            "Job": [healthy_job, failed_job],
        },
    )

    graph = find_problems(mgr=None, context=None, namespace="ns")

    ids = {n.id for n in graph.nodes}
    assert ids == {"pod-2", "pvc-2", "job-2"}
    assert graph.edges == []
    assert graph.resource_count == len(graph.nodes)
    assert graph.truncated is False


def test_find_problems_excludes_kinds_with_no_status_concept(monkeypatch, make_config_map, make_secret):
    cm = make_config_map("cm-1", "settings")
    secret = make_secret("secret-1", "tls")
    _patch_fetchers(monkeypatch, {"ConfigMap": [cm], "Secret": [secret]})

    graph = find_problems(mgr=None, context=None, namespace="ns")

    # ConfigMap/Secret describers always report "unknown" health (no status
    # concept), so they can never appear here regardless of content.
    assert graph.nodes == []


def test_find_problems_reports_all_problem_nodes_as_root(monkeypatch, make_pod):
    crashing_pod = make_pod("pod-1", "web-crash", phase="Running")
    crashing_pod.status.container_statuses[0].state = k8s.V1ContainerState(
        waiting=k8s.V1ContainerStateWaiting(reason="CrashLoopBackOff")
    )
    _patch_fetchers(monkeypatch, {"Pod": [crashing_pod]})

    graph = find_problems(mgr=None, context=None, namespace="ns")

    assert len(graph.nodes) == 1
    assert graph.nodes[0].is_root is True

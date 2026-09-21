from typing import Any

from krowser.graph.builder import fetch_root
from krowser.graph.models import Graph, GraphNode
from krowser.k8s.client import KubeClientManager
from krowser.k8s.metrics import fetch_node_metrics, fetch_pod_metrics
from krowser.k8s.resource_types import ICONS_BY_KIND, RESOURCE_TYPES, PROBLEMS_TYPE_ID
from krowser.k8s.status import build_node

# "degraded" is an unambiguous failure (CrashLoopBackOff, a Failed Job, a Lost
# PV, ...) on every kind that has a status concept at all. "progressing" also
# counts -- e.g. a PVC stuck Pending, or a Deployment that never finishes
# rolling out -- since those are exactly the kind of "is something actually
# wrong" signal this view exists to surface, even though the same value also
# covers normal, quickly self-resolving transitions (a Pod briefly Pending).
# Kinds with no status concept at all (ConfigMap, Secret, ServiceAccount, ...)
# always report "unknown" and so never appear here without any extra filtering.
_PROBLEM_HEALTH = {"degraded", "progressing"}


def find_problems(mgr: KubeClientManager, context: str | None, namespace: str | None) -> Graph:
    """Cross-type scan for unhealthy resources across every browsable kind.

    Reuses the same fetch_root + build_node pipeline as a normal per-type
    graph build, but skips relationship/edge derivation entirely -- a flat
    aggregation across unrelated kinds has no meaningful graph to show -- and
    keeps only nodes whose health signals an actual problem, rather than
    every resource of every kind.
    """
    problems: list[GraphNode] = []
    for rt in RESOURCE_TYPES:
        if rt.id == PROBLEMS_TYPE_ID:
            continue

        objects: list[Any] = fetch_root(mgr, context, namespace, rt.id, rt)
        if not objects:
            continue

        icon = ICONS_BY_KIND.get(rt.kind, rt.icon)
        node_metrics = fetch_node_metrics(mgr, context) if rt.kind == "Node" else None
        pod_metrics = fetch_pod_metrics(mgr, context, namespace) if rt.kind == "Pod" else None

        for obj in objects:
            node = build_node(
                obj, rt.kind, icon, True, node_metrics=node_metrics, pod_metrics=pod_metrics
            )
            if node.health in _PROBLEM_HEALTH:
                problems.append(node)

    return Graph(nodes=problems, edges=[], resource_count=len(problems), truncated=False)

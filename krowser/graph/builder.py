from typing import Any

from krowser.config import settings
from krowser.graph.expansions import GRAPH_EXPANSIONS, GraphExpansion
from krowser.graph.models import Graph, GraphEdge, GraphNode
from krowser.graph.relationships import build_edges
from krowser.k8s.client import KubeClientManager
from krowser.k8s.fetchers import FETCHERS_BY_KIND
from krowser.k8s.metrics import fetch_node_metrics, fetch_pod_metrics
from krowser.k8s.resource_types import ICONS_BY_KIND, ResourceTypeSpec, get_resource_type
from krowser.k8s.status import build_node


def fetch_root(
    mgr: KubeClientManager, context: str | None, namespace: str | None, type_id: str, rt: ResourceTypeSpec
) -> list[Any]:
    if type_id == "storage/persistentvolumes" and namespace:
        # PersistentVolume is cluster-scoped; when a namespace is selected, filter
        # to PVs bound to a PVC in that namespace rather than ignoring the filter.
        pvs = FETCHERS_BY_KIND["PersistentVolume"](mgr, context, None)
        return [pv for pv in pvs if pv.spec.claim_ref and pv.spec.claim_ref.namespace == namespace]
    return FETCHERS_BY_KIND[rt.kind](mgr, context, namespace)


# "uses" (Pod -> ConfigMap/Secret) and "grants" (RoleBinding/ClusterRoleBinding
# -> Role/ClusterRole): forward-only. A ConfigMap/Secret or well-known
# built-in ClusterRole (e.g. "system:auth-delegator") is commonly shared
# across many unrelated objects in a namespace/cluster, so letting
# reachability flow *backward* out of the shared target would pull every
# other consumer into the graph -- e.g. a StatefulSet's pod showing up in a
# Deployments-only view just because both mount the same
# "argocd-cmd-params-cm". A pod can reach the ConfigMap/Secret it uses (and a
# binding the ClusterRole it grants), but that never flows back to every
# *other* consumer of the same shared target.
_FORWARD_ONLY_RELATIONS = {"uses", "grants"}

# "restricts" (NetworkPolicy -> Pod): backward-only, the mirror image of the
# above. A single broadly-scoped NetworkPolicy (e.g. an empty podSelector,
# which applies to every pod in the namespace) is commonly shared across many
# unrelated pods, so letting reachability flow *forward* from the policy to
# every pod it selects would bridge unrelated workloads into the same view --
# e.g. a StatefulSet's pod showing up in a Deployments-only view just because
# a namespace-wide default-deny policy selects both. A reachable pod can
# discover the policy that restricts it, but that policy never flows forward
# to every *other* pod it also restricts.
_BACKWARD_ONLY_RELATIONS = {"restricts"}

# "allows-from"/"allows-to" (NetworkPolicy -> peer Pod, an ingress/egress
# rule's own peer once resolved to a real fetched Pod): excluded from
# reachability in *both* directions, unlike every other relation above. A
# permissive rule (e.g. an ingress peer with an empty namespaceSelector,
# matching every pod in every namespace) is a hub on both the "what it
# restricts" and "what it allows" axes at once, so neither a forward-only nor
# a backward-only rule alone would stop it from bridging in every pod in the
# cluster the moment the policy became visible for any other reason. These
# edges never establish reachability either way; they still render (via the
# ordinary both-endpoints-already-reachable filter in build(), below) purely
# as extra context whenever the peer pod happens to already be visible for
# an unrelated reason -- e.g. a namespace-wide "view all Pods", where every
# pod in the namespace is already a root.
_NON_REACHABILITY_RELATIONS = {"allows-from", "allows-to"}


def _reachable_uids(root_uids: set[str], edges: list[GraphEdge]) -> set[str]:
    # Most relations are traversed in both directions, since either endpoint
    # legitimately wants to discover the other (e.g. a Pod should show which
    # Service exposes it) -- see _FORWARD_ONLY_RELATIONS/_BACKWARD_ONLY_RELATIONS/
    # _NON_REACHABILITY_RELATIONS above for the exceptions.
    adjacency: dict[str, set[str]] = {}
    for edge in edges:
        if edge.relation in _NON_REACHABILITY_RELATIONS:
            continue
        if edge.relation not in _BACKWARD_ONLY_RELATIONS:
            adjacency.setdefault(edge.source, set()).add(edge.target)
        if edge.relation not in _FORWARD_ONLY_RELATIONS:
            adjacency.setdefault(edge.target, set()).add(edge.source)

    visited = set(root_uids)
    frontier = list(root_uids)
    while frontier:
        current = frontier.pop()
        for neighbor in adjacency.get(current, ()):
            if neighbor not in visited:
                visited.add(neighbor)
                frontier.append(neighbor)
    return visited


class GraphBuilder:
    def __init__(self, mgr: KubeClientManager):
        self._mgr = mgr

    def build(self, type_id: str, namespace: str | None, context: str | None) -> Graph:
        rt = get_resource_type(type_id)
        expansion = GRAPH_EXPANSIONS.get(type_id, GraphExpansion(()))

        root_objects = fetch_root(self._mgr, context, namespace, type_id, rt)
        resource_count = len(root_objects)
        truncated = False
        if resource_count > settings.max_graph_nodes:
            truncated = True
            root_objects = sorted(root_objects, key=lambda o: o.metadata.name)[
                : settings.max_graph_nodes
            ]

        world: dict[str, list[Any]] = {rt.kind: root_objects}
        for kind in expansion.include:
            world.setdefault(kind, FETCHERS_BY_KIND[kind](self._mgr, context, namespace))

        root_uids = {obj.metadata.uid for obj in root_objects}

        # Best-effort usage overlay from the cluster's metrics-server, fetched
        # once per build and only when the graph actually contains that kind.
        node_metrics = fetch_node_metrics(self._mgr, context) if "Node" in world else None
        pod_metrics = fetch_pod_metrics(self._mgr, context, namespace) if "Pod" in world else None

        all_nodes: list[GraphNode] = []
        for kind, objs in world.items():
            icon = ICONS_BY_KIND.get(kind, "default")
            for obj in objs:
                all_nodes.append(
                    build_node(
                        obj,
                        kind,
                        icon,
                        obj.metadata.uid in root_uids,
                        node_metrics=node_metrics,
                        pod_metrics=pod_metrics,
                    )
                )

        all_edges = build_edges(world)

        # Only keep nodes actually connected to a root (directly or transitively) so
        # e.g. selecting "Deployments" shows the pods that belong to a deployment,
        # not every unrelated pod in the namespace that also happened to be fetched
        # as part of the expansion.
        reachable = _reachable_uids(root_uids, all_edges)
        nodes = [n for n in all_nodes if n.id in reachable]
        edges = [e for e in all_edges if e.source in reachable and e.target in reachable]

        return Graph(nodes=nodes, edges=edges, resource_count=resource_count, truncated=truncated)

from typing import Any

from krowser.config import settings
from krowser.graph.expansions import GRAPH_EXPANSIONS, GraphExpansion
from krowser.graph.models import Graph, GraphEdge, GraphNode
from krowser.graph.relationships import build_edges
from krowser.k8s.client import KubeClientManager
from krowser.k8s.custom_resources import list_custom_resources
from krowser.k8s.fetchers import FETCHERS_BY_KIND
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
    if rt.api_group is not None:
        # A dynamic custom-resource type: no FETCHERS_BY_KIND entry (Kind is
        # arbitrary), fetched generically via CustomObjectsApi instead. A
        # cluster-scoped CR ignores the namespace filter, same as Node/PV.
        ns = namespace if rt.namespaced else None
        return list_custom_resources(mgr, context, ns, rt.api_group, rt.version, rt.plural)
    return FETCHERS_BY_KIND[rt.kind](mgr, context, namespace)


def _reachable_uids(root_uids: set[str], edges: list[GraphEdge]) -> set[str]:
    # Most relations are traversed in both directions, since either endpoint
    # legitimately wants to discover the other (e.g. a Pod should show which
    # Service exposes it). "uses" (Pod -> ConfigMap/Secret) is the exception:
    # a ConfigMap/Secret is very commonly shared across many unrelated pods
    # in a namespace (a TLS cert, an injected CA bundle, common RBAC config),
    # so letting reachability flow *backward* out of one would pull every
    # other consumer of that same ConfigMap/Secret into the graph -- e.g. a
    # StatefulSet's pod showing up in a Deployments-only view just because
    # both mount the same "argocd-cmd-params-cm". A pod can reach the
    # ConfigMap/Secret it uses, but that never flows back out to other pods.
    adjacency: dict[str, set[str]] = {}
    for edge in edges:
        adjacency.setdefault(edge.source, set()).add(edge.target)
        if edge.relation != "uses":
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
        rt = get_resource_type(type_id, self._mgr, context)
        # Dynamic custom-resource types have no GRAPH_EXPANSIONS entry (their
        # id isn't known statically); they never show related kinds, same as
        # e.g. ConfigMaps.
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

        all_nodes: list[GraphNode] = []
        for kind, objs in world.items():
            # A dynamic custom-resource Kind has no ICONS_BY_KIND entry
            # (Kind is arbitrary); fall back to the resource type's own icon
            # ("crd") for its own root objects, and the generic default icon
            # for anything else (unreachable today: CR expansions are always
            # empty, but kept for safety if that ever changes).
            icon = ICONS_BY_KIND.get(kind, rt.icon if kind == rt.kind else "default")
            is_cr_root = kind == rt.kind and rt.api_group is not None
            for obj in objs:
                all_nodes.append(
                    build_node(
                        obj,
                        kind,
                        icon,
                        obj.metadata.uid in root_uids,
                        api_group=rt.api_group if is_cr_root else None,
                        api_version=rt.version if is_cr_root else None,
                        plural=rt.plural if is_cr_root else None,
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

from typing import Any

from krowser.config import settings
from krowser.graph.expansions import GRAPH_EXPANSIONS
from krowser.graph.models import Graph, GraphEdge, GraphNode
from krowser.graph.relationships import build_edges
from krowser.k8s.client import KubeClientManager
from krowser.k8s.fetchers import FETCHERS_BY_KIND
from krowser.k8s.resource_types import ICONS_BY_KIND, get_resource_type
from krowser.k8s.status import build_node


def fetch_root(
    mgr: KubeClientManager, context: str | None, namespace: str | None, type_id: str, kind: str
) -> list[Any]:
    if type_id == "storage/persistentvolumes" and namespace:
        # PersistentVolume is cluster-scoped; when a namespace is selected, filter
        # to PVs bound to a PVC in that namespace rather than ignoring the filter.
        pvs = FETCHERS_BY_KIND["PersistentVolume"](mgr, context, None)
        return [pv for pv in pvs if pv.spec.claim_ref and pv.spec.claim_ref.namespace == namespace]
    return FETCHERS_BY_KIND[kind](mgr, context, namespace)


def _reachable_uids(root_uids: set[str], edges: list[GraphEdge]) -> set[str]:
    adjacency: dict[str, set[str]] = {}
    for edge in edges:
        adjacency.setdefault(edge.source, set()).add(edge.target)
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
        expansion = GRAPH_EXPANSIONS[type_id]

        root_objects = fetch_root(self._mgr, context, namespace, type_id, rt.kind)
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
            icon = ICONS_BY_KIND[kind]
            for obj in objs:
                all_nodes.append(build_node(obj, kind, icon, obj.metadata.uid in root_uids))

        all_edges = build_edges(world)

        # Only keep nodes actually connected to a root (directly or transitively) so
        # e.g. selecting "Deployments" shows the pods that belong to a deployment,
        # not every unrelated pod in the namespace that also happened to be fetched
        # as part of the expansion.
        reachable = _reachable_uids(root_uids, all_edges)
        nodes = [n for n in all_nodes if n.id in reachable]
        edges = [e for e in all_edges if e.source in reachable and e.target in reachable]

        return Graph(nodes=nodes, edges=edges, resource_count=resource_count, truncated=truncated)

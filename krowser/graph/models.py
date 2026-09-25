from typing import Literal

from pydantic import BaseModel

Health = Literal["healthy", "progressing", "degraded", "suspended", "unknown"]
BadgeVariant = Literal["age", "status", "ready", "namespace", "misc", "metrics", "warning"]
Relation = Literal[
    "owns",
    "routes-to",
    "claims",
    "binds",
    "uses",
    "exposes",
    "targets",
    "runs-on",
    "runs-as",
    "grants",
    "scales",
    "restricts",
    "allows-from",
    "allows-to",
    "protects",
    "attaches",
]


class Badge(BaseModel):
    text: str
    variant: BadgeVariant


class GraphNode(BaseModel):
    id: str
    kind: str
    name: str
    namespace: str | None
    icon: str
    health: Health
    status_label: str
    age: str
    age_seconds: int
    ready: str | None
    badges: list[Badge]
    is_root: bool
    is_static: bool = False
    containers: list[str] = []
    # Set only for a custom resource instance (an arbitrary Kind from a CRD)
    # -- the CRD's own metadata.name (e.g. "virtualmachines.kubevirt.io"),
    # letting the frontend ask for this node's YAML without krowser needing a
    # static GETTERS_BY_KIND entry for every possible CRD Kind. See
    # krowser.k8s.custom_resources and the /api/resource-yaml route.
    crd: str | None = None


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    relation: Relation
    label: str | None = None


class Graph(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    resource_count: int
    truncated: bool

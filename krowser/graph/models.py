from typing import Literal

from pydantic import BaseModel

Health = Literal["healthy", "progressing", "degraded", "suspended", "unknown"]
BadgeVariant = Literal["age", "status", "ready", "namespace", "misc"]
Relation = Literal["owns", "routes-to", "claims", "binds", "uses", "exposes", "targets", "runs-on"]


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

"""Pure functions deriving GraphEdges from already-fetched raw Kubernetes objects.

Each function takes the full `world` (Kind name -> list of raw client objects,
e.g. V1Pod/V1Deployment/...) and looks up only the kinds it cares about via
`world.get(kind, [])`, so a linker is a safe no-op whenever its kinds weren't
fetched for the current selection. No I/O happens here, which is what makes
this module trivially unit-testable against hand-built fixture objects.
"""

from typing import Any

from krowser.graph.models import GraphEdge

World = dict[str, list[Any]]


def _all_objects(world: World):
    for objs in world.values():
        yield from objs


def link_owner_references(world: World) -> list[GraphEdge]:
    """Covers Deployment->ReplicaSet, ReplicaSet->Pod, DaemonSet->Pod,
    StatefulSet->Pod, CronJob->Job, Job->Pod uniformly, since Kubernetes sets
    ownerReferences the same way across all of these.

    EndpointSlice objects also carry a real ownerReference back to their
    Service, but that relationship already has its own, more descriptive
    edge (link_service_to_endpointslices, "exposes") -- so EndpointSlice is
    excluded here as a child to avoid a duplicate "owns" edge alongside it.
    """
    uid_index = {obj.metadata.uid for obj in _all_objects(world)}
    edges = []
    for kind, objs in world.items():
        if kind == "EndpointSlice":
            continue
        for obj in objs:
            for owner_ref in obj.metadata.owner_references or []:
                if owner_ref.uid in uid_index:
                    edges.append(
                        GraphEdge(
                            id=f"owns:{owner_ref.uid}:{obj.metadata.uid}",
                            source=owner_ref.uid,
                            target=obj.metadata.uid,
                            relation="owns",
                        )
                    )
    return edges


def link_ingress_to_services(world: World) -> list[GraphEdge]:
    services_by_key = {(s.metadata.namespace, s.metadata.name): s for s in world.get("Service", [])}
    edges = []
    for ing in world.get("Ingress", []):
        namespace = ing.metadata.namespace
        backend_names: set[str] = set()

        default_backend = ing.spec.default_backend if ing.spec else None
        if default_backend and default_backend.service:
            backend_names.add(default_backend.service.name)

        for rule in ing.spec.rules or []:
            if not rule.http:
                continue
            for path in rule.http.paths or []:
                if path.backend and path.backend.service:
                    backend_names.add(path.backend.service.name)

        for name in backend_names:
            svc = services_by_key.get((namespace, name))
            if svc:
                edges.append(
                    GraphEdge(
                        id=f"routes-to:{ing.metadata.uid}:{svc.metadata.uid}",
                        source=ing.metadata.uid,
                        target=svc.metadata.uid,
                        relation="routes-to",
                    )
                )
    return edges


def link_pod_to_pvc(world: World) -> list[GraphEdge]:
    pvcs_by_key = {
        (p.metadata.namespace, p.metadata.name): p for p in world.get("PersistentVolumeClaim", [])
    }
    edges = []
    for pod in world.get("Pod", []):
        for volume in pod.spec.volumes or []:
            claim = volume.persistent_volume_claim
            if not claim:
                continue
            pvc = pvcs_by_key.get((pod.metadata.namespace, claim.claim_name))
            if pvc:
                edges.append(
                    GraphEdge(
                        id=f"claims:{pod.metadata.uid}:{pvc.metadata.uid}",
                        source=pod.metadata.uid,
                        target=pvc.metadata.uid,
                        relation="claims",
                    )
                )
    return edges


def _pod_containers(pod: Any):
    return list(pod.spec.containers or []) + list(pod.spec.init_containers or [])


def _referenced_config_map_names(pod: Any) -> set[str]:
    names: set[str] = set()
    for volume in pod.spec.volumes or []:
        if volume.config_map:
            names.add(volume.config_map.name)
    for container in _pod_containers(pod):
        for env_from in container.env_from or []:
            if env_from.config_map_ref:
                names.add(env_from.config_map_ref.name)
        for env in container.env or []:
            if env.value_from and env.value_from.config_map_key_ref:
                names.add(env.value_from.config_map_key_ref.name)
    return names


def _referenced_secret_names(pod: Any) -> set[str]:
    names: set[str] = set()
    for volume in pod.spec.volumes or []:
        if volume.secret:
            names.add(volume.secret.secret_name)
    for container in _pod_containers(pod):
        for env_from in container.env_from or []:
            if env_from.secret_ref:
                names.add(env_from.secret_ref.name)
        for env in container.env or []:
            if env.value_from and env.value_from.secret_key_ref:
                names.add(env.value_from.secret_key_ref.name)
    for pull_secret in pod.spec.image_pull_secrets or []:
        names.add(pull_secret.name)
    return names


def link_pod_to_configmap(world: World) -> list[GraphEdge]:
    configmaps_by_key = {
        (cm.metadata.namespace, cm.metadata.name): cm for cm in world.get("ConfigMap", [])
    }
    edges = []
    for pod in world.get("Pod", []):
        for name in _referenced_config_map_names(pod):
            cm = configmaps_by_key.get((pod.metadata.namespace, name))
            if cm:
                edges.append(
                    GraphEdge(
                        id=f"uses:{pod.metadata.uid}:{cm.metadata.uid}",
                        source=pod.metadata.uid,
                        target=cm.metadata.uid,
                        relation="uses",
                    )
                )
    return edges


def link_pod_to_secret(world: World) -> list[GraphEdge]:
    secrets_by_key = {
        (s.metadata.namespace, s.metadata.name): s for s in world.get("Secret", [])
    }
    edges = []
    for pod in world.get("Pod", []):
        for name in _referenced_secret_names(pod):
            secret = secrets_by_key.get((pod.metadata.namespace, name))
            if secret:
                edges.append(
                    GraphEdge(
                        id=f"uses:{pod.metadata.uid}:{secret.metadata.uid}",
                        source=pod.metadata.uid,
                        target=secret.metadata.uid,
                        relation="uses",
                    )
                )
    return edges


def link_pvc_to_pv(world: World) -> list[GraphEdge]:
    pvs_by_name = {pv.metadata.name: pv for pv in world.get("PersistentVolume", [])}
    edges = []
    for pvc in world.get("PersistentVolumeClaim", []):
        if pvc.status.phase != "Bound" or not pvc.spec.volume_name:
            continue
        pv = pvs_by_name.get(pvc.spec.volume_name)
        if pv:
            edges.append(
                GraphEdge(
                    id=f"binds:{pvc.metadata.uid}:{pv.metadata.uid}",
                    source=pvc.metadata.uid,
                    target=pv.metadata.uid,
                    relation="binds",
                )
            )
    return edges


SERVICE_NAME_LABEL = "kubernetes.io/service-name"


def link_service_to_endpointslices(world: World) -> list[GraphEdge]:
    """EndpointSlices declare their owning Service via the well-known
    'kubernetes.io/service-name' label (discovery.k8s.io convention), rather
    than an ownerReference, so this needs its own linker."""
    services_by_key = {(s.metadata.namespace, s.metadata.name): s for s in world.get("Service", [])}
    edges = []
    for eps in world.get("EndpointSlice", []):
        service_name = (eps.metadata.labels or {}).get(SERVICE_NAME_LABEL)
        if not service_name:
            continue
        svc = services_by_key.get((eps.metadata.namespace, service_name))
        if svc:
            edges.append(
                GraphEdge(
                    id=f"exposes:{svc.metadata.uid}:{eps.metadata.uid}",
                    source=svc.metadata.uid,
                    target=eps.metadata.uid,
                    relation="exposes",
                )
            )
    return edges


def link_endpointslice_to_pods(world: World) -> list[GraphEdge]:
    """Each EndpointSlice endpoint carries a targetRef with the backing Pod's
    UID directly, so this matches by UID rather than name (like
    link_owner_references) instead of re-deriving selector matching."""
    pod_uids = {p.metadata.uid for p in world.get("Pod", [])}
    edges = []
    for eps in world.get("EndpointSlice", []):
        for endpoint in eps.endpoints or []:
            target_ref = endpoint.target_ref
            if target_ref and target_ref.kind == "Pod" and target_ref.uid in pod_uids:
                edges.append(
                    GraphEdge(
                        id=f"targets:{eps.metadata.uid}:{target_ref.uid}",
                        source=eps.metadata.uid,
                        target=target_ref.uid,
                        relation="targets",
                    )
                )
    return edges


LINKERS = [
    link_owner_references,
    link_ingress_to_services,
    link_pod_to_pvc,
    link_pvc_to_pv,
    link_pod_to_configmap,
    link_pod_to_secret,
    link_service_to_endpointslices,
    link_endpointslice_to_pods,
]


def build_edges(world: World) -> list[GraphEdge]:
    return [edge for linker in LINKERS for edge in linker(world)]

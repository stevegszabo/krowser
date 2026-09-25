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


def link_volumeattachment_to_pv(world: World) -> list[GraphEdge]:
    """A VolumeAttachment references its PersistentVolume by name via
    spec.source.persistentVolumeName (there is exactly one PV per
    attachment, so -- unlike NetworkPolicy/PodDisruptionBudget's
    selector-based many-to-one matching -- there's no shared-hub bridging
    risk here; an ordinary bidirectional relation is fine)."""
    pvs_by_name = {pv.metadata.name: pv for pv in world.get("PersistentVolume", [])}
    edges = []
    for va in world.get("VolumeAttachment", []):
        pv_name = va.spec.source.persistent_volume_name
        pv = pvs_by_name.get(pv_name) if pv_name else None
        if pv:
            edges.append(
                GraphEdge(
                    id=f"attaches:{va.metadata.uid}:{pv.metadata.uid}",
                    source=va.metadata.uid,
                    target=pv.metadata.uid,
                    relation="attaches",
                )
            )
    return edges


def link_pod_to_node(world: World) -> list[GraphEdge]:
    """Static/mirror pods (e.g. kube-apiserver on a control-plane node) carry
    a real ownerReference back to their Node, which link_owner_references
    already turns into an "owns" edge -- skip those here to avoid a second,
    duplicate edge between the same Node and Pod."""
    nodes_by_name = {n.metadata.name: n for n in world.get("Node", [])}
    edges = []
    for pod in world.get("Pod", []):
        node_name = pod.spec.node_name
        if not node_name:
            continue
        node = nodes_by_name.get(node_name)
        if not node:
            continue
        owned_by_node = any(
            ref.uid == node.metadata.uid for ref in (pod.metadata.owner_references or [])
        )
        if owned_by_node:
            continue
        edges.append(
            GraphEdge(
                id=f"runs-on:{pod.metadata.uid}:{node.metadata.uid}",
                source=pod.metadata.uid,
                target=node.metadata.uid,
                relation="runs-on",
            )
        )
    return edges


def link_pod_to_serviceaccount(world: World) -> list[GraphEdge]:
    """Every pod has an effective ServiceAccount even when spec.serviceAccountName
    is unset (it implicitly runs as "default" in its namespace), so that's the
    key used to match here rather than skipping pods with no explicit name."""
    service_accounts_by_key = {
        (sa.metadata.namespace, sa.metadata.name): sa for sa in world.get("ServiceAccount", [])
    }
    edges = []
    for pod in world.get("Pod", []):
        sa_name = pod.spec.service_account_name or "default"
        sa = service_accounts_by_key.get((pod.metadata.namespace, sa_name))
        if sa:
            edges.append(
                GraphEdge(
                    id=f"runs-as:{pod.metadata.uid}:{sa.metadata.uid}",
                    source=pod.metadata.uid,
                    target=sa.metadata.uid,
                    relation="runs-as",
                )
            )
    return edges


def link_rolebinding_to_role_or_clusterrole(world: World) -> list[GraphEdge]:
    """A RoleBinding references the Role/ClusterRole it grants via
    roleRef.kind + roleRef.name, not an ownerReference -- and unlike a
    ClusterRoleBinding (always roleRef.kind == "ClusterRole"), a RoleBinding
    can point at either, so both pools are checked here."""
    roles_by_key = {(r.metadata.namespace, r.metadata.name): r for r in world.get("Role", [])}
    cluster_roles_by_name = {r.metadata.name: r for r in world.get("ClusterRole", [])}
    edges = []
    for binding in world.get("RoleBinding", []):
        role_ref = binding.role_ref
        if role_ref.kind == "Role":
            target = roles_by_key.get((binding.metadata.namespace, role_ref.name))
        elif role_ref.kind == "ClusterRole":
            target = cluster_roles_by_name.get(role_ref.name)
        else:
            target = None
        if target:
            edges.append(
                GraphEdge(
                    id=f"grants:{binding.metadata.uid}:{target.metadata.uid}",
                    source=binding.metadata.uid,
                    target=target.metadata.uid,
                    relation="grants",
                )
            )
    return edges


def link_clusterrolebinding_to_clusterrole(world: World) -> list[GraphEdge]:
    """A ClusterRoleBinding references the ClusterRole it grants via
    roleRef.name, not an ownerReference, so this needs its own linker
    (unlike Deployment->ReplicaSet->Pod etc., handled generically above)."""
    roles_by_name = {r.metadata.name: r for r in world.get("ClusterRole", [])}
    edges = []
    for binding in world.get("ClusterRoleBinding", []):
        role_ref = binding.role_ref
        if role_ref.kind != "ClusterRole":
            continue
        role = roles_by_name.get(role_ref.name)
        if role:
            edges.append(
                GraphEdge(
                    id=f"grants:{binding.metadata.uid}:{role.metadata.uid}",
                    source=binding.metadata.uid,
                    target=role.metadata.uid,
                    relation="grants",
                )
            )
    return edges


def _serviceaccount_subject_edges(bindings: list[Any], world: World) -> list[GraphEdge]:
    service_accounts_by_key = {
        (sa.metadata.namespace, sa.metadata.name): sa for sa in world.get("ServiceAccount", [])
    }
    edges = []
    for binding in bindings:
        for subject in binding.subjects or []:
            if subject.kind != "ServiceAccount":
                continue
            namespace = subject.namespace or binding.metadata.namespace
            sa = service_accounts_by_key.get((namespace, subject.name))
            if sa:
                edges.append(
                    GraphEdge(
                        id=f"binds:{binding.metadata.uid}:{sa.metadata.uid}",
                        source=binding.metadata.uid,
                        target=sa.metadata.uid,
                        relation="binds",
                    )
                )
    return edges


def link_rolebinding_to_serviceaccount_subjects(world: World) -> list[GraphEdge]:
    """A RoleBinding also "binds" its Subjects (who the grant applies to),
    not just the Role/ClusterRole -- of those, only ServiceAccount subjects
    are ever fetched into the graph (User/Group aren't real cluster objects
    krowser can look up)."""
    return _serviceaccount_subject_edges(world.get("RoleBinding", []), world)


def link_clusterrolebinding_to_serviceaccount_subjects(world: World) -> list[GraphEdge]:
    """Same as link_rolebinding_to_serviceaccount_subjects, above, for
    ClusterRoleBinding's subjects."""
    return _serviceaccount_subject_edges(world.get("ClusterRoleBinding", []), world)


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


def _selector_matches(selector: Any, labels: dict[str, str]) -> bool:
    """A LabelSelector matcher (matchLabels + matchExpressions). Unlike every
    other linker in this module -- which follows a reference Kubernetes
    itself already resolved (an ownerReference, a roleRef, an EndpointSlice's
    targetRef mirroring Service's own selector-derived membership) -- a
    NetworkPolicy's podSelector has no equivalent controller-computed
    "resolved membership" object to piggyback on, so this is the one place
    selector matching has to be implemented directly. An empty selector
    (no matchLabels and no matchExpressions) matches every pod in the
    namespace, per the NetworkPolicy spec.
    """
    if selector is None:
        return False
    for key, value in (selector.match_labels or {}).items():
        if labels.get(key) != value:
            return False
    for expr in selector.match_expressions or []:
        values = expr.values or []
        if expr.operator == "In":
            if labels.get(expr.key) not in values:
                return False
        elif expr.operator == "NotIn":
            if labels.get(expr.key) in values:
                return False
        elif expr.operator == "Exists":
            if expr.key not in labels:
                return False
        elif expr.operator == "DoesNotExist":
            if expr.key in labels:
                return False
    return True


def link_networkpolicy_to_pods(world: World) -> list[GraphEdge]:
    """A NetworkPolicy applies to whichever pods in its own namespace match
    its spec.podSelector -- see _selector_matches for why this is matched
    directly rather than following a pre-resolved reference.

    Uses its own "restricts" relation rather than reusing "targets"
    (EndpointSlice -> Pod) because the two need opposite reachability
    treatment in builder.py's _reachable_uids: a broadly-scoped policy (e.g.
    an empty podSelector matching every pod in the namespace) is exactly the
    kind of many-to-one hub that must not bridge unrelated pods into the same
    view, the same problem "uses"/"grants" solve for ConfigMap/Secret and
    ClusterRole -- just with the hub on the *source* side of the edge here
    instead of the target side.
    """
    edges = []
    for policy in world.get("NetworkPolicy", []):
        for pod in world.get("Pod", []):
            if pod.metadata.namespace != policy.metadata.namespace:
                continue
            if _selector_matches(policy.spec.pod_selector, pod.metadata.labels or {}):
                edges.append(
                    GraphEdge(
                        id=f"restricts:{policy.metadata.uid}:{pod.metadata.uid}",
                        source=policy.metadata.uid,
                        target=pod.metadata.uid,
                        relation="restricts",
                    )
                )
    return edges


def _namespace_labels_by_name(world: World) -> dict[str, dict[str, str]]:
    """Real Namespace objects (fetched as internal lookup data alongside
    NetworkPolicy -- see NETWORK_POLICY_RELATED_KINDS) already carry the
    well-known `kubernetes.io/metadata.name` label Kubernetes auto-populates,
    plus whatever custom labels a namespace actually has. Falling back to a
    synthetic single-key dict when a namespace's real object isn't in `world`
    (some views don't fetch Namespace at all) preserves the old
    name-only-matching behavior exactly in those cases."""
    return {ns.metadata.name: (ns.metadata.labels or {}) for ns in world.get("Namespace", [])}


def _peer_matches_pod(
    peer: Any, pod: Any, policy_namespace: str, namespace_labels_by_name: dict[str, dict[str, str]]
) -> bool:
    """Resolves one NetworkPolicyPeer (an ingress rule's `from` entry or an
    egress rule's `to` entry) against a candidate Pod.

    ipBlock peers are skipped entirely -- an external CIDR has no
    Kubernetes object a graph edge could point at. A namespaceSelector is
    matched against the pod's own namespace's real labels when its Namespace
    object was fetched (see _namespace_labels_by_name), falling back to just
    the well-known `kubernetes.io/metadata.name` label otherwise -- correct
    for the common "select this namespace by name" pattern even without the
    real object, but unable to match a custom namespace label in that case.
    """
    if peer.ip_block is not None:
        return False
    if peer.namespace_selector is not None:
        namespace_labels = namespace_labels_by_name.get(
            pod.metadata.namespace, {"kubernetes.io/metadata.name": pod.metadata.namespace}
        )
        if not _selector_matches(peer.namespace_selector, namespace_labels):
            return False
        # A namespaceSelector with no podSelector alongside it matches every
        # pod in the matched namespace(s).
        if peer.pod_selector is None:
            return True
        return _selector_matches(peer.pod_selector, pod.metadata.labels or {})
    if peer.pod_selector is not None:
        # No namespaceSelector: a bare podSelector only ever applies within
        # the NetworkPolicy's own namespace.
        return pod.metadata.namespace == policy_namespace and _selector_matches(
            peer.pod_selector, pod.metadata.labels or {}
        )
    return False


def link_networkpolicy_peers(world: World) -> list[GraphEdge]:
    """Resolves each NetworkPolicy's ingress `from` / egress `to` peers
    against pods already present in `world`, so a policy shows not just that
    it restricts a pod but what traffic it actually allows.

    Deliberately excluded from reachability entirely in builder.py's
    _reachable_uids (neither forward- nor backward-only, unlike every other
    special-cased relation) -- a permissive rule (e.g. an ingress peer with
    an empty namespaceSelector, matching every pod in every namespace) would
    otherwise bridge every pod in the cluster into any view the policy
    happens to appear in, the instant that rule got evaluated. These edges
    only ever render when both the policy and the peer pod are already
    reachable for some other, unrelated reason (e.g. namespace-wide "view
    all Pods", where every pod in the namespace is already a root).
    """
    edges: list[GraphEdge] = []
    seen: set[tuple[str, str, str]] = set()
    namespace_labels_by_name = _namespace_labels_by_name(world)

    def _add(policy: Any, pod: Any, relation: str) -> None:
        key = (policy.metadata.uid, pod.metadata.uid, relation)
        if key in seen:
            return
        seen.add(key)
        edges.append(
            GraphEdge(
                id=f"{relation}:{policy.metadata.uid}:{pod.metadata.uid}",
                source=policy.metadata.uid,
                target=pod.metadata.uid,
                relation=relation,
            )
        )

    for policy in world.get("NetworkPolicy", []):
        for rule in policy.spec.ingress or []:
            for peer in rule._from or []:
                for pod in world.get("Pod", []):
                    if _peer_matches_pod(peer, pod, policy.metadata.namespace, namespace_labels_by_name):
                        _add(policy, pod, "allows-from")
        for rule in policy.spec.egress or []:
            for peer in rule.to or []:
                for pod in world.get("Pod", []):
                    if _peer_matches_pod(peer, pod, policy.metadata.namespace, namespace_labels_by_name):
                        _add(policy, pod, "allows-to")
    return edges


def link_poddisruptionbudget_to_pods(world: World) -> list[GraphEdge]:
    """A PodDisruptionBudget applies to whichever pods in its own namespace
    match its spec.selector -- the same shape and matching rules as
    NetworkPolicy's podSelector (see _selector_matches), and the same
    reachability concern applies: a broadly-scoped PDB (e.g. an empty
    selector matching every pod in the namespace) is a many-to-one hub that
    must not bridge unrelated pods together on a Pod/Deployment/etc. view,
    so this uses its own "protects" relation (backward-only, like
    "restricts") rather than an ordinary bidirectional one.
    """
    edges = []
    for pdb in world.get("PodDisruptionBudget", []):
        for pod in world.get("Pod", []):
            if pod.metadata.namespace != pdb.metadata.namespace:
                continue
            if _selector_matches(pdb.spec.selector, pod.metadata.labels or {}):
                edges.append(
                    GraphEdge(
                        id=f"protects:{pdb.metadata.uid}:{pod.metadata.uid}",
                        source=pdb.metadata.uid,
                        target=pod.metadata.uid,
                        relation="protects",
                    )
                )
    return edges


def link_hpa_to_target(world: World) -> list[GraphEdge]:
    """An HPA references its scale target via spec.scaleTargetRef
    {kind, name}, not an ownerReference. Looked up generically over whatever
    kind is actually present in `world` (Deployment/StatefulSet today)
    rather than hardcoding either, so this keeps working unmodified if a
    future expansion ever fetches a third scalable kind."""
    by_kind_key = {
        (kind, obj.metadata.namespace, obj.metadata.name): obj
        for kind, objs in world.items()
        for obj in objs
    }
    edges = []
    for hpa in world.get("HorizontalPodAutoscaler", []):
        ref = hpa.spec.scale_target_ref
        target = by_kind_key.get((ref.kind, hpa.metadata.namespace, ref.name))
        if target:
            edges.append(
                GraphEdge(
                    id=f"scales:{hpa.metadata.uid}:{target.metadata.uid}",
                    source=hpa.metadata.uid,
                    target=target.metadata.uid,
                    relation="scales",
                )
            )
    return edges


def link_namespace_to_resourcequota_and_limitrange(world: World) -> list[GraphEdge]:
    """ResourceQuota/LimitRange declare their namespace via metadata.namespace
    (a plain field, like every namespaced object), not an ownerReference or a
    label, so this matches by name rather than following a reference --
    reusing "owns" for the relation label since, unlike ConfigMap/ClusterRole,
    a ResourceQuota/LimitRange is never shared across namespaces, so there's
    no bridging risk in leaving it bidirectional like most other relations."""
    edges = []
    for ns in world.get("Namespace", []):
        for kind in ("ResourceQuota", "LimitRange"):
            for obj in world.get(kind, []):
                if obj.metadata.namespace == ns.metadata.name:
                    edges.append(
                        GraphEdge(
                            id=f"owns:{ns.metadata.uid}:{obj.metadata.uid}",
                            source=ns.metadata.uid,
                            target=obj.metadata.uid,
                            relation="owns",
                        )
                    )
    return edges


LINKERS = [
    link_owner_references,
    link_ingress_to_services,
    link_pod_to_pvc,
    link_pvc_to_pv,
    link_volumeattachment_to_pv,
    link_pod_to_node,
    link_pod_to_configmap,
    link_pod_to_secret,
    link_service_to_endpointslices,
    link_endpointslice_to_pods,
    link_pod_to_serviceaccount,
    link_rolebinding_to_role_or_clusterrole,
    link_clusterrolebinding_to_clusterrole,
    link_rolebinding_to_serviceaccount_subjects,
    link_clusterrolebinding_to_serviceaccount_subjects,
    link_networkpolicy_to_pods,
    link_networkpolicy_peers,
    link_namespace_to_resourcequota_and_limitrange,
    link_hpa_to_target,
    link_poddisruptionbudget_to_pods,
]


def build_edges(world: World) -> list[GraphEdge]:
    return [edge for linker in LINKERS for edge in linker(world)]

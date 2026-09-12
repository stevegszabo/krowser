"""Generic support for listing/getting instances of an arbitrary CRD.

Unlike every other *Api client used elsewhere in krowser, CustomObjectsApi
has no generated model classes -- it returns plain dicts (raw, camelCase
JSON, straight off the wire) instead of typed objects with snake_case
attributes. AttrDict below bridges that gap so a custom resource instance
can flow through the exact same build_node()/relationships.py code paths
(which all use dot access, e.g. `obj.metadata.owner_references`) as every
typed object fetched elsewhere.
"""

from datetime import datetime
from typing import Any

from kubernetes.client.rest import ApiException

from krowser.k8s.client import KubeClientManager
from krowser.k8s.fetchers import ResourceAccessError


def _snake_to_camel(name: str) -> str:
    first, *rest = name.split("_")
    return first + "".join(word.capitalize() for word in rest)


def _wrap(value: Any) -> Any:
    if isinstance(value, dict):
        return AttrDict(value)
    if isinstance(value, list):
        return [_wrap(v) for v in value]
    return value


class AttrDict:
    def __init__(self, data: dict[str, Any] | None):
        self._data = data or {}

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        camel = _snake_to_camel(name)
        key = camel if camel in self._data else name
        value = self._data.get(key)
        if isinstance(value, str) and key.endswith("Timestamp"):
            # Typed *Api clients parse RFC3339 timestamps into datetime
            # automatically during deserialization; raw CustomObjectsApi
            # responses don't get that treatment, so do it here instead --
            # humanize_age() and sorting-by-age both expect a real datetime.
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return _wrap(value)


def list_custom_resources(
    mgr: KubeClientManager, context: str | None, namespace: str | None, group: str, version: str, plural: str
) -> list[AttrDict]:
    api = mgr.custom_objects_api(context)
    try:
        if namespace:
            resp = api.list_namespaced_custom_object(group, version, namespace, plural)
        else:
            resp = api.list_cluster_custom_object(group, version, plural)
    except ApiException as exc:
        raise ResourceAccessError(plural, exc.status, exc.reason or "") from exc
    return [AttrDict(item) for item in resp.get("items", [])]


def get_custom_resource(
    mgr: KubeClientManager,
    context: str | None,
    namespace: str | None,
    name: str,
    group: str,
    version: str,
    plural: str,
) -> dict[str, Any]:
    # Returned as a raw dict (not AttrDict-wrapped) -- this is only ever used
    # to render a resource's actual YAML, which wants the real JSON shape.
    api = mgr.custom_objects_api(context)
    try:
        if namespace:
            return api.get_namespaced_custom_object(group, version, namespace, plural, name)
        return api.get_cluster_custom_object(group, version, plural, name)
    except ApiException as exc:
        raise ResourceAccessError(plural, exc.status, exc.reason or "") from exc

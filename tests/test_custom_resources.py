from datetime import datetime, timezone

import pytest
from kubernetes import client as k8s
from kubernetes.client.rest import ApiException

from krowser.k8s.custom_resources import (
    AttrDict,
    get_custom_resource,
    list_custom_resources,
    resolve_served_version,
)
from krowser.k8s.fetchers import ResourceAccessError


def _version(name, served, storage):
    return k8s.V1CustomResourceDefinitionVersion(name=name, served=served, storage=storage)


def test_attrdict_converts_snake_case_to_camel_and_wraps_nested_dicts():
    obj = AttrDict({"metadata": {"name": "widget-1", "namespace": "ns"}, "spec": {"replicaCount": 3}})

    assert obj.metadata.name == "widget-1"
    assert obj.metadata.namespace == "ns"
    assert obj.spec.replica_count == 3


def test_attrdict_wraps_lists_of_dicts():
    obj = AttrDict({"status": {"conditions": [{"type": "Ready", "status": "True"}]}})

    conditions = obj.status.conditions
    assert len(conditions) == 1
    assert conditions[0].type == "Ready"
    assert conditions[0].status == "True"


def test_attrdict_missing_key_returns_none():
    obj = AttrDict({"metadata": {"name": "widget-1"}})

    assert obj.status is None
    assert obj.metadata.namespace is None


def test_attrdict_parses_timestamp_fields_into_datetime():
    obj = AttrDict({"metadata": {"creationTimestamp": "2026-01-15T10:30:00Z"}})

    result = obj.metadata.creation_timestamp
    assert result == datetime(2026, 1, 15, 10, 30, 0, tzinfo=timezone.utc)


def test_resolve_served_version_prefers_storage_and_served(make_crd):
    crd = make_crd(
        "crd-1",
        "widgets.example.com",
        versions=[
            _version("v1alpha1", served=True, storage=False),
            _version("v1", served=True, storage=True),
        ],
    )

    assert resolve_served_version(crd) == "v1"


def test_resolve_served_version_falls_back_to_first_served_when_storage_not_served(make_crd):
    crd = make_crd(
        "crd-1",
        "widgets.example.com",
        versions=[
            _version("v1alpha1", served=True, storage=False),
            _version("v1", served=False, storage=True),
        ],
    )

    assert resolve_served_version(crd) == "v1alpha1"


def test_resolve_served_version_raises_when_nothing_served(make_crd):
    crd = make_crd(
        "crd-1",
        "widgets.example.com",
        versions=[_version("v1", served=False, storage=True)],
    )

    with pytest.raises(ValueError):
        resolve_served_version(crd)


class _FakeCustomObjectsApi:
    def __init__(self, items=(), error=None):
        self._items = list(items)
        self._error = error

    def list_namespaced_custom_object(self, group, version, namespace, plural):
        if self._error:
            raise self._error
        return {"items": [i for i in self._items if i["metadata"].get("namespace") == namespace]}

    def list_cluster_custom_object(self, group, version, plural):
        if self._error:
            raise self._error
        return {"items": self._items}

    def get_namespaced_custom_object(self, group, version, namespace, plural, name):
        if self._error:
            raise self._error
        return next(
            i for i in self._items if i["metadata"].get("namespace") == namespace and i["metadata"]["name"] == name
        )

    def get_cluster_custom_object(self, group, version, plural, name):
        if self._error:
            raise self._error
        return next(i for i in self._items if i["metadata"]["name"] == name)


class _FakeManager:
    def __init__(self, api):
        self._api = api

    def custom_objects_api(self, context):
        return self._api


def _item(name, namespace=None):
    metadata = {"name": name}
    if namespace:
        metadata["namespace"] = namespace
    return {"metadata": metadata}


def test_list_custom_resources_namespaced_filters_by_namespace():
    api = _FakeCustomObjectsApi(items=[_item("a", "ns-1"), _item("b", "ns-2")])
    mgr = _FakeManager(api)

    result = list_custom_resources(mgr, None, "ns-1", "example.com", "v1", "widgets")

    assert [r.metadata.name for r in result] == ["a"]
    assert isinstance(result[0], AttrDict)


def test_list_custom_resources_cluster_scoped_ignores_namespace():
    api = _FakeCustomObjectsApi(items=[_item("a"), _item("b")])
    mgr = _FakeManager(api)

    result = list_custom_resources(mgr, None, None, "example.com", "v1", "widgets")

    assert {r.metadata.name for r in result} == {"a", "b"}


def test_get_custom_resource_returns_raw_dict_not_attrdict():
    api = _FakeCustomObjectsApi(items=[_item("a", "ns-1")])
    mgr = _FakeManager(api)

    result = get_custom_resource(mgr, None, "ns-1", "a", "example.com", "v1", "widgets")

    assert result == {"metadata": {"name": "a", "namespace": "ns-1"}}
    assert not isinstance(result, AttrDict)


def test_list_custom_resources_wraps_api_error():
    api = _FakeCustomObjectsApi(error=ApiException(status=403, reason="Forbidden"))
    mgr = _FakeManager(api)

    with pytest.raises(ResourceAccessError):
        list_custom_resources(mgr, None, None, "example.com", "v1", "widgets")

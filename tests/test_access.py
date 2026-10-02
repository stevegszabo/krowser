from krowser.k8s.access import can_i


class _Obj:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeAuthorizationV1Api:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.reviews = []

    def create_self_subject_access_review(self, review):
        self.reviews.append(review)
        return _Obj(status=_Obj(allowed=self.allowed))


class _FakeManager:
    def __init__(self, api):
        self._api = api

    def authorization_v1(self, context):
        return self._api


def test_can_i_returns_allowed_status():
    api = _FakeAuthorizationV1Api(allowed=True)
    mgr = _FakeManager(api)

    assert can_i(mgr, None, "delete", "", "pods", namespace="ns") is True


def test_can_i_returns_false_when_denied():
    api = _FakeAuthorizationV1Api(allowed=False)
    mgr = _FakeManager(api)

    assert can_i(mgr, None, "update", "apps", "deployments", subresource="scale", namespace="ns") is False


def test_can_i_builds_resource_attributes_from_arguments():
    api = _FakeAuthorizationV1Api(allowed=True)
    mgr = _FakeManager(api)

    can_i(mgr, None, "patch", "apps", "statefulsets", namespace="ns")

    attrs = api.reviews[0].spec.resource_attributes
    assert attrs.verb == "patch"
    assert attrs.group == "apps"
    assert attrs.resource == "statefulsets"
    assert attrs.subresource == ""
    assert attrs.namespace == "ns"

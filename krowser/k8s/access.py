from kubernetes import client as k8s_client

from krowser.k8s.client import KubeClientManager


def can_i(
    mgr: KubeClientManager,
    context: str | None,
    verb: str,
    group: str,
    resource: str,
    subresource: str | None = None,
    namespace: str | None = None,
) -> bool:
    """Equivalent to `kubectl auth can-i <verb> <resource>[/<subresource>]
    [-n <namespace>]` -- a SelfSubjectAccessReview needs no RBAC grant of its
    own (every authenticated identity can always ask what *it itself* can
    do), which is what makes it safe to check speculatively before showing a
    mutating action in the UI, rather than only finding out via a failed
    request after the fact.
    """
    api = mgr.authorization_v1(context)
    review = k8s_client.V1SelfSubjectAccessReview(
        spec=k8s_client.V1SelfSubjectAccessReviewSpec(
            resource_attributes=k8s_client.V1ResourceAttributes(
                namespace=namespace,
                verb=verb,
                group=group,
                resource=resource,
                subresource=subresource or "",
            )
        )
    )
    result = api.create_self_subject_access_review(review)
    return bool(result.status.allowed)

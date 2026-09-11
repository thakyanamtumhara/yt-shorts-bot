class InstagramAIDisclosureError(RuntimeError):
    pass


def raise_for_disclosure_rejection(response):
    try:
        body = response.json()
    except (ValueError, TypeError):
        return
    if not isinstance(body, dict):
        return
    error = body.get("error") or {}
    message = str(error.get("message", "")).lower() if isinstance(error, dict) else ""
    if "is_ai_generated" in message or body.get("is_ai_generated") is False:
        raise InstagramAIDisclosureError(
            "Instagram rejected the required native AI disclosure; publishing stopped without an unlabeled retry."
        )


def create_ai_container(client, url, data, *, timeout=30, audit=None):
    flag = data.get("is_ai_generated")
    if not (flag is True or flag == "true"):
        raise InstagramAIDisclosureError(
            "Required Instagram is_ai_generated=true is missing; no container request was sent."
        )
    response = client.post(url, data=dict(data), timeout=timeout)
    raise_for_disclosure_rejection(response)
    if response.status_code == 200 and audit is not None:
        body = response.json()
        container_id = body.get("id") if isinstance(body, dict) else None
        if container_id:
            audit["ai_disclosure"] = {
                "requested": True,
                "container_id": str(container_id),
                "verification": "create_accepted_native_label_unverified",
            }
    return response

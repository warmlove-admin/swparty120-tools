from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from app.config import settings
from app.models.line_source_link import LineSourceKind


LINE_API_BASE = "https://api.line.me/v2/bot"


def _line_get_json(path: str) -> dict | None:
    if not settings.line_channel_access_token:
        return None
    request = Request(
        f"{LINE_API_BASE}{path}",
        headers={"Authorization": f"Bearer {settings.line_channel_access_token}"},
    )
    try:
        with urlopen(request, timeout=4) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return None


def fetch_line_source_display_name(source_kind: LineSourceKind, source_id: str) -> str | None:
    encoded_id = quote(source_id, safe="")
    if source_kind == LineSourceKind.user:
        data = _line_get_json(f"/profile/{encoded_id}")
        return (data or {}).get("displayName") or None
    if source_kind == LineSourceKind.group:
        data = _line_get_json(f"/group/{encoded_id}/summary")
        return (data or {}).get("groupName") or None
    return None


def fetch_line_group_member_display_name(group_id: str | None, user_id: str | None) -> str | None:
    if not group_id or not user_id:
        return None
    data = _line_get_json(f"/group/{quote(group_id, safe='')}/member/{quote(user_id, safe='')}")
    return (data or {}).get("displayName") or None

from __future__ import annotations

import json
from typing import Dict, Optional
import urllib.request


def http_get_json(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content_type = resp.headers.get("Content-Type", "")
        body = resp.read()
    if "application/json" not in content_type and not body.strip().startswith((b"{", b"[")):
        raise ValueError(f"non-json response from {url} (content-type={content_type})")
    return json.loads(body.decode("utf-8"))


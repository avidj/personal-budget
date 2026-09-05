# SPDX-License-Identifier: Apache-2.0
"""Local LLM access (Ollama). One call, structured output, no retries beyond the schema.

The prompt is rendered by the pipeline (categorization.py) so that the n8n AI node
and this client use exactly the same text and schema.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

PROPOSAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string"},
                    "payee": {"type": "string"},
                    "category_ref": {"type": "string"},
                    "category": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "reason": {"type": "string"},
                },
                "required": ["ref", "category_ref", "confidence"],
            },
        }
    },
    "required": ["proposals"],
}


class OllamaClient:
    def __init__(self, base_url: str, model: str, timeout: float = 600.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def available(self) -> bool:
        try:
            return httpx.get(f"{self.base_url}/api/tags", timeout=5).status_code == 200
        except httpx.HTTPError:
            return False

    def structured(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "stream": False,
            "format": schema,
            "options": {"temperature": 0},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        r = httpx.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
        r.raise_for_status()
        content = r.json()["message"]["content"]
        log.info("ollama %s answered in %.1fs", self.model, r.json().get("total_duration", 0) / 1e9)
        return json.loads(content)


def schema_with_categories(
    categories: list[str], category_refs: list[str] | None = None
) -> dict[str, Any]:
    """Constrain category name and reference to the budget's real categories (Ollama native format)."""
    schema = json.loads(json.dumps(PROPOSAL_SCHEMA))
    props = schema["properties"]["proposals"]["items"]["properties"]
    props["category"]["enum"] = sorted(set(categories))
    if category_refs:
        props["category_ref"]["enum"] = sorted(set(category_refs))
    return schema

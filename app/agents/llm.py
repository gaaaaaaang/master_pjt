from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import get_settings


class AzureAgentClient:
    def __init__(self, *, timeout_seconds: float = 45.0) -> None:
        settings = get_settings()
        self.api_key = settings.openai_api_key
        self.model = settings.openai_model
        self.endpoint = settings.openai_endpoint.rstrip("/")
        self.api_version = settings.openai_api_version
        self.timeout_seconds = timeout_seconds

    def complete_json(
        self,
        *,
        system_prompt: str,
        input_data: dict[str, Any],
        output_schema: dict[str, Any],
        schema_name: str,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured.")

        url = (
            f"{self.endpoint}/openai/deployments/{self.model}/chat/completions"
            f"?api-version={self.api_version}"
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(input_data, ensure_ascii=False, default=str),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": output_schema,
                },
            },
        }
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                url,
                headers={"api-key": self.api_key, "Content-Type": "application/json"},
                json=payload,
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = response.text.strip()
                if len(detail) > 1000:
                    detail = f"{detail[:1000]}..."
                raise RuntimeError(
                    f"LLM API returned HTTP {response.status_code}: {detail or 'empty response body'}"
                ) from exc

        body = response.json()
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("LLM response did not include message content.") from exc
        if isinstance(content, list):
            content = "".join(
                str(item.get("text", "")) if isinstance(item, dict) else str(item)
                for item in content
            )
        return json.loads(str(content))

from collections.abc import Callable
from dataclasses import dataclass
import logging
import time
from typing import Any

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[int, BaseModel], dict[str, Any]]

    def to_gemini_declaration(self) -> dict[str, Any]:
        schema = self.input_model.model_json_schema()
        properties: dict[str, Any] = {}
        for prop_name, prop_schema in schema.get("properties", {}).items():
            cleaned: dict[str, Any] = {
                "type": _resolve_json_type(prop_schema),
                "description": prop_schema.get("description", ""),
            }
            if "enum" in prop_schema:
                cleaned["enum"] = prop_schema["enum"]
            properties[prop_name] = cleaned

        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "OBJECT",
                "properties": properties,
                "required": schema.get("required", []),
            },
        }


def _resolve_json_type(prop_schema: dict[str, Any]) -> str:
    raw = prop_schema.get("type")
    if isinstance(raw, str):
        return raw.upper()
    for candidate in prop_schema.get("anyOf", []):
        if isinstance(candidate, dict) and candidate.get("type") and candidate["type"] != "null":
            return str(candidate["type"]).upper()
    return "STRING"


class ToolRegistry:
    """
    Central tool registry enforcing:
      - Pydantic input validation
      - Backend-injected `user_id` authorization (never trusted from LLM args)
      - Structured error recovery for unknown tools or invalid arguments
      - Execution latency & audit logging
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool

    def list_declarations(self) -> list[dict[str, Any]]:
        return [tool.to_gemini_declaration() for tool in self._tools.values()]

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def execute(
        self,
        user_id: int,
        tool_name: str,
        raw_arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        start = time.perf_counter()
        tool = self._tools.get(tool_name)
        if tool is None:
            logger.warning("Agent requested unknown tool=%s for user_id=%s", tool_name, user_id)
            return {
                "ok": False,
                "error": f"Unknown tool '{tool_name}'.",
            }

        try:
            validated_args = tool.input_model.model_validate(raw_arguments or {})
        except ValidationError as exc:
            logger.warning("Invalid arguments for tool=%s user_id=%s", tool_name, user_id)
            return {
                "ok": False,
                "error": f"Invalid arguments for '{tool_name}': {exc.errors()[0]['msg']}",
            }

        try:
            result = tool.handler(user_id, validated_args)
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "Tool executed tool=%s user_id=%s ok=true latency_ms=%.1f",
                tool_name,
                user_id,
                elapsed_ms,
            )
            return {"ok": True, "data": result}
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.warning(
                "Tool failed tool=%s user_id=%s error=%s latency_ms=%.1f",
                tool_name,
                user_id,
                type(exc).__name__,
                elapsed_ms,
            )
            return {
                "ok": False,
                "error": f"Tool execution error: {type(exc).__name__}",
            }

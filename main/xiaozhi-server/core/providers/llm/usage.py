"""Provider-neutral LLM usage normalization and per-turn aggregation."""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Dict, Iterable, Optional


TOKEN_USAGE_PREFIX = "TOKEN_USAGE "


def _read_field(value: Any, *names: str) -> Any:
    for name in names:
        if isinstance(value, dict) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return None


def _as_non_negative_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def normalize_provider_usage(usage: Any) -> Dict[str, Optional[int]]:
    """Map common provider/OpenAI usage shapes to one stable schema."""
    prompt_details = _read_field(
        usage, "prompt_tokens_details", "input_tokens_details"
    )
    completion_details = _read_field(
        usage, "completion_tokens_details", "output_tokens_details"
    )

    input_tokens = _as_non_negative_int(
        _read_field(usage, "prompt_tokens", "input_tokens")
    )
    output_tokens = _as_non_negative_int(
        _read_field(usage, "completion_tokens", "output_tokens")
    )
    total_tokens = _as_non_negative_int(_read_field(usage, "total_tokens"))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cached_input_tokens": _as_non_negative_int(
            _read_field(prompt_details, "cached_tokens")
        ),
        "reasoning_tokens": _as_non_negative_int(
            _read_field(completion_details, "reasoning_tokens")
        ),
    }


def build_payload_profile(
    dialogue: Iterable[Dict[str, Any]], tools: Optional[Iterable[Dict[str, Any]]] = None
) -> Dict[str, int]:
    """Measure request composition without retaining or logging request contents."""
    role_chars = {
        "system_chars": 0,
        "user_chars": 0,
        "assistant_chars": 0,
        "tool_result_chars": 0,
        "other_message_chars": 0,
    }
    message_count = 0

    for message in dialogue or []:
        message_count += 1
        role = message.get("role") if isinstance(message, dict) else None
        serialized = json.dumps(
            message, ensure_ascii=False, separators=(",", ":"), default=str
        )
        size = len(serialized)
        field = {
            "system": "system_chars",
            "user": "user_chars",
            "assistant": "assistant_chars",
            "tool": "tool_result_chars",
        }.get(role, "other_message_chars")
        role_chars[field] += size

    tool_list = list(tools or [])
    tool_schema_chars = len(
        json.dumps(
            tool_list, ensure_ascii=False, separators=(",", ":"), default=str
        )
    ) if tool_list else 0

    return {
        "message_count": message_count,
        "tool_count": len(tool_list),
        "tool_schema_chars": tool_schema_chars,
        **role_chars,
    }


def create_usage_event(
    *,
    usage: Any,
    model: Optional[str],
    provider: str,
    usage_context: Optional[Dict[str, Any]],
    payload_profile: Dict[str, int],
    latency_ms: int,
    request_id: Optional[str] = None,
    status: str = "completed",
) -> Dict[str, Any]:
    normalized = normalize_provider_usage(usage) if usage is not None else {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "cached_input_tokens": None,
        "reasoning_tokens": None,
    }
    context = usage_context or {}
    return {
        "event": "llm_usage",
        "turn_id": context.get("turn_id"),
        "session_id": context.get("session_id"),
        "request_id": request_id,
        "call_index": context.get("call_index", 1),
        "purpose": context.get("purpose", "unspecified"),
        "provider": provider,
        "model": model,
        **normalized,
        "latency_ms": max(0, int(latency_ms)),
        "status": status,
        "usage_source": "provider" if usage is not None else "unavailable",
        **payload_profile,
    }


def emit_usage_event(logger: Any, event: Dict[str, Any], usage_context=None) -> None:
    """Deliver usage to a turn collector, or log it directly when standalone."""
    callback: Optional[Callable[[Dict[str, Any]], None]] = (
        (usage_context or {}).get("record_usage")
    )
    if callback is not None:
        try:
            callback(event)
            return
        except Exception as exc:
            logger.warning(f"Token usage callback failed: {exc}")
    logger.info(
        TOKEN_USAGE_PREFIX
        + json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=str)
    )


class LLMUsageTurnTracker:
    """Aggregate all provider calls made for one user turn."""

    def __init__(
        self,
        *,
        logger: Any,
        turn_id: str,
        session_id: str,
        model: Optional[str] = None,
    ):
        self.logger = logger
        self.turn_id = turn_id
        self.session_id = session_id
        self.model = model
        self.started_at = time.perf_counter()
        self._lock = threading.Lock()
        self._next_call_index = 0
        self._events = []
        self._tool_events = []
        self._finished = False

    def next_request(self, purpose: str) -> Dict[str, Any]:
        with self._lock:
            self._next_call_index += 1
            call_index = self._next_call_index
        return {
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "call_index": call_index,
            "purpose": purpose,
            "record_usage": self.record,
        }

    def record(self, event: Dict[str, Any]) -> None:
        with self._lock:
            self._events.append(dict(event))
        self.logger.info(
            TOKEN_USAGE_PREFIX
            + json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=str)
        )

    def record_tool(
        self,
        *,
        tool_name: str,
        argument_chars: int,
        result_chars: int,
        execution_ms: int,
        action: Optional[str],
        status: str,
        requires_llm_followup: bool,
    ) -> Dict[str, Any]:
        event = {
            "event": "tool_usage",
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "tool_name": tool_name,
            "argument_chars": max(0, int(argument_chars)),
            "result_chars": max(0, int(result_chars)),
            "execution_ms": max(0, int(execution_ms)),
            "action": action,
            "status": status,
            "requires_llm_followup": bool(requires_llm_followup),
        }
        with self._lock:
            self._tool_events.append(event)
        self.logger.info(
            TOKEN_USAGE_PREFIX
            + json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=str)
        )
        return event

    def finish(self, status: str = "completed") -> Optional[Dict[str, Any]]:
        with self._lock:
            if self._finished:
                return None
            self._finished = True
            events = list(self._events)
            tool_events = list(self._tool_events)

        def total(field: str) -> Optional[int]:
            values = [event.get(field) for event in events]
            if not values or any(value is None for value in values):
                return None
            return sum(values)

        summary = {
            "event": "turn_usage_summary",
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "model": self.model,
            "api_call_count": len(events),
            "provider_usage_call_count": sum(
                event.get("usage_source") == "provider" for event in events
            ),
            "tool_capable_call_count": sum(
                int((event.get("tool_count") or 0) > 0) for event in events
            ),
            "tool_call_count": len(tool_events),
            "tool_followup_count": sum(
                event.get("requires_llm_followup") is True for event in tool_events
            ),
            "tool_argument_chars": sum(
                event.get("argument_chars") or 0 for event in tool_events
            ),
            "tool_result_chars": sum(
                event.get("result_chars") or 0 for event in tool_events
            ),
            "tool_execution_ms": sum(
                event.get("execution_ms") or 0 for event in tool_events
            ),
            "input_tokens": total("input_tokens"),
            "output_tokens": total("output_tokens"),
            "total_tokens": total("total_tokens"),
            "cached_input_tokens": total("cached_input_tokens"),
            "reasoning_tokens": total("reasoning_tokens"),
            "duration_ms": max(
                0, int((time.perf_counter() - self.started_at) * 1000)
            ),
            "status": status,
        }
        self.logger.info(
            TOKEN_USAGE_PREFIX
            + json.dumps(summary, ensure_ascii=False, separators=(",", ":"), default=str)
        )
        return summary

"""Provider-neutral LLM usage normalization and per-turn aggregation."""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Dict, Iterable, Optional


TOKEN_USAGE_PREFIX = "TOKEN_USAGE "
TOKEN_BUDGET_PREFIX = "TOKEN_BUDGET "

DEFAULT_TOKEN_BUDGET = {
    "enabled": False,
    "max_total_tokens_per_turn": 12_000,
    "max_llm_calls_per_turn": 3,
    "max_tool_calls_per_turn": 8,
    "max_output_tokens_per_request": 200,
    "warn_at_percent": 80,
    "client_telemetry": True,
}

CLIENT_USAGE_FIELDS = (
    "turn_id",
    "model",
    "api_call_count",
    "llm_calls_started",
    "provider_usage_call_count",
    "tool_call_count",
    "tool_followup_count",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "known_total_tokens",
    "provider_usage_complete",
    "duration_ms",
    "status",
    "budget_enabled",
    "budget_status",
    "budget_reason",
    "max_total_tokens_per_turn",
    "max_llm_calls_per_turn",
    "max_tool_calls_per_turn",
    "max_output_tokens_per_request",
    "warn_at_percent",
    "output_cap_enforced",
)


def build_client_usage_payload(
    summary: Dict[str, Any], session_id: str
) -> Dict[str, Any]:
    """Build the strict public telemetry allowlist sent to connected clients."""
    return {
        "type": "token_usage",
        "session_id": session_id,
        "usage": {
            key: summary.get(key)
            for key in CLIENT_USAGE_FIELDS
        },
    }


class TokenBudgetExceeded(RuntimeError):
    """Raised before a costly or mutating step when a hard budget is exhausted."""

    def __init__(self, reason: str, public_message: str, event: Dict[str, Any]):
        super().__init__(reason)
        self.reason = reason
        self.public_message = public_message
        self.event = event


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


def _first_not_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def normalize_provider_usage(usage: Any) -> Dict[str, Optional[int]]:
    """Map common provider/OpenAI usage shapes to one stable schema."""
    prompt_details = _read_field(
        usage, "prompt_tokens_details", "input_tokens_details"
    )
    completion_details = _read_field(
        usage, "completion_tokens_details", "output_tokens_details"
    )

    input_tokens = _as_non_negative_int(
        _read_field(
            usage,
            "prompt_tokens",
            "input_tokens",
            "prompt_token_count",
            "prompt_eval_count",
        )
    )
    output_tokens = _as_non_negative_int(
        _read_field(
            usage,
            "completion_tokens",
            "output_tokens",
            "candidates_token_count",
            "eval_count",
        )
    )
    total_tokens = _as_non_negative_int(
        _read_field(usage, "total_tokens", "total_token_count")
    )
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cached_input_tokens": _as_non_negative_int(
            _first_not_none(
                _read_field(
                    usage, "cached_content_token_count", "cache_read_input_tokens"
                ),
                _read_field(prompt_details, "cached_tokens"),
            )
        ),
        "reasoning_tokens": _as_non_negative_int(
            _first_not_none(
                _read_field(usage, "thoughts_token_count"),
                _read_field(completion_details, "reasoning_tokens"),
            )
        ),
        "tool_input_tokens": _as_non_negative_int(
            _read_field(usage, "tool_use_prompt_token_count")
        ),
    }


_USAGE_TOKEN_FIELDS = {
    "prompt_tokens",
    "input_tokens",
    "prompt_token_count",
    "prompt_eval_count",
    "completion_tokens",
    "output_tokens",
    "candidates_token_count",
    "eval_count",
    "total_tokens",
    "total_token_count",
}


def find_usage_candidate(value: Any, max_depth: int = 5) -> Any:
    """Find a provider usage object in common SDK/SSE wrapper shapes."""
    seen = set()

    def visit(current: Any, depth: int) -> Any:
        if current is None or depth > max_depth:
            return None
        identity = id(current)
        if identity in seen:
            return None
        seen.add(identity)

        if isinstance(current, dict):
            if _USAGE_TOKEN_FIELDS.intersection(current):
                return current
            for key in ("usage", "usage_metadata", "metadata", "data", "metrics"):
                if key in current:
                    found = visit(current[key], depth + 1)
                    if found is not None:
                        return found
            return None

        if any(hasattr(current, field) for field in _USAGE_TOKEN_FIELDS):
            return current
        for attr in ("usage", "usage_metadata", "metadata", "data", "metrics"):
            if hasattr(current, attr):
                found = visit(getattr(current, attr), depth + 1)
                if found is not None:
                    return found
        return None

    return visit(value, 0)


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
        "tool_input_tokens": None,
    }
    has_provider_counts = any(value is not None for value in normalized.values())
    if status == "completed" and not has_provider_counts:
        status = "usage_unavailable"
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
        "usage_source": "provider" if has_provider_counts else "unavailable",
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


class StreamUsageRecorder:
    """Collect the final usage object from any streaming provider and emit once."""

    def __init__(
        self,
        *,
        logger: Any,
        model: Optional[str],
        provider: str,
        dialogue: Iterable[Dict[str, Any]],
        tools: Optional[Iterable[Dict[str, Any]]] = None,
        usage_context: Optional[Dict[str, Any]] = None,
    ):
        self.logger = logger
        self.model = model
        self.provider = provider
        self.usage_context = usage_context
        self.payload_profile = build_payload_profile(dialogue, tools)
        self.started_at = time.perf_counter()
        self.final_usage = None
        self.request_id = None
        self._emitted = False

    def capture(
        self, value: Any, request_id: Optional[str] = None
    ) -> Any:
        usage = find_usage_candidate(value)
        if usage is not None:
            self.final_usage = usage
        if request_id:
            self.request_id = str(request_id)
        elif value is not None:
            candidate_id = _read_field(value, "request_id", "id")
            if candidate_id:
                self.request_id = str(candidate_id)
        return usage

    def emit(self, status: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if self._emitted:
            return None
        self._emitted = True
        resolved_status = status or (
            "completed" if self.final_usage is not None else "usage_unavailable"
        )
        event = create_usage_event(
            usage=self.final_usage,
            model=self.model,
            provider=self.provider,
            usage_context=self.usage_context,
            payload_profile=self.payload_profile,
            latency_ms=(time.perf_counter() - self.started_at) * 1000,
            request_id=self.request_id,
            status=resolved_status,
        )
        emit_usage_event(self.logger, event, self.usage_context)
        return event


class LLMUsageTurnTracker:
    """Aggregate all provider calls made for one user turn."""

    def __init__(
        self,
        *,
        logger: Any,
        turn_id: str,
        session_id: str,
        model: Optional[str] = None,
        budget_config: Optional[Dict[str, Any]] = None,
        summary_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        output_cap_enforced: bool = False,
    ):
        self.logger = logger
        self.turn_id = turn_id
        self.session_id = session_id
        self.model = model
        self.started_at = time.perf_counter()
        configured_budget = dict(DEFAULT_TOKEN_BUDGET)
        configured_budget.update(budget_config or {})
        self.budget = {
            "enabled": bool(configured_budget["enabled"]),
            "max_total_tokens_per_turn": self._positive_int(
                configured_budget["max_total_tokens_per_turn"],
                DEFAULT_TOKEN_BUDGET["max_total_tokens_per_turn"],
            ),
            "max_llm_calls_per_turn": self._positive_int(
                configured_budget["max_llm_calls_per_turn"],
                DEFAULT_TOKEN_BUDGET["max_llm_calls_per_turn"],
            ),
            "max_tool_calls_per_turn": self._positive_int(
                configured_budget["max_tool_calls_per_turn"],
                DEFAULT_TOKEN_BUDGET["max_tool_calls_per_turn"],
            ),
            "max_output_tokens_per_request": self._positive_int(
                configured_budget["max_output_tokens_per_request"],
                DEFAULT_TOKEN_BUDGET["max_output_tokens_per_request"],
            ),
            "warn_at_percent": min(
                100,
                self._positive_int(
                    configured_budget["warn_at_percent"],
                    DEFAULT_TOKEN_BUDGET["warn_at_percent"],
                ),
            ),
            "client_telemetry": bool(configured_budget["client_telemetry"]),
        }
        self.summary_callback = summary_callback
        self.output_cap_enforced = bool(output_cap_enforced)
        self._lock = threading.Lock()
        self._next_call_index = 0
        self._events = []
        self._tool_events = []
        self._finished = False
        self._budget_status = "disabled" if not self.budget["enabled"] else "within_budget"
        self._budget_reason = None

    @staticmethod
    def _positive_int(value: Any, default: int) -> int:
        normalized = _as_non_negative_int(value)
        return normalized if normalized is not None and normalized > 0 else default

    def _known_total_tokens_locked(self) -> int:
        return sum(
            event.get("total_tokens") or 0
            for event in self._events
            if event.get("total_tokens") is not None
        )

    def _provider_usage_complete_locked(self) -> bool:
        return bool(self._events) and all(
            event.get("total_tokens") is not None for event in self._events
        )

    def _budget_event_locked(
        self,
        *,
        action: str,
        status: str,
        reason: Optional[str] = None,
        requested_tool_calls: int = 0,
    ) -> Dict[str, Any]:
        known_total = self._known_total_tokens_locked()
        limit = self.budget["max_total_tokens_per_turn"]
        return {
            "event": "token_budget",
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "model": self.model,
            "enabled": self.budget["enabled"],
            "action": action,
            "status": status,
            "reason": reason,
            "known_total_tokens": known_total,
            "provider_usage_complete": self._provider_usage_complete_locked(),
            "remaining_known_tokens": max(0, limit - known_total),
            "llm_calls_started": self._next_call_index,
            "tool_calls_recorded": len(self._tool_events),
            "requested_tool_calls": max(0, int(requested_tool_calls)),
            **{
                key: self.budget[key]
                for key in (
                    "max_total_tokens_per_turn",
                    "max_llm_calls_per_turn",
                    "max_tool_calls_per_turn",
                    "max_output_tokens_per_request",
                    "warn_at_percent",
                )
            },
        }

    def _emit_budget_event(self, event: Dict[str, Any]) -> None:
        self.logger.info(
            TOKEN_BUDGET_PREFIX
            + json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=str)
        )

    def next_request(self, purpose: str) -> Dict[str, Any]:
        with self._lock:
            if self._finished:
                event = self._budget_event_locked(
                    action="request_blocked",
                    status="blocked",
                    reason="turn_already_finished",
                )
                reason = "turn_already_finished"
            elif (
                self.budget["enabled"]
                and self._next_call_index >= self.budget["max_llm_calls_per_turn"]
            ):
                self._budget_status = "blocked"
                self._budget_reason = "llm_call_limit"
                event = self._budget_event_locked(
                    action="request_blocked",
                    status="blocked",
                    reason=self._budget_reason,
                )
                reason = self._budget_reason
            elif (
                self.budget["enabled"]
                and self._events
                and self._known_total_tokens_locked()
                >= self.budget["max_total_tokens_per_turn"]
            ):
                self._budget_status = "blocked"
                self._budget_reason = "turn_token_limit"
                event = self._budget_event_locked(
                    action="request_blocked",
                    status="blocked",
                    reason=self._budget_reason,
                )
                reason = self._budget_reason
            else:
                reason = None
                self._next_call_index += 1
                call_index = self._next_call_index
                event = self._budget_event_locked(
                    action="request_authorized",
                    status=self._budget_status,
                )
        self._emit_budget_event(event)
        if reason is not None:
            raise TokenBudgetExceeded(
                reason,
                "本轮大模型调用已达到预算上限，请开始新一轮对话。",
                event,
            )
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
            if self.budget["enabled"]:
                known_total = self._known_total_tokens_locked()
                limit = self.budget["max_total_tokens_per_turn"]
                warning_at = (limit * self.budget["warn_at_percent"]) // 100
                if known_total >= limit:
                    self._budget_status = "limit_reached_after_response"
                elif known_total >= warning_at:
                    self._budget_status = "warning"
                budget_event = self._budget_event_locked(
                    action="usage_observed",
                    status=self._budget_status,
                    reason=(
                        "turn_token_limit"
                        if known_total >= limit
                        else "warning_threshold"
                        if known_total >= warning_at
                        else None
                    ),
                )
            else:
                budget_event = None
        self.logger.info(
            TOKEN_USAGE_PREFIX
            + json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=str)
        )
        if budget_event is not None:
            self._emit_budget_event(budget_event)

    def authorize_tools(self, requested_count: int) -> Dict[str, Any]:
        requested_count = max(0, int(requested_count))
        with self._lock:
            reason = None
            if self._finished:
                reason = "turn_already_finished"
            elif (
                self.budget["enabled"]
                and self._events
                and self._known_total_tokens_locked()
                >= self.budget["max_total_tokens_per_turn"]
            ):
                reason = "turn_token_limit"
            elif (
                self.budget["enabled"]
                and len(self._tool_events) + requested_count
                > self.budget["max_tool_calls_per_turn"]
            ):
                reason = "tool_call_limit"

            if reason is not None:
                self._budget_status = "blocked"
                self._budget_reason = reason
            event = self._budget_event_locked(
                action="tool_batch_blocked" if reason else "tool_batch_authorized",
                status="blocked" if reason else self._budget_status,
                reason=reason,
                requested_tool_calls=requested_count,
            )
        self._emit_budget_event(event)
        if reason is not None:
            public_message = (
                "本轮工具调用数量超过预算限制，请缩小操作范围后重试。"
                if reason == "tool_call_limit"
                else "本轮 Token 已达到预算上限，未继续执行工具，请开始新一轮对话。"
            )
            raise TokenBudgetExceeded(reason, public_message, event)
        return event

    def effective_max_output_tokens(
        self, configured_max_tokens: Optional[int] = None
    ) -> Optional[int]:
        if not self.budget["enabled"]:
            return configured_max_tokens
        limit = self.budget["max_output_tokens_per_request"]
        configured = _as_non_negative_int(configured_max_tokens)
        return min(configured, limit) if configured and configured > 0 else limit

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
            budget_status = self._budget_status
            budget_reason = self._budget_reason
            llm_calls_started = self._next_call_index
        if status == "completed" and budget_status == "blocked":
            status = "budget_blocked"

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
            "llm_calls_started": llm_calls_started,
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
            "tool_input_tokens": total("tool_input_tokens"),
            "duration_ms": max(
                0, int((time.perf_counter() - self.started_at) * 1000)
            ),
            "status": status,
            "budget_enabled": self.budget["enabled"],
            "budget_status": budget_status,
            "budget_reason": budget_reason,
            "output_cap_enforced": self.output_cap_enforced,
            "known_total_tokens": sum(
                event.get("total_tokens") or 0
                for event in events
                if event.get("total_tokens") is not None
            ),
            "provider_usage_complete": bool(events)
            and all(event.get("total_tokens") is not None for event in events),
            **{
                key: self.budget[key]
                for key in (
                    "max_total_tokens_per_turn",
                    "max_llm_calls_per_turn",
                    "max_tool_calls_per_turn",
                    "max_output_tokens_per_request",
                    "warn_at_percent",
                )
            },
        }
        self.logger.info(
            TOKEN_USAGE_PREFIX
            + json.dumps(summary, ensure_ascii=False, separators=(",", ":"), default=str)
        )
        if self.summary_callback is not None and self.budget["client_telemetry"]:
            try:
                self.summary_callback(dict(summary))
            except Exception as exc:
                self.logger.warning(f"Token usage summary callback failed: {exc}")
        return summary

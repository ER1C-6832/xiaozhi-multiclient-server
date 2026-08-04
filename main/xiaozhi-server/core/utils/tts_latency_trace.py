"""Low-overhead, opt-in timing for the LLM -> TTS -> WebSocket pipeline.

Enable with either:

    XIAOZHI_TTS_LATENCY_TRACE=1

or server config:

    tts_latency_trace:
      enabled: true

The trace intentionally records lengths and timing metadata only. It does not
persist prompts, model output, audio payloads, API keys, or tool arguments.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict
from typing import Any

TAG = __name__
_TRUTHY = {"1", "true", "yes", "on", "enabled"}


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


class TTSLatencyTrace:
    """Per-connection trace recorder safe to call from TTS worker threads."""

    def __init__(self, conn: Any):
        self._conn = conn
        self._lock = threading.Lock()
        self._turns: dict[str, dict[str, Any]] = {}

    def enabled(self) -> bool:
        env_value = os.getenv("XIAOZHI_TTS_LATENCY_TRACE", "").strip().lower()
        if env_value in _TRUTHY:
            return True
        config = getattr(self._conn, "config", {}) or {}
        trace_config = config.get("tts_latency_trace", {})
        if isinstance(trace_config, dict):
            return bool(trace_config.get("enabled", False))
        return False

    def start_turn(
        self,
        sentence_id: str,
        *,
        query_chars: int = 0,
        route: str | None = None,
        reason: str | None = None,
    ) -> None:
        if not self.enabled() or not sentence_id:
            return
        now_ns = time.perf_counter_ns()
        with self._lock:
            self._turns[sentence_id] = {
                "start_ns": now_ns,
                "last_ns": now_ns,
                "events": {},
                "counts": defaultdict(int),
            }
        self.mark(
            "turn_started",
            sentence_id=sentence_id,
            first_only=True,
            query_chars=max(0, int(query_chars)),
            route=route,
            reason=reason,
        )

    def mark(
        self,
        event: str,
        *,
        sentence_id: str | None = None,
        first_only: bool = False,
        **fields: Any,
    ) -> None:
        if not self.enabled():
            return
        resolved_id = sentence_id or getattr(self._conn, "sentence_id", None)
        if not resolved_id:
            return
        now_ns = time.perf_counter_ns()
        with self._lock:
            state = self._turns.get(resolved_id)
            if state is None:
                state = {
                    "start_ns": now_ns,
                    "last_ns": now_ns,
                    "events": {},
                    "counts": defaultdict(int),
                }
                self._turns[resolved_id] = state
            if first_only and event in state["events"]:
                return
            start_ns = state["start_ns"]
            last_ns = state["last_ns"]
            state["last_ns"] = now_ns
            state["counts"][event] += 1
            occurrence = state["counts"][event]
            state["events"].setdefault(event, now_ns)

        payload = {
            "event": "tts_latency",
            "stage": event,
            "session_id": getattr(self._conn, "session_id", None),
            "sentence_id": resolved_id,
            "occurrence": occurrence,
            "since_turn_ms": round((now_ns - start_ns) / 1_000_000, 3),
            "since_previous_ms": round((now_ns - last_ns) / 1_000_000, 3),
        }
        payload.update({key: _json_value(value) for key, value in fields.items()})
        logger = getattr(self._conn, "logger", None)
        if logger is not None:
            logger.bind(tag=TAG).info(
                "TTS_LATENCY " + json.dumps(payload, ensure_ascii=False, sort_keys=True)
            )

    def emit_summary(self, sentence_id: str | None = None) -> dict[str, Any] | None:
        if not self.enabled():
            return None
        resolved_id = sentence_id or getattr(self._conn, "sentence_id", None)
        if not resolved_id:
            return None
        with self._lock:
            state = self._turns.get(resolved_id)
            if state is None:
                return None
            start_ns = state["start_ns"]
            events = dict(state["events"])

        def elapsed(start_event: str, end_event: str) -> float | None:
            start = events.get(start_event)
            end = events.get(end_event)
            if start is None or end is None or end < start:
                return None
            return round((end - start) / 1_000_000, 3)

        summary = {
            "event": "tts_latency_summary",
            "session_id": getattr(self._conn, "session_id", None),
            "sentence_id": resolved_id,
            "turn_to_first_llm_delta_ms": elapsed("turn_started", "llm_first_delta"),
            "llm_request_to_first_delta_ms": elapsed(
                "llm_request_started", "llm_first_delta"
            ),
            "llm_stream_ms": elapsed("llm_first_delta", "llm_stream_completed"),
            "llm_first_delta_to_tts_text_ms": elapsed(
                "llm_first_delta", "tts_text_received"
            ),
            "llm_complete_to_tts_text_ms": elapsed(
                "llm_stream_completed", "tts_text_received"
            ),
            "turn_to_tts_text_ms": elapsed("turn_started", "tts_text_received"),
            "turn_to_first_segment_ms": elapsed("turn_started", "tts_segment_ready"),
            "tts_text_to_first_segment_ms": elapsed(
                "tts_text_received", "tts_segment_ready"
            ),
            "segment_to_provider_request_ms": elapsed(
                "tts_segment_ready", "tts_provider_request_started"
            ),
            "provider_first_audio_ms": elapsed(
                "tts_provider_request_started", "tts_provider_first_audio_chunk"
            ),
            "provider_audio_total_ms": elapsed(
                "tts_provider_request_started", "tts_provider_audio_completed"
            ),
            "provider_complete_to_first_opus_ms": elapsed(
                "tts_provider_audio_completed", "tts_first_opus"
            ),
            "first_opus_to_first_ws_send_ms": elapsed(
                "tts_first_opus", "tts_first_ws_audio_send"
            ),
            "segment_to_first_ws_audio_ms": elapsed(
                "tts_segment_ready", "tts_first_ws_audio_send"
            ),
            "turn_to_first_ws_audio_ms": elapsed(
                "turn_started", "tts_first_ws_audio_send"
            ),
            "turn_total_ms": round(
                (events.get("tts_stop_sent", time.perf_counter_ns()) - start_ns)
                / 1_000_000,
                3,
            ),
        }
        logger = getattr(self._conn, "logger", None)
        if logger is not None:
            logger.bind(tag=TAG).info(
                "TTS_LATENCY_SUMMARY "
                + json.dumps(summary, ensure_ascii=False, sort_keys=True)
            )
        with self._lock:
            self._turns.pop(resolved_id, None)
        return summary


def mark_tts_latency(
    conn: Any,
    event: str,
    *,
    sentence_id: str | None = None,
    first_only: bool = False,
    **fields: Any,
) -> None:
    trace = getattr(conn, "tts_latency_trace", None)
    if trace is None:
        return
    try:
        trace.mark(
            event,
            sentence_id=sentence_id,
            first_only=first_only,
            **fields,
        )
    except Exception:
        # Diagnostics must never change the production speech path.
        return


def emit_tts_latency_summary(conn: Any, sentence_id: str | None = None) -> None:
    trace = getattr(conn, "tts_latency_trace", None)
    if trace is None:
        return
    try:
        trace.emit_summary(sentence_id)
    except Exception:
        return

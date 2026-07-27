from dataclasses import dataclass
from typing import Any

import httpx


DEFAULT_CONNECT_TIMEOUT_SECONDS = 3.0
DEFAULT_READ_TIMEOUT_SECONDS = 15.0
DEFAULT_WRITE_TIMEOUT_SECONDS = 5.0
DEFAULT_POOL_TIMEOUT_SECONDS = 2.0
DEFAULT_MAX_RETRIES = 0
MAX_ALLOWED_RETRIES = 5


@dataclass(frozen=True)
class OpenAITransportConfig:
    timeout: httpx.Timeout
    max_retries: int


def _positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _max_retries(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_RETRIES
    return min(MAX_ALLOWED_RETRIES, max(0, parsed))


def build_openai_transport_config(config: dict) -> OpenAITransportConfig:
    """Build latency-bounded OpenAI SDK transport options.

    Voice turns should fail promptly when credentials or connectivity are broken.
    Retries remain opt-in because replaying a streaming request adds large and
    unpredictable latency, while authentication errors are never recoverable.
    """
    timeout_value = config.get("timeout")
    if isinstance(timeout_value, dict):
        timeout = httpx.Timeout(
            pool=_positive_float(
                timeout_value.get("pool"), DEFAULT_POOL_TIMEOUT_SECONDS
            ),
            connect=_positive_float(
                timeout_value.get("connect"), DEFAULT_CONNECT_TIMEOUT_SECONDS
            ),
            write=_positive_float(
                timeout_value.get("write"), DEFAULT_WRITE_TIMEOUT_SECONDS
            ),
            read=_positive_float(
                timeout_value.get("read"), DEFAULT_READ_TIMEOUT_SECONDS
            ),
        )
    elif isinstance(timeout_value, (int, float)) and timeout_value > 0:
        timeout = httpx.Timeout(float(timeout_value))
    else:
        timeout = httpx.Timeout(
            pool=DEFAULT_POOL_TIMEOUT_SECONDS,
            connect=DEFAULT_CONNECT_TIMEOUT_SECONDS,
            write=DEFAULT_WRITE_TIMEOUT_SECONDS,
            read=DEFAULT_READ_TIMEOUT_SECONDS,
        )

    return OpenAITransportConfig(
        timeout=timeout,
        max_retries=_max_retries(config.get("max_retries")),
    )

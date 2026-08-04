#!/usr/bin/env python3
"""Measure the local PCM -> Opus -> server WebSocket-send pipeline.

This probe does not call an LLM or a cloud TTS provider and does not open an
audio device. It generates a deterministic mono PCM tone, encodes it with the
same Opus encoder used by the server, then sends the packets through the same
sendAudio rate-control path into a fake WebSocket.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from dataclasses import dataclass

import numpy as np

from core.handle.sendAudioHandle import sendAudio
from core.utils.opus_encoder_utils import OpusEncoderUtils


class _FakeLogger:
    def bind(self, **_kwargs):
        return self

    def debug(self, *_args, **_kwargs):
        return None

    info = debug
    warning = debug
    error = debug


class _FakeWebSocket:
    def __init__(self, started_ns: int):
        self.started_ns = started_ns
        self.send_times_ns: list[int] = []
        self.packet_sizes: list[int] = []

    async def send(self, payload):
        if isinstance(payload, (bytes, bytearray)):
            self.send_times_ns.append(time.perf_counter_ns())
            self.packet_sizes.append(len(payload))


@dataclass
class _FakeConnection:
    websocket: _FakeWebSocket
    config: dict
    sentence_id: str = "pcm-probe"
    client_abort: bool = False
    conn_from_mqtt_gateway: bool = False
    last_activity_time: float = 0.0
    logger: _FakeLogger = _FakeLogger()


def _tone_pcm(sample_rate: int, duration_ms: int, frequency_hz: float) -> bytes:
    samples = max(1, int(sample_rate * duration_ms / 1000))
    timeline = np.arange(samples, dtype=np.float64) / sample_rate
    waveform = 0.18 * np.sin(2.0 * math.pi * frequency_hz * timeline)
    return np.asarray(waveform * 32767.0, dtype=np.int16).tobytes()


async def _run(args) -> dict:
    started_ns = time.perf_counter_ns()
    pcm_started_ns = time.perf_counter_ns()
    pcm = _tone_pcm(args.sample_rate, args.duration_ms, args.frequency_hz)
    pcm_ready_ns = time.perf_counter_ns()

    encoder = OpusEncoderUtils(
        sample_rate=args.sample_rate,
        channels=1,
        frame_size_ms=args.frame_ms,
    )
    packets: list[bytes] = []
    first_opus_ns: int | None = None

    def collect(packet: bytes) -> None:
        nonlocal first_opus_ns
        if first_opus_ns is None:
            first_opus_ns = time.perf_counter_ns()
        packets.append(packet)

    encode_started_ns = time.perf_counter_ns()
    encoder.encode_pcm_to_opus_stream(pcm, True, collect)
    encode_completed_ns = time.perf_counter_ns()

    websocket = _FakeWebSocket(started_ns)
    conn = _FakeConnection(
        websocket=websocket,
        config={"tts_audio_send_delay": -1},
    )
    await sendAudio(conn, packets, frame_duration=args.frame_ms)

    controller = getattr(conn, "audio_rate_controller", None)
    if controller is not None:
        await asyncio.wait_for(controller.queue_empty_event.wait(), timeout=10.0)
        controller.stop_sending()

    completed_ns = time.perf_counter_ns()
    first_ws_ns = websocket.send_times_ns[0] if websocket.send_times_ns else None
    last_ws_ns = websocket.send_times_ns[-1] if websocket.send_times_ns else None

    def ms(start: int | None, end: int | None) -> float | None:
        if start is None or end is None:
            return None
        return round((end - start) / 1_000_000, 3)

    return {
        "status": "ok" if packets and websocket.send_times_ns else "failed",
        "sample_rate": args.sample_rate,
        "frame_ms": args.frame_ms,
        "duration_ms": args.duration_ms,
        "pcm_bytes": len(pcm),
        "opus_packet_count": len(packets),
        "websocket_packet_count": len(websocket.send_times_ns),
        "pcm_generation_ms": ms(pcm_started_ns, pcm_ready_ns),
        "opus_encode_total_ms": ms(encode_started_ns, encode_completed_ns),
        "pcm_ready_to_first_opus_ms": ms(pcm_ready_ns, first_opus_ns),
        "first_opus_to_first_ws_send_ms": ms(first_opus_ns, first_ws_ns),
        "first_to_last_ws_send_ms": ms(first_ws_ns, last_ws_ns),
        "total_probe_ms": ms(started_ns, completed_ns),
        "expected_audio_playback_ms": len(packets) * args.frame_ms,
        "first_packet_bytes": websocket.packet_sizes[0]
        if websocket.packet_sizes
        else None,
        "payload_persisted": False,
        "cloud_service_used": False,
        "audio_device_opened": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-rate", type=int, default=24000)
    parser.add_argument("--frame-ms", type=int, default=60)
    parser.add_argument("--duration-ms", type=int, default=1200)
    parser.add_argument("--frequency-hz", type=float, default=440.0)
    args = parser.parse_args()
    if args.sample_rate <= 0 or args.frame_ms <= 0 or args.duration_ms <= 0:
        parser.error("sample rate, frame size, and duration must be positive")
    result = asyncio.run(_run(args))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())

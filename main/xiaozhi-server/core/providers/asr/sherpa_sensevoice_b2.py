"""Sherpa-ONNX SenseVoice provider with utterance-local VAD boundary refinement.

This provider deliberately remains opt-in.  It consumes the complete 16 kHz
PCM utterance already produced by Xiaozhi, refines speech boundaries with a
fresh Silero VAD instance, then runs the configured SenseVoice ONNX model.
There is no cross-request VAD state and no second network service.
"""

import asyncio
import os
import threading
import time
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np
import sherpa_onnx

from config.logger import setup_logging
from core.providers.asr.base import ASRProviderBase
from core.providers.asr.dto.dto import InterfaceType


TAG = __name__
logger = setup_logging()
SAMPLE_RATE = 16000


def _as_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _positive_int(config: dict, key: str, default: int) -> int:
    value = int(config.get(key, default))
    if value <= 0:
        raise ValueError(f"{key} must be greater than zero")
    return value


def _non_negative_float(config: dict, key: str, default: float) -> float:
    value = float(config.get(key, default))
    if value < 0:
        raise ValueError(f"{key} must not be negative")
    return value


@dataclass(frozen=True)
class SpeechBoundary:
    start: int
    length: int

    @property
    def end(self) -> int:
        return self.start + self.length


def contextualize_segments(
    samples: np.ndarray,
    boundaries: Sequence[SpeechBoundary],
    pre_roll_samples: int,
    post_roll_samples: int,
) -> List[np.ndarray]:
    """Add context without crossing more than half of an adjacent quiet gap."""
    output: List[np.ndarray] = []
    total = int(samples.size)

    for index, boundary in enumerate(boundaries):
        previous = boundaries[index - 1] if index > 0 else None
        following = boundaries[index + 1] if index + 1 < len(boundaries) else None

        pre_budget = pre_roll_samples
        if previous is not None:
            pre_budget = min(pre_budget, max(0, boundary.start - previous.end) // 2)

        post_budget = post_roll_samples
        if following is not None:
            post_budget = min(
                post_budget, max(0, following.start - boundary.end) // 2
            )

        start = max(0, boundary.start - pre_budget)
        end = min(total, boundary.end + post_budget)
        if end > start:
            output.append(
                np.ascontiguousarray(samples[start:end], dtype=np.float32)
            )

    return output


class ASRProvider(ASRProviderBase):
    def __init__(self, config: dict, delete_audio_file: bool):
        super().__init__()
        self.interface_type = InterfaceType.LOCAL
        self.output_dir = config.get("output_dir", "tmp/")
        self.delete_audio_file = delete_audio_file
        os.makedirs(self.output_dir, exist_ok=True)

        model_dir = os.path.abspath(config.get("model_dir", ""))
        self.model_path = self._resolve_path(
            model_dir, config.get("model_filename", "model.onnx")
        )
        self.tokens_path = self._resolve_path(
            model_dir, config.get("tokens_filename", "tokens.txt")
        )
        configured_vad = config.get("vad_model_path", "silero_vad.onnx")
        self.vad_model_path = self._resolve_path(model_dir, configured_vad)

        self.language = str(config.get("language", "zh"))
        self.num_threads = _positive_int(config, "num_threads", 4)
        self.vad_enabled = _as_bool(config.get("vad_enabled"), True)
        self.vad_min_silence_duration = _non_negative_float(
            config, "vad_min_silence_duration", 0.5
        )
        self.vad_buffer_size_sec = _non_negative_float(
            config, "vad_buffer_size_sec", 30.0
        )
        if self.vad_buffer_size_sec <= 0:
            raise ValueError("vad_buffer_size_sec must be greater than zero")
        self.pre_roll_samples = int(
            _non_negative_float(config, "vad_pre_roll_ms", 320.0)
            * SAMPLE_RATE
            / 1000
        )
        self.post_roll_samples = int(
            _non_negative_float(config, "vad_post_roll_ms", 240.0)
            * SAMPLE_RATE
            / 1000
        )
        self.min_segment_samples = int(
            _non_negative_float(config, "min_segment_sec", 0.30) * SAMPLE_RATE
        )
        self.fallback_to_full_audio = _as_bool(
            config.get("fallback_to_full_audio"), True
        )
        self.audio_level_diagnostics = _as_bool(
            config.get("audio_level_diagnostics"), True
        )
        self._decode_lock = threading.Lock()

        required = [self.model_path, self.tokens_path]
        if self.vad_enabled:
            required.append(self.vad_model_path)
        missing = [path for path in required if not os.path.isfile(path)]
        if missing:
            raise FileNotFoundError(
                "Sherpa SenseVoice B2 model file missing: " + ", ".join(missing)
            )

        self.recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=self.model_path,
            tokens=self.tokens_path,
            num_threads=self.num_threads,
            sample_rate=SAMPLE_RATE,
            use_itn=True,
            debug=False,
            language=self.language,
        )
        logger.bind(tag=TAG).info(
            "Sherpa SenseVoice B2 ready: "
            f"model={self.model_path}, language={self.language}, "
            f"threads={self.num_threads}, vad={self.vad_enabled}"
        )

    @staticmethod
    def _resolve_path(model_dir: str, value: str) -> str:
        value = os.path.expanduser(str(value))
        if os.path.isabs(value):
            return os.path.abspath(value)
        return os.path.abspath(os.path.join(model_dir, value))

    def _create_vad(self):
        vad_config = sherpa_onnx.VadModelConfig()
        vad_config.silero_vad.model = self.vad_model_path
        vad_config.silero_vad.min_silence_duration = (
            self.vad_min_silence_duration
        )
        vad_config.sample_rate = SAMPLE_RATE
        vad = sherpa_onnx.VoiceActivityDetector(
            vad_config, buffer_size_in_seconds=self.vad_buffer_size_sec
        )
        return vad, int(vad_config.silero_vad.window_size)

    @staticmethod
    def _drain_boundaries(vad, target: List[SpeechBoundary]) -> None:
        while not vad.empty():
            segment = vad.front
            target.append(
                SpeechBoundary(start=int(segment.start), length=len(segment.samples))
            )
            vad.pop()

    def _detect_boundaries(self, samples: np.ndarray) -> List[SpeechBoundary]:
        if not self.vad_enabled:
            return [SpeechBoundary(0, int(samples.size))]

        vad, window_size = self._create_vad()
        boundaries: List[SpeechBoundary] = []
        offset = 0
        while offset < samples.size:
            window = samples[offset : offset + window_size]
            if window.size < window_size:
                window = np.pad(window, (0, window_size - window.size))
            vad.accept_waveform(np.ascontiguousarray(window, dtype=np.float32))
            self._drain_boundaries(vad, boundaries)
            offset += window_size

        flush_samples = int(
            (
                self.vad_min_silence_duration
                + self.post_roll_samples / SAMPLE_RATE
                + 0.2
            )
            * SAMPLE_RATE
        )
        silence = np.zeros(window_size, dtype=np.float32)
        for _ in range(max(1, (flush_samples + window_size - 1) // window_size)):
            vad.accept_waveform(silence)
            self._drain_boundaries(vad, boundaries)

        return boundaries

    def _prepare_segments(self, samples: np.ndarray) -> List[np.ndarray]:
        boundaries = self._detect_boundaries(samples)
        segments = contextualize_segments(
            samples,
            boundaries,
            self.pre_roll_samples,
            self.post_roll_samples,
        )
        segments = [
            segment
            for segment in segments
            if segment.size >= self.min_segment_samples
        ]
        if not segments and self.fallback_to_full_audio and samples.size:
            logger.bind(tag=TAG).warning(
                "B2 VAD produced no usable segment; falling back to full utterance"
            )
            return [samples]
        return segments

    def _decode_segment(self, samples: np.ndarray) -> str:
        stream = self.recognizer.create_stream()
        stream.accept_waveform(SAMPLE_RATE, samples)
        self.recognizer.decode_stream(stream)
        return stream.result.text.strip()

    def _recognize(self, pcm_bytes: bytes) -> str:
        samples = (
            np.frombuffer(pcm_bytes, dtype="<i2")
            .astype(np.float32)
            .reshape(-1)
            / 32768.0
        )
        samples = np.ascontiguousarray(samples, dtype=np.float32)
        if samples.size == 0:
            return ""

        if self.audio_level_diagnostics:
            rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
            peak = float(np.max(np.abs(samples)))
            rms_db = 20.0 * np.log10(rms + 1e-12)
            logger.bind(tag=TAG).info(
                f"B2 input: duration={samples.size / SAMPLE_RATE:.3f}s, "
                f"rms_dbfs={rms_db:.1f}, peak={peak:.3f}"
            )

        segments = self._prepare_segments(samples)
        texts = [self._decode_segment(segment) for segment in segments]
        return "".join(text for text in texts if text)

    async def speech_to_text(
        self, opus_data: List[bytes], session_id: str, artifacts=None
    ) -> Tuple[Optional[str], Optional[str]]:
        if artifacts is None:
            return "", None

        started = time.monotonic()
        try:
            text = await asyncio.to_thread(self._recognize_locked, artifacts.pcm_bytes)
            logger.bind(tag=TAG).info(
                f"B2 ASR: elapsed={time.monotonic() - started:.3f}s, text={text}"
            )
            return text, artifacts.file_path
        except Exception as exc:
            logger.bind(tag=TAG).error(
                f"Sherpa SenseVoice B2 recognition failed: {exc}", exc_info=True
            )
            return "", artifacts.file_path

    def _recognize_locked(self, pcm_bytes: bytes) -> str:
        with self._decode_lock:
            return self._recognize(pcm_bytes)

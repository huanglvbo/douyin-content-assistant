"""Local speech recognition through faster-whisper's public Python API.

This module performs no hosted-model requests. Model weights may be downloaded
by faster-whisper on first use; a supplied cache is reused on later runs.
"""
from __future__ import annotations

import math
import os
from pathlib import Path


SUPPORTED_MODELS = frozenset({'tiny', 'base', 'small', 'medium', 'large-v3', 'large-v3-turbo'})
MAX_SOURCE_BYTES = 2 * 1024**3
MAX_DURATION_SECONDS = 4 * 60 * 60
MAX_SEGMENTS = 100_000


class RecognitionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _notify(progress, stage: str, fraction: float | None = None):
    # A host can request cancellation without a second execution channel.
    if progress is not None and progress(stage, fraction) is False:
        raise RecognitionError('cancelled', 'Local speech recognition was cancelled.')


def _load_model(name: str, directory: Path):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RecognitionError('dependency_missing', 'Speech recognition dependencies are missing. Run the installer.') from None
    try:
        return WhisperModel(
            name, device='cpu', compute_type='int8', download_root=str(directory),
            cpu_threads=max(1, min(4, os.cpu_count() or 1)), num_workers=1,
        )
    except Exception:
        raise RecognitionError('model_unavailable', 'The local speech model could not load. Check its download or cache.') from None


def _seconds(value, label: str) -> float:
    if isinstance(value, bool):
        raise RecognitionError('invalid_timeline', f'Speech recognition returned an invalid {label}.')
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise RecognitionError('invalid_timeline', f'Speech recognition returned an invalid {label}.') from None
    if not math.isfinite(number) or number < 0:
        raise RecognitionError('invalid_timeline', f'Speech recognition returned an invalid {label}.')
    return number


def transcribe_file(source: Path, model_dir: Path, model='small', progress=None) -> dict:
    """Return the full spoken transcript and segment timestamps.

    ``source`` is one explicitly selected local media file. ``model_dir`` is a
    caller-controlled cache (normally AppPaths.models, which respects
    CREATOR_NOTES_MODELS). Returning False from ``progress(stage, fraction)``
    cancels between model/segment operations. Timestamps cover recognized
    speech; silence is not filled with invented text.
    """
    source = Path(source).expanduser()
    if source.is_symlink() or not source.is_file():
        raise RecognitionError('source_missing', 'Select an existing local audio or video file.')
    source = source.resolve()
    if not 0 < source.stat().st_size <= MAX_SOURCE_BYTES:
        raise RecognitionError('source_size', 'The audio or video file is empty or exceeds the local transcription size limit.')
    if model not in SUPPORTED_MODELS:
        raise RecognitionError('invalid_model', 'Select a supported local speech model.')
    cache = Path(model_dir).expanduser().resolve()
    cache.mkdir(parents=True, exist_ok=True)
    _notify(progress, 'Preparing the local speech model', None)
    recognizer = _load_model(model, cache)
    try:
        _notify(progress, 'Transcribing audio', 0.0)
        stream, info = recognizer.transcribe(
            str(source), beam_size=5, vad_filter=True,
            condition_on_previous_text=False, word_timestamps=False,
        )
        duration = _seconds(info.duration, 'audio duration')
        if not 0 < duration <= MAX_DURATION_SECONDS:
            raise RecognitionError('duration_limit', 'The audio duration is zero or exceeds the four-hour transcription limit.')
        result = []
        previous_start = 0.0
        for item in stream:
            _notify(progress, 'Transcribing audio', min(1.0, previous_start / duration))
            start, end = _seconds(item.start, 'start time'), _seconds(item.end, 'end time')
            if start < previous_start or end <= start or start >= duration or end > duration + 0.5:
                raise RecognitionError('invalid_timeline', 'Speech recognition returned invalid segment timestamps.')
            if not isinstance(item.text, str):
                raise RecognitionError('invalid_transcript', 'Speech recognition returned invalid segment text.')
            text = item.text.strip()
            if '\x00' in text or len(text) > 32_000:
                raise RecognitionError('invalid_transcript', 'Speech recognition returned invalid segment text.')
            previous_start = start
            if not text:
                continue
            if len(result) >= MAX_SEGMENTS:
                raise RecognitionError('segment_limit', 'Speech recognition returned too many segments.')
            result.append({'start': start, 'end': min(end, duration), 'text': text})
        if not result:
            raise RecognitionError('no_speech', 'No deliverable speech was recognized. No transcript was fabricated.')
        _notify(progress, 'Transcription complete', 1.0)
        return {
            'segments': result,
            'transcript': '\n'.join(item['text'] for item in result),
            'duration': duration,
            'model': model,
            'language': str(getattr(info, 'language', '') or ''),
        }
    except RecognitionError:
        raise
    except KeyboardInterrupt:
        raise RecognitionError('cancelled', 'Local speech recognition was cancelled.') from None
    except Exception:
        raise RecognitionError('transcription_failed', 'Local transcription did not complete. Check the audio and model environment.') from None

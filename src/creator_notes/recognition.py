"""Local speech recognition through faster-whisper's public Python API.

This module performs no hosted-model requests. Model weights may be downloaded
by faster-whisper on first use; a supplied cache is reused on later runs.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
import re


SUPPORTED_MODELS = frozenset({'tiny', 'base', 'small', 'medium', 'large-v3', 'large-v3-turbo'})
MODEL_REPOSITORIES = {
    name: 'Systran/faster-whisper-' + name
    for name in ('tiny', 'base', 'small', 'medium', 'large-v3')
}
MODEL_REPOSITORIES['large-v3-turbo'] = 'mobiuslabsgmbh/faster-whisper-large-v3-turbo'
# Cache reuse may increase capacity, but must not reduce the selected profile's
# minimum capacity. Turbo is not treated as equivalent to a full-size model.
CACHED_MODEL_ALTERNATIVES = {
    'tiny': ('base', 'small', 'medium', 'large-v3'),
    'base': ('small', 'medium', 'large-v3'),
    'small': ('medium', 'large-v3'),
    'medium': ('large-v3',),
    'large-v3': (),
    'large-v3-turbo': (),
}
MODEL_DOWNLOAD_FILES = ('config.json', 'preprocessor_config.json', 'model.bin', 'tokenizer.json', 'vocabulary.*')
MAX_SOURCE_BYTES = 2 * 1024**3
MAX_DURATION_SECONDS = 4 * 60 * 60
MAX_SEGMENTS = 100_000
TIMELINE_RETRY_CHUNK_LENGTH = 20


class RecognitionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _inside_file(path: Path, root: Path) -> bool:
    """Allow canonical HF blob links, but never follow a link outside this cache."""
    try:
        resolved = path.resolve(strict=True)
        return resolved.is_relative_to(root) and resolved.is_file() and resolved.stat().st_size > 0
    except (OSError, ValueError, RuntimeError):
        return False


def _complete_model(path: Path, root: Path) -> bool:
    # A tokenizer is required: faster-whisper otherwise fetches one separately.
    required = ('model.bin', 'config.json', 'tokenizer.json')
    if not all(_inside_file(path / name, root) for name in required):
        return False
    return any(_inside_file(path / name, root) for name in ('vocabulary.json', 'vocabulary.txt'))


def model_inventory(model_dir: Path) -> dict:
    """Inspect only the supplied local cache, without loading weights or networking.

    ``ready`` describes required files being present, not a successful model load.
    Canonical HF snapshots may link into the same cache's blobs directory. English-
    only models and arbitrary external directories are never selected as alternatives.
    """
    root = Path(model_dir).expanduser().resolve()
    found = {}
    for name in sorted(SUPPORTED_MODELS):
        candidates = [(root / name, 'direct'), (root / ('faster-whisper-' + name), 'direct')]
        if root.name in (name, 'faster-whisper-' + name):
            candidates.insert(0, (root, 'direct'))
        repository = root / ('models--' + MODEL_REPOSITORIES[name].replace('/', '--'))
        snapshots = repository / 'snapshots'
        try:
            main_ref = repository / 'refs' / 'main'
            if _inside_file(main_ref, root) and main_ref.stat().st_size <= 128:
                revision = main_ref.read_text(encoding='utf-8').strip()
                if re.fullmatch(r'[a-fA-F0-9]{7,64}', revision):
                    candidates.append((snapshots / revision, 'huggingface_snapshot'))
            if snapshots.resolve().is_relative_to(root) and snapshots.is_dir():
                candidates.extend((item, 'huggingface_snapshot') for item in sorted(snapshots.iterdir(), key=lambda p: p.name, reverse=True)[:100]
                                  if re.fullmatch(r'[a-fA-F0-9]{7,64}', item.name))
        except (OSError, UnicodeError, RuntimeError):
            pass
        for candidate, layout in candidates:
            if _complete_model(candidate, root):
                found[name] = {'path': str(candidate.resolve()), 'ready': True, 'layout': layout,
                               'validation': 'required_files_present'}
                break
    return found


def select_model(requested: str, model_dir: Path, prefer_cached: bool = True) -> dict:
    """Return an explicit selection; a cache miss is a pending download, not failure.

    A caller choosing ``prefer_cached=True`` opts into a multilingual cached
    alternative of equal or higher capacity. The result always records both
    names and the reason for a change; smaller models are never substituted.
    """
    if requested not in SUPPORTED_MODELS:
        raise RecognitionError('invalid_model', 'Select a supported local speech model.')
    inventory = model_inventory(model_dir)
    selected = requested
    reason = 'requested_cached' if requested in inventory else 'download_required'
    if requested not in inventory and prefer_cached:
        selected = next((name for name in CACHED_MODEL_ALTERNATIVES[requested] if name in inventory), requested)
        if selected in inventory:
            reason = 'cached_alternative'
    return {'requested_model': requested, 'selected_model': selected,
            'cache_path': inventory.get(selected, {}).get('path'), 'reason': reason,
            'download_required': reason == 'download_required'}


def _configure_hf_downloads():
    # This environment change belongs to this process and its children only.
    # HF reads this option at import time, so set it before either public import.
    os.environ['HF_HUB_DISABLE_XET'] = '1'


def _repair_model_cache(requested: str, root: Path):
    """Force one bounded official download when a snapshot remains incomplete."""
    _configure_hf_downloads()
    from huggingface_hub import snapshot_download
    return snapshot_download(
        MODEL_REPOSITORIES[requested], cache_dir=str(root),
        allow_patterns=list(MODEL_DOWNLOAD_FILES), force_download=True,
        token=False, endpoint='https://huggingface.co', max_workers=2,
    )


def ensure_model_cache(model_dir: Path, requested: str, progress=None, prefer_cached=True) -> dict:
    """Prepare public weights before browser work; do not load the model itself.

    Existing complete multilingual caches are preferred explicitly. A failed
    download reports one safe error and leaves the cache available for a retry.
    It never advances creator collection, opens a browser, or uses an HF token.
    """
    root = Path(model_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    _configure_hf_downloads()
    selection = select_model(requested, root, prefer_cached=prefer_cached)
    if not selection['download_required']:
        _notify(progress, f"Using cached {selection['selected_model']} speech model", None)
        return dict(selection, downloaded_now=False)
    _notify(progress, f'Downloading {requested} speech model; one-time setup before collection', None)
    try:
        from faster_whisper.utils import download_model
    except ImportError:
        raise RecognitionError('dependency_missing', 'Speech recognition dependencies are missing. Run the installer.') from None
    try:
        download_model(requested, cache_dir=str(root), use_auth_token=False)
    except Exception:
        raise RecognitionError('model_unavailable', 'The speech model download did not complete. Retry model preparation; collection has not started.') from None
    selection = select_model(requested, root, prefer_cached=False)
    repaired = False
    if selection['download_required']:
        _notify(progress, f'Repairing incomplete {requested} speech model once from the official source', None)
        try:
            _repair_model_cache(requested, root)
        except Exception:
            raise RecognitionError('model_unavailable', 'The speech model cache repair did not complete. Retry model preparation; collection has not started.') from None
        repaired = True
        selection = select_model(requested, root, prefer_cached=False)
        if selection['download_required']:
            raise RecognitionError('model_unavailable', 'The speech model cache remains incomplete after one repair. Collection has not started.')
    _notify(progress, f'{requested} speech model is cached', None)
    return dict(selection, downloaded_now=True, cache_repaired=repaired)


def _notify(progress, stage: str, fraction: float | None = None):
    # A host can request cancellation without a second execution channel.
    if progress is not None and progress(stage, fraction) is False:
        raise RecognitionError('cancelled', 'Local speech recognition was cancelled.')


def _load_model(name: str, directory: Path):
    _configure_hf_downloads()
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RecognitionError('dependency_missing', 'Speech recognition dependencies are missing. Run the installer.') from None
    try:
        return WhisperModel(
            name, device='cpu', compute_type='int8', download_root=str(directory),
            cpu_threads=max(1, min(4, os.cpu_count() or 1)), num_workers=1,
            local_files_only=Path(name).is_dir(),
            use_auth_token=False,
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


def _audit_seconds(value):
    """Retain numeric timing facts without serializing arbitrary model values."""
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    try:
        return float(value) if math.isfinite(value) and abs(value) <= MAX_DURATION_SECONDS * 2 else None
    except (ValueError, OverflowError):
        return None


def _timeline_failure(error, *, duration, index=None, previous_start=None, item=None, reasons):
    error.timeline_failure = {
        'duration_seconds': _audit_seconds(duration),
        'segment_index': index if isinstance(index, int) and 0 <= index <= MAX_SEGMENTS else None,
        'previous_start': _audit_seconds(previous_start),
        'start': _audit_seconds(getattr(item, 'start', None)),
        'end': _audit_seconds(getattr(item, 'end', None)), 'reasons': reasons,
    }
    return error


def _transcription_pass(recognizer, source, progress, chunk_length=None):
    options = {'beam_size': 5, 'vad_filter': True,
               'condition_on_previous_text': False, 'word_timestamps': False}
    if chunk_length is not None:
        options['chunk_length'] = chunk_length
    # Each pass receives the same complete input, without clip timestamps,
    # cropped audio, or manually removed/rewritten model segments.
    stream, info = recognizer.transcribe(str(source), **options)
    try:
        duration = _seconds(info.duration, 'audio duration')
    except RecognitionError as exc:
        raise _timeline_failure(exc, duration=info.duration, reasons=['invalid_audio_duration'])
    if not 0 < duration <= MAX_DURATION_SECONDS:
        raise RecognitionError('duration_limit', 'The audio duration is zero or exceeds the four-hour transcription limit.')
    result = []
    previous_start = 0.0
    for index, item in enumerate(stream):
        _notify(progress, 'Transcribing audio', min(1.0, previous_start / duration))
        try:
            start, end = _seconds(item.start, 'start time'), _seconds(item.end, 'end time')
        except RecognitionError as exc:
            raise _timeline_failure(exc, duration=duration, index=index,
                previous_start=previous_start, item=item, reasons=['invalid_numeric_timestamp'])
        reasons = []
        if start < previous_start:
            reasons.append('start_before_previous_start')
        if end <= start:
            reasons.append('end_not_after_start')
        if start >= duration:
            reasons.append('start_at_or_after_duration')
        if end > duration + 0.5:
            reasons.append('end_more_than_half_second_after_duration')
        if reasons:
            error = RecognitionError('invalid_timeline', 'Speech recognition returned invalid segment timestamps.')
            raise _timeline_failure(error, duration=duration, index=index,
                previous_start=previous_start, item=item, reasons=reasons)
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
        # Keep the pre-existing <= 0.5 second rounding normalization unchanged.
        # Larger overruns reject the entire pass and are never clipped away.
        result.append({'start': start, 'end': min(end, duration), 'text': text})
    if not result:
        raise RecognitionError('no_speech', 'No deliverable speech was recognized. No transcript was fabricated.')
    return {'segments': result, 'transcript': '\n'.join(item['text'] for item in result),
            'duration': duration, 'language': str(getattr(info, 'language', '') or '')}


def _recovery_metadata(attempts):
    return {'strategy': 'one_full_input_shorter_chunk_retry', 'retry_count': max(0, len(attempts) - 1),
            'full_input_replayed': len(attempts) > 1, 'input_cropped': False, 'attempts': attempts}


def transcribe_file(source: Path, model_dir: Path, model='small', progress=None, prefer_cached=False) -> dict:
    """Return the full spoken transcript and segment timestamps.

    ``source`` is one explicitly selected local media file. ``model_dir`` is a
    caller-controlled cache (normally AppPaths.models, which respects
    CREATOR_NOTES_MODELS). Returning False from ``progress(stage, fraction)``
    cancels between model/segment operations. Timestamps cover recognized
    speech; silence is not filled with invented text. An invalid timeline can
    trigger one full-input replay with shorter chunks; its audit facts remain
    separate from the returned successful machine transcript.
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
    selection = select_model(model, cache, prefer_cached=prefer_cached)
    selected = selection['selected_model']
    stage = (f'Using cached {selected} speech model' if selection['cache_path'] else
             f'Preparing {selected} speech model; a first-use download may be required')
    _notify(progress, stage, None)
    recognizer = _load_model(selection['cache_path'] or selected, cache)
    attempts = []
    try:
        _notify(progress, 'Transcribing audio', 0.0)
        for attempt, chunk_length in enumerate((None, TIMELINE_RETRY_CHUNK_LENGTH), 1):
            try:
                result = _transcription_pass(recognizer, source, progress, chunk_length)
            except RecognitionError as exc:
                if exc.code != 'invalid_timeline':
                    if attempts:
                        status = exc.code if exc.code in {'no_speech', 'cancelled', 'duration_limit',
                            'invalid_transcript', 'segment_limit', 'transcription_failed'} else 'transcription_failed'
                        attempts.append({'attempt': attempt, 'chunk_length_seconds': chunk_length, 'status': status})
                    raise
                attempts.append({'attempt': attempt, 'chunk_length_seconds': chunk_length,
                    'status': 'invalid_timeline', 'failure': getattr(exc, 'timeline_failure', {})})
                if attempt == 2:
                    raise
                _notify(progress, 'Retrying the full audio once with shorter speech chunks', 0.0)
                continue
            except Exception:
                if attempts:
                    attempts.append({'attempt': attempt, 'chunk_length_seconds': chunk_length,
                                     'status': 'transcription_failed'})
                raise
            if attempts:
                attempts.append({'attempt': attempt, 'chunk_length_seconds': chunk_length, 'status': 'passed',
                                 'duration_seconds': result['duration'], 'segments': len(result['segments'])})
                result['timeline_recovery'] = _recovery_metadata(attempts)
            _notify(progress, 'Transcription complete', 1.0)
            return dict(result, model=selected, requested_model=model, model_selection=selection)
    except RecognitionError as exc:
        if attempts:
            exc.timeline_recovery = _recovery_metadata(attempts)
        raise
    except KeyboardInterrupt:
        error = RecognitionError('cancelled', 'Local speech recognition was cancelled.')
        if attempts:
            error.timeline_recovery = _recovery_metadata(attempts)
        raise error from None
    except Exception:
        error = RecognitionError('transcription_failed', 'Local transcription did not complete. Check the audio and model environment.')
        if attempts:
            error.timeline_recovery = _recovery_metadata(attempts)
        raise error from None

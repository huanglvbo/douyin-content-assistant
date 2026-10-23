"""Verified local video frames and host-written visual context for archives.

Frame sampling is visual evidence, not proof of silence or complete speech.
All paths are derived from owned native records; submissions accept no paths.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import uuid

from .engine import now, valid_id

PNG = b'\x89PNG\r\n\x1a\n'
MAX_PNG_BYTES = 16 * 1024 * 1024


class MediaUnavailable(ValueError):
    code = 'media_unavailable'


def _sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def _owned(path, root, *, file=False):
    path, root = Path(path), Path(root).resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError('Visual artifact is outside its owned directory')
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ValueError('Linked visual artifacts are not accepted')
        if part.resolve() == root:
            break
    if file and not path.is_file():
        raise ValueError('An owned visual artifact is missing')
    return resolved


def _vid(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{5,30}', value):
        raise ValueError('Invalid public work identifier')
    return value


def _text(value, limit=12000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or '\x00' in value:
        raise ValueError('Visual text must be a nonempty bounded string')
    return value.strip()


def _number(value, duration):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 0 <= value < duration):
        raise ValueError('Frame times must be finite and inside the actual video duration')
    return float(value)


def _write_new(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def verified_media(engine, report):
    """Return a verified saved MP4 and its actual native source check.

    A transcript reused from a prior check may have no new media checkpoint.
    Only the same verified author and work can supply that prior checkpoint.
    """
    author = report.get('profile', {}).get('sec_uid')
    video = report.get('latest', {}).get('video_id')
    if not author or not video:
        raise ValueError('Verified creator and work metadata are required')
    source = engine._saved_media(report)
    if source is not None:
        return source, valid_id(report['check_id']), report['media_checkpoint']['sha256']
    if report.get('media_checkpoint'):
        raise ValueError('Saved media changed or is missing')
    if report.get('transcription', {}).get('reused') is True:
        for prior in engine.store.jobs():
            if (prior.get('profile', {}).get('sec_uid') == author
                    and prior.get('latest', {}).get('video_id') == video):
                source = engine._saved_media(prior)
                if source is not None:
                    return source, valid_id(prior['check_id']), prior['media_checkpoint']['sha256']
    raise MediaUnavailable('Verified saved media is unavailable; retain this work as incomplete')


def _visual_root(paths, archive_id, video_id):
    archive_id, video_id = valid_id(archive_id), _vid(video_id)
    root = paths.reports / 'archives' / archive_id / 'visual' / video_id
    return _owned(root, paths.reports)


def _manifest(paths, archive_id, video_id, frame_set_id, check_id):
    root = _visual_root(paths, archive_id, video_id)
    frame_set_id, check_id = valid_id(frame_set_id), valid_id(check_id)
    folder = _owned(root / frame_set_id, root)
    manifest = _owned(folder / 'frames.json', folder, file=True)
    if manifest.stat().st_size > 128000:
        raise ValueError('Visual manifest is too large')
    value = json.loads(manifest.read_text(encoding='utf-8'))
    if (value.get('archive_id') != archive_id or value.get('video_id') != video_id
            or value.get('check_id') != check_id or value.get('frame_set_id') != frame_set_id
            or value.get('asr_status') not in {'no_speech', 'speech'}):
        raise ValueError('Visual manifest does not match the native archive work')
    duration = value.get('duration_seconds')
    if (isinstance(duration, bool) or not isinstance(duration, (float, int))
            or not math.isfinite(duration) or not 0 < duration <= 14400):
        raise ValueError('Invalid native video duration')
    if not isinstance(value.get('media_sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', value['media_sha256']):
        raise ValueError('Invalid native media checksum')
    valid_id(value.get('media_check_id'))
    frames = value.get('frames')
    if not isinstance(frames, list) or not 1 <= len(frames) <= 12:
        raise ValueError('Invalid number of sampled video frames')
    seen = set()
    for index, frame in enumerate(frames, 1):
        stamp = _number(frame.get('time_seconds'), duration)
        if stamp in seen:
            raise ValueError('Sampled frame timestamps must be distinct')
        seen.add(stamp)
        expected = folder / f'frame-{index:03d}.png'
        if Path(frame.get('path', '')).resolve() != expected.resolve():
            raise ValueError('Frame path does not match its owned native record')
        image = _owned(expected, folder, file=True)
        if not 0 < image.stat().st_size <= MAX_PNG_BYTES or image.stat().st_size != frame.get('bytes'):
            raise ValueError('Sampled frame size changed or exceeds its limit')
        with image.open('rb') as stream:
            if stream.read(8) != PNG:
                raise ValueError('Sampled frame is not a PNG')
        if _sha(image) != frame.get('sha256'):
            raise ValueError('Sampled frame checksum changed')
    return value


def _overview(value, frames):
    if not isinstance(value, dict):
        raise ValueError('A host-written visual overview is required')
    summary = _text(value.get('summary'))
    limits = value.get('limitations')
    observations = value.get('observations')
    if not isinstance(limits, list) or not 1 <= len(limits) <= 20:
        raise ValueError('State the visual sampling and speech evidence limitations')
    allowed = {float(frame['time_seconds']) for frame in frames}
    if not isinstance(observations, list) or len(observations) != len(allowed):
        raise ValueError('Describe every returned sampled frame')
    seen, clean = set(), []
    for row in observations:
        stamp = row.get('time_seconds') if isinstance(row, dict) else None
        if isinstance(stamp, bool) or not isinstance(stamp, (float, int)) or stamp not in allowed or stamp in seen:
            raise ValueError('Visual observations must match the actual sampled frame times')
        seen.add(stamp)
        clean.append({'time_seconds': float(stamp), 'description': _text(row.get('description'), 6000)})
    quality = value.get('speech_quality', 'not_assessed')
    if quality not in {'not_assessed', 'unresolved'}:
        raise ValueError('Invalid speech quality classification')
    note = _text(value.get('quality_note'), 4000) if quality == 'unresolved' else ''
    return {'summary': summary, 'observations': clean, 'limitations': [_text(x, 4000) for x in limits],
            'speech_quality': quality, 'quality_note': note}


def load_video_overview(paths, archive_id, video_id, check_id):
    """Load and verify the current native visual context, without networking."""
    root = _visual_root(paths, archive_id, video_id)
    pointer = root / 'overview.json'
    if not pointer.exists():
        return None
    pointer = _owned(pointer, root, file=True)
    if pointer.stat().st_size > 256000:
        raise ValueError('Visual overview is too large')
    result = json.loads(pointer.read_text(encoding='utf-8'))
    frames = _manifest(paths, archive_id, video_id, result.get('frame_set_id'), check_id)
    clean = _overview(result, frames['frames'])
    for key in ('archive_id', 'video_id', 'check_id', 'media_sha256', 'media_check_id', 'asr_status'):
        if result.get(key) != frames[key]:
            raise ValueError('Visual overview is not bound to its original native media')
    return dict(frames, **clean)


class ArchiveVisuals:
    def __init__(self, engine, archive_manager):
        self.engine, self.archives, self.paths = engine, archive_manager, engine.paths

    def _context(self, archive_id, video_id):
        archive_id, video_id = valid_id(archive_id), _vid(video_id)
        offset = 0
        while True:
            page = self.archives.report(archive_id, offset=offset, limit=300)
            work = next((item for item in page['works'] if item['video_id'] == video_id), None)
            if work:
                break
            offset = page['next_offset']
            if offset is None:
                raise ValueError('This work does not belong to the native archive')
        if page['status'] in {'queued', 'running'} or work['kind'] != 'video' or not work.get('check_id'):
            raise ValueError('Finish native video acquisition before sampling frames')
        check = self.engine.report(work['check_id'])
        if (check.get('archive_id') != archive_id or check.get('archive_video_id') != video_id
                or check.get('latest', {}).get('video_id') != video_id
                or check.get('latest', {}).get('url') != 'https://www.douyin.com/video/' + video_id
                or check.get('profile', {}).get('sec_uid') != page['profile']['sec_uid']
                or check['status'] not in {'done', 'partial', 'no_speech', 'error'}
                or (check['status'] == 'error' and check.get('error_code') != 'no_speech')):
            raise ValueError('A completed media acquisition from this exact archive is required')
        media, source_id, checksum = verified_media(self.engine, check)
        return check, media, source_id, checksum

    def capture(self, archive_id, video_id, timestamps=None):
        check, media, source_id, checksum = self._context(archive_id, video_id)
        import av
        frame_set = uuid.uuid4().hex
        root = _visual_root(self.paths, archive_id, video_id)
        folder = _owned(root / frame_set, self.paths.reports)
        with av.open(str(media)) as container:
            if not container.streams.video:
                raise ValueError('The saved media has no video stream')
            stream = container.streams.video[0]
            duration = float(stream.duration * stream.time_base) if stream.duration else float((container.duration or 0) / av.time_base)
            if not math.isfinite(duration) or not 0 < duration <= 14400:
                raise ValueError('Cannot determine a bounded actual video duration')
            requested = [duration * n for n in (.2, .5, .8)] if timestamps is None else timestamps
            if not isinstance(requested, list) or not 1 <= len(requested) <= 12:
                raise ValueError('Request between one and twelve frame times')
            requested = sorted(_number(x, duration) for x in requested)
            if len(set(requested)) != len(requested):
                raise ValueError('Requested frame times must be distinct')
            folder.mkdir(parents=True, exist_ok=False)
            frames, seen = [], set()
            for number, target in enumerate(requested, 1):
                container.seek(int(target / stream.time_base), stream=stream, backward=True)
                selected = None
                for frame in container.decode(stream):
                    if frame.time is not None and frame.time >= target:
                        selected = frame
                        break
                if selected is None or selected.time in seen or selected.time >= duration:
                    raise ValueError('The video cannot provide all distinct requested frame times')
                if selected.width * selected.height > 64_000_000:
                    raise ValueError('Decoded video frame exceeds the pixel limit')
                seen.add(selected.time)
                scale = min(1.0, 1280 / max(selected.width, selected.height))
                width, height = max(1, int(selected.width * scale)), max(1, int(selected.height * scale))
                image = selected.reformat(width=width, height=height, format='rgb24')
                encoder = av.CodecContext.create('png', 'w')
                encoder.width, encoder.height, encoder.pix_fmt = width, height, 'rgb24'
                packets = [*encoder.encode(image), *encoder.encode(None)]
                payload = b''.join(bytes(packet) for packet in packets)
                if not payload.startswith(PNG) or not 0 < len(payload) <= MAX_PNG_BYTES:
                    raise ValueError('Sampled frame encoding failed or exceeds the size limit')
                path = folder / f'frame-{number:03d}.png'
                with path.open('xb') as output:
                    output.write(payload)
                frames.append({'time_seconds': float(selected.time), 'path': str(path.resolve()),
                               'sha256': _sha(path), 'bytes': path.stat().st_size})
        if _sha(media) != checksum:
            raise ValueError('Saved media changed during frame sampling')
        result = {'archive_id': archive_id, 'video_id': video_id, 'check_id': check['check_id'],
                  'frame_set_id': frame_set, 'created_at': now(), 'duration_seconds': duration,
                  'media_check_id': source_id, 'media_sha256': checksum, 'frames': frames,
                  'asr_status': 'no_speech' if check.get('error_code') == 'no_speech' or check['status'] == 'no_speech' else 'speech'}
        _write_new(folder / 'frames.json', result)
        return result

    def submit(self, archive_id, video_id, frame_set_id, overview):
        check, _, source_id, checksum = self._context(archive_id, video_id)
        record = _manifest(self.paths, archive_id, video_id, frame_set_id, check['check_id'])
        if record['media_sha256'] != checksum or record['media_check_id'] != source_id:
            raise ValueError('Sampled frames no longer match the verified native media')
        clean = _overview(overview, record['frames'])
        record.update(clean, reviewed_at=now())
        root = _visual_root(self.paths, archive_id, video_id)
        revisions = _owned(root / 'revisions', root)
        revisions.mkdir(exist_ok=True)
        revision = revisions / (uuid.uuid4().hex + '.json')
        _write_new(revision, record)
        temporary = root / ('.overview-' + uuid.uuid4().hex + '.json')
        _write_new(temporary, record)
        temporary.replace(_owned(root / 'overview.json', root))
        return {'archive_id': archive_id, 'video_id': video_id, 'check_id': check['check_id'],
                'frame_set_id': frame_set_id, 'status': 'visual_context_saved',
                'asr_status': record['asr_status'], 'speech_quality': clean['speech_quality'],
                'frames': len(record['frames']), 'original_media_preserved': True,
                'overview_path': str((root / 'overview.json').resolve())}

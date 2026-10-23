"""Durable creator jobs. No dependency on a third-party application or daemon."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import uuid

from .paths import AppPaths
from .review import run_review, stamp, source_sha256

METRICS = ('like', 'comment', 'collect', 'share')
MODELS = {'fast': 'base', 'balanced': 'small', 'accurate': 'medium'}


class SavedMediaError(ValueError):
    code = 'saved_media_changed'


def now():
    return datetime.now(timezone.utc).isoformat()


def valid_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32}', value):
        raise ValueError('Invalid check ID')
    return value


def process_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name != 'nt':
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    api.GetExitCodeProcess.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    handle = api.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        result = wintypes.DWORD()
        return bool(api.GetExitCodeProcess(handle, ctypes.byref(result))) and result.value == 259
    finally:
        api.CloseHandle(handle)


class Store:
    def __init__(self, home):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.path = self.home / 'creator-notes.sqlite3'
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS identities(query TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS deliveries(author TEXT, video TEXT, payload TEXT NOT NULL,
                    PRIMARY KEY(author,video));
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, job):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO jobs VALUES(?,?)', (job['check_id'], json.dumps(job, ensure_ascii=False)))

    def get(self, check_id):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM jobs WHERE id=?', (valid_id(check_id),)).fetchone()
        if not row:
            raise ValueError('Check not found')
        return json.loads(row[0])

    def claim_resume(self, job, previous):
        """Claim a stopped job exactly once, including across MCP processes."""
        with self.connect() as db:
            cursor = db.execute('UPDATE jobs SET payload=? WHERE id=? AND payload=?',
                (json.dumps(job, ensure_ascii=False), job['check_id'], previous))
            return cursor.rowcount == 1

    def jobs(self):
        with self.connect() as db:
            rows = db.execute('SELECT payload FROM jobs ORDER BY rowid DESC').fetchall()
        return [json.loads(row[0]) for row in rows]

    def identity(self, query):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM identities WHERE query=?', (query.strip().casefold(),)).fetchone()
        return json.loads(row[0]) if row else None

    def bind(self, query, profile, replace=False):
        old = self.identity(query)
        if old and old['sec_uid'] != profile['sec_uid'] and not replace:
            raise ValueError('Identity changed; confirm profile and refresh identity explicitly')
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO identities VALUES(?,?)', (query.strip().casefold(), json.dumps(profile, ensure_ascii=False)))

    def history(self, author):
        with self.connect() as db:
            rows = db.execute('SELECT video,payload FROM deliveries WHERE author=?', (author,)).fetchall()
        return {row[0]: json.loads(row[1]) for row in rows}

    def deliver(self, job):
        # Machine transcription alone does not complete the requested product.
        if not job.get('complete') or not job.get('delivery_complete'):
            return
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO deliveries VALUES(?,?,?)',
                       (job['profile']['sec_uid'], job['latest']['video_id'], json.dumps(job, ensure_ascii=False)))


def export_report(job, transcript, destination):
    video = job['latest']
    folder = Path(destination) / job['check_id']
    folder.mkdir(parents=True, exist_ok=False)
    metrics = video['statistics']
    count = lambda value: 'Unavailable' if value is None else str(value)
    lines = [f"# {job['profile'].get('nickname', 'Douyin creator')}: {video.get('title', '')}", '',
             f"- Video: {video['url']}", f"- Published at (with timezone): {video.get('published_at', 'Unavailable')}",
             f"- Metrics sampled at (with timezone): {job.get('collected_at', now())}",
             '- ' + '; '.join(f'{label}: {count(metrics.get(key))}' for key, label in zip(METRICS, ('Likes', 'Comments', 'Saves', 'Shares'))),
             f"- Update state: {job['update_state']}", '',
             'This is the original local machine transcript. AI review is pending; speech does not establish all visual evidence.', '']
    segments = transcript['segments']
    timed = '\n'.join(f"[{stamp(s['start'])} → {stamp(s['end'])}] {s['text']}" for s in segments)
    srt = '\n\n'.join(f"{i}\n{stamp(s['start'], True)} --> {stamp(s['end'], True)}\n{s['text']}" for i, s in enumerate(segments, 1)) + '\n'
    assets = {'report.md': '\n'.join(lines) + timed + '\n',
              'segments.json': json.dumps(segments, ensure_ascii=False, indent=2),
              'transcript.txt': transcript['transcript'], 'transcript.srt': srt}
    for name, content in assets.items():
        (folder / name).write_text(content, encoding='utf-8')
    job['files'] = {name: str((folder / name).resolve()) for name in (*assets, 'report.json')}
    job['source_sha256'] = source_sha256(folder / 'segments.json')
    (folder / 'report.json').write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding='utf-8')
    # Keep this in SQLite after writing report.json, avoiding a self-referential hash.
    job['original_hashes'] = {name: source_sha256(path) for name, path in job['files'].items()}
    return job['files']


def originals_match(job):
    hashes = job.get('original_hashes', {})
    files = job.get('files', {})
    if not hashes or set(hashes) != set(files):
        return False
    try:
        return all(not Path(path).is_symlink() and source_sha256(path) == hashes[name]
                   for name, path in files.items())
    except OSError:
        return False


def media_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


class Manager:
    def __init__(self, paths=None, collector_factory=None, acquire=None, recognize=None):
        self.paths = (paths or AppPaths.current()).prepare()
        self.store = Store(self.paths.home)
        self.collector_factory = collector_factory
        self.acquire = acquire
        self.recognize = recognize
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='creator-notes')
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        for job in self.store.jobs():
            if job['status'] in ('queued', 'running') and not process_alive(job.get('owner_pid')):
                job.update(status='interrupted', complete=False, delivery_complete=False, stage='The previous process ended; retry this check', finished_at=now())
                self.store.save(job)

    def submit(self, creator, profile='balanced', refresh_identity=False):
        if not isinstance(creator, str) or not 1 <= len(creator.strip()) <= 256:
            raise ValueError('Provide a creator name, ID or profile URL (1-256 characters)')
        if profile not in MODELS:
            raise ValueError('Unknown transcription profile')
        creator = creator.strip()
        with self.lock:
            active = [job for job in self.store.jobs() if job['status'] in ('queued', 'running')]
            for job in active:
                if job['query'] == creator and job['asr_profile'] == profile and not job.get('archive_id'):
                    return job
            if len(active) >= 8:
                raise ValueError('Queue is full; wait for current checks')
            job = {'check_id': uuid.uuid4().hex, 'query': creator, 'asr_profile': profile,
                   'status': 'queued', 'stage': 'Waiting for local collection', 'complete': False,
                   'delivery_complete': False, 'review_status': 'not_started',
                   'owner_pid': os.getpid(), 'created_at': now(), 'refresh_identity': bool(refresh_identity)}
            self.store.save(job)
            self.pool.submit(self.run, job['check_id'], refresh_identity)
            return job

    def submit_archive_work(self, archive_id, identity, work, collected_at, profile='balanced'):
        """Transcribe one work from the controller's persisted, verified inventory.

        This entry does not discover arbitrary video URLs or call collect_latest.
        The archive controller selects the exact work from its owned inventory.
        """
        from .collector import SEC_UID
        archive_id = valid_id(archive_id)
        if profile not in MODELS or not isinstance(identity, dict) or not isinstance(work, dict):
            raise ValueError('Invalid archive work request')
        author = identity.get('sec_uid', '')
        video_id = work.get('video_id', '')
        if (not isinstance(author, str) or not SEC_UID.fullmatch(author)
                or not isinstance(video_id, str) or not re.fullmatch(r'[0-9]{5,30}', video_id)
                or work.get('url') != 'https://www.douyin.com/video/' + video_id
                or work.get('author_sec_uid') not in ('', author)
                or work.get('kind') not in ('video', 'image')):
            raise ValueError('The archived work does not match its verified creator')
        statistics = work.get('statistics')
        if not isinstance(statistics, dict) or set(statistics) != set(METRICS):
            raise ValueError('Invalid archived metrics')
        if any(value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0)
               for value in statistics.values()):
            raise ValueError('Invalid archived metric value')
        try:
            if datetime.fromisoformat(collected_at).tzinfo is None:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError('An archive metric sampling time with timezone is required') from None
        # Copy only public fields; the inventory cannot inject paths or job state.
        identity = {key: identity.get(key, '') for key in
                    ('sec_uid', 'nickname', 'unique_id', 'short_id', 'uid', 'profile_url', 'signature')}
        work = {key: work.get(key) for key in
                ('video_id', 'title', 'create_time', 'published_at', 'url', 'duration_seconds',
                 'statistics', 'pinned', 'kind', 'aweme_type', 'author_sec_uid')}
        with self.lock:
            for entry in self.store.jobs():
                if (entry.get('archive_id') == archive_id and entry.get('archive_video_id') == video_id
                        and entry['asr_profile'] == profile):
                    return entry
            if self.stopping.is_set():
                raise ValueError('The worker is stopping; reconnect before submitting archive work')
            if len([entry for entry in self.store.jobs() if entry['status'] in ('queued', 'running')]) >= 8:
                raise ValueError('Queue is full; wait for current checks')
            source = json.loads(json.dumps({'profile': identity, 'latest': work,
                'coverage': {'selection': 'exact work from a verified public creator inventory',
                             'archive_id': archive_id}, 'collected_at': collected_at}, allow_nan=False))
            job = {'check_id': uuid.uuid4().hex, 'query': identity['unique_id'] or identity['profile_url'],
                   'asr_profile': profile, 'status': 'queued', 'stage': 'Waiting to transcribe archived work',
                   'complete': False, 'delivery_complete': False, 'review_status': 'not_started',
                   'owner_pid': os.getpid(), 'created_at': now(), 'refresh_identity': False,
                   'archive_id': archive_id, 'archive_video_id': video_id, 'archive_source': source}
            self.store.save(job)
            self.pool.submit(self.run, job['check_id'], False)
            return job

    def _saved_media(self, job):
        checkpoint = job.get('media_checkpoint')
        if not isinstance(checkpoint, dict):
            return None
        try:
            path = Path(checkpoint['path'])
            owned = self.paths.work / valid_id(job['check_id'])
            if owned.is_symlink() or (hasattr(owned, 'is_junction') and owned.is_junction()):
                return None
            expected = owned.resolve()
            if (path.is_symlink() or not path.is_file() or path.resolve().parent != expected
                    or expected.parent != self.paths.work.resolve()
                    or any(item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction())
                           for item in (path.parent, self.paths.work))
                    or path.stat().st_size != checkpoint['bytes']
                    or checkpoint['video_id'] != job['latest']['video_id']
                    or checkpoint['author'] != job['profile']['sec_uid']
                    or media_sha256(path) != checkpoint['sha256']):
                return None
            return path.resolve()
        except (OSError, KeyError, TypeError, ValueError):
            return None

    def resume(self, check_id, profile=None):
        """Continue the same stopped job; never solve or repeatedly retry a CAPTCHA."""
        with self.lock:
            if self.stopping.is_set():
                raise ValueError('The worker is stopping; reconnect the MCP service before resuming')
            job = self.store.get(check_id)
            previous_payload = json.dumps(job, ensure_ascii=False)
            if job['status'] in ('queued', 'running'):
                return job
            if profile is not None and profile not in MODELS:
                raise ValueError('Unknown transcription profile')
            blocked = job['status'] in ('captcha', 'login_required')
            model_error = job['status'] == 'error' and job.get('error_code') in {
                'model_unavailable', 'dependency_missing', 'transcription_failed', 'invalid_timeline'}
            if not (blocked or model_error or job['status'] == 'interrupted') or job.get('files'):
                raise ValueError('This job cannot resume; start a new check instead')
            if blocked:
                from .login import login_status
                state = login_status(self.paths.session)
                try:
                    verified_at = datetime.fromisoformat(state.get('updated_at', ''))
                    blocked_at = datetime.fromisoformat(job['finished_at'])
                    fresh = verified_at > blocked_at
                except (ValueError, TypeError, KeyError):
                    fresh = False
                if (state.get('status') != 'logged_in' or not state.get('storage_state_ready')
                        or not fresh):
                    raise ValueError('Complete a new dedicated login or human verification before resuming')
            if job.get('media_checkpoint') and self._saved_media(job) is None:
                raise ValueError('Saved media changed or is missing; start a new check')
            if len([entry for entry in self.store.jobs() if entry['status'] in ('queued', 'running')]) >= 8:
                raise ValueError('Queue is full; wait for current checks')
            failures = job.get('attempt_history', [])
            failures.append({key: job[key] for key in ('status', 'error_code', 'stage', 'finished_at') if key in job})
            for key in ('error_code', 'finished_at', 'progress'):
                job.pop(key, None)
            job.update(status='queued', stage='Continuing the saved check', owner_pid=os.getpid(),
                       asr_profile=profile or job['asr_profile'], attempt_history=failures[-20:],
                       resumed_at=now(), resume_count=job.get('resume_count', 0) + 1)
            if not self.store.claim_resume(job, previous_payload):
                current = self.store.get(check_id)
                if current['status'] in ('queued', 'running'):
                    return current
                raise ValueError('The saved check changed; read its latest status before resuming')
            try:
                self.pool.submit(self.run, check_id, job.get('refresh_identity', False))
            except RuntimeError:
                job.update(status='interrupted', stage='Worker stopped; reconnect before resuming', finished_at=now())
                self.store.save(job)
                raise ValueError('The worker stopped; reconnect the MCP service before resuming') from None
            return job

    def run(self, check_id, refresh_identity=False):
        job = self.store.get(check_id)

        def update(**fields):
            job.update(fields)
            self.store.save(job)

        def progress(stage, fraction=None):
            if self.stopping.is_set():
                raise InterruptedError('Stopping local worker')
            update(stage=str(stage), progress=fraction)

        try:
            update(status='running', resume_available=False)
            saved_media = self._saved_media(job)
            if job.get('media_checkpoint') and saved_media is None:
                raise SavedMediaError('Saved media changed; start a new check')
            requested_model = MODELS[job['asr_profile']]
            selection = {'requested_model': requested_model, 'selected_model': requested_model,
                         'reason': 'injected_recognizer'}
            if self.recognize is None:
                from .recognition import ensure_model_cache, select_model
                progress('Checking the local speech model before opening Douyin')
                update(model_selection=select_model(requested_model, self.paths.models))
                selection = ensure_model_cache(self.paths.models, requested_model, progress=progress)
            update(model_selection=selection)
            model = selection['selected_model']
            progress('Reusing saved video data and audio' if saved_media else 'Verifying creator identity')

            async def collect():
                factory = self.collector_factory
                if factory is None:
                    from .collector import BrowserCollector
                    factory = BrowserCollector
                async with factory(data_dir=self.paths.session) as collector:
                    identity = None if refresh_identity else self.store.identity(job['query'])
                    if identity is None:
                        resolved = await collector.resolve_creator(job['query'])
                        if resolved.get('status') != 'resolved':
                            update(status='needs_selection', candidates=resolved.get('candidates', []),
                                   stage='Select one verified creator profile', finished_at=now())
                            return None
                        identity = resolved['profile']
                        self.store.bind(job['query'], identity, replace=refresh_identity)
                    update(profile=identity, stage='Reading the newest work and four engagement metrics')
                    return await collector.collect_latest(identity)

            collected = ({'latest': job['latest'], 'profile': job['profile'],
                          'coverage': job.get('coverage'), 'collected_at': job['collected_at']}
                         if saved_media else job.get('archive_source') or asyncio.run(collect()))
            if collected is None:
                return
            video, identity = collected['latest'], collected.get('profile') or job['profile']
            history = self.store.history(identity['sec_uid'])
            previous = history.get(video['video_id'])
            latest_time = max((entry['latest'].get('create_time') or 0 for entry in history.values()), default=0)
            state = ('archive_item' if job.get('archive_id') else
                'no_update' if previous else ('first_check' if not history else
                'new_video' if (video.get('create_time') or 0) > latest_time else 'previously_unseen_latest'))
            update(profile=identity, latest=video, update_state=state, coverage=collected.get('coverage'), collected_at=collected['collected_at'])
            if video.get('kind') == 'image':
                update(status='unsupported_latest', stage=('This archived work is an image post; speech transcription is unavailable'
                    if job.get('archive_id') else 'The newest work is an image post; no spoken transcript can be delivered'), finished_at=now())
                return
            missing = [name for name in METRICS if video['statistics'].get(name) is None]
            update(missing_metrics=missing)
            cached = next((entry for entry in self.store.jobs()
                if entry.get('status') in ('done', 'partial') and entry.get('latest', {}).get('video_id') == video['video_id']
                and entry.get('profile', {}).get('sec_uid') == identity['sec_uid']
                and entry.get('transcription', {}).get('model') == model and entry.get('files')), None)
            transcript = None
            if cached:
                try:
                    raw = Path(cached['files']['segments.json'])
                    if originals_match(cached) and source_sha256(raw) == cached['source_sha256']:
                        transcript = {'segments': json.loads(raw.read_text(encoding='utf-8')),
                            'transcript': Path(cached['files']['transcript.txt']).read_text(encoding='utf-8'),
                            'duration': cached['transcription']['duration_seconds'], 'model': model}
                        if cached['transcription'].get('timeline_recovery'):
                            transcript['timeline_recovery'] = cached['transcription']['timeline_recovery']
                except (OSError, ValueError, KeyError):
                    transcript = None
            reused = transcript is not None
            if transcript is None:
                progress('Reusing saved audio' if saved_media else 'Acquiring video audio')
                acquire = self.acquire
                if acquire is None:
                    from .media import acquire_video
                    acquire = acquire_video
                recognize = self.recognize
                if recognize is None:
                    from .recognition import transcribe_file
                    recognize = transcribe_file
                media = saved_media or acquire(video['url'], self.paths.work / check_id,
                    state_path=self.paths.session / 'storage-state.json', progress=progress)
                # A complete download survives ASR/model failures. Bind it to
                # this exact creator/video and check it before every reuse.
                media = Path(media)
                if media.is_file() and media.resolve().parent == (self.paths.work / check_id).resolve():
                    update(media_checkpoint={'path': str(media.resolve()), 'bytes': media.stat().st_size,
                        'sha256': media_sha256(media), 'video_id': video['video_id'], 'author': identity['sec_uid']})
                transcript = recognize(media, self.paths.models, model=model, progress=progress)
            if not transcript.get('segments') or not transcript.get('transcript', '').strip():
                update(status='no_speech', stage='No deliverable speech was recognized', finished_at=now())
                return
            job.update(status='partial' if missing else 'done', complete=not missing, review_status='pending',
                       stage='Raw transcript ready; host AI review, corrections, and overview are pending', finished_at=now(),
                       transcription={'model': transcript.get('model', model), 'requested_model': requested_model,
                           'media_reused': bool(saved_media), 'duration_seconds': transcript['duration'],
                           'segments': len(transcript['segments']), 'characters': len(transcript['transcript']),
                           'reused': reused, 'machine_transcript': True})
            if transcript.get('timeline_recovery'):
                # Recognition records controlled timing facts separately from
                # the successful transcript; keep them in the native report.
                job['transcription']['timeline_recovery'] = transcript['timeline_recovery']
            export_report(job, transcript, self.paths.reports)
            self.store.save(job)
        except Exception as exc:
            code = 'dependency_missing' if isinstance(exc, ImportError) else getattr(exc, 'code', type(exc).__name__)
            # Never serialize arbitrary exception strings, URLs or credentials.
            known = {'captcha', 'login_required', 'identity_unverified', 'no_data', 'not_found', 'ambiguous'}
            status = 'interrupted' if isinstance(exc, InterruptedError) else code if code in known else 'error'
            from .recognition import RecognitionError
            if isinstance(exc, RecognitionError) and getattr(exc, 'timeline_recovery', None):
                update(timeline_recovery=exc.timeline_recovery)
            update(status=status, complete=False,
                   delivery_complete=False, error_code=code,
                   resume_available=status in ('captcha', 'login_required', 'interrupted') or code in
                       ('model_unavailable', 'dependency_missing', 'transcription_failed', 'invalid_timeline'),
                   stage=('Human verification is required; complete it in the dedicated login window before resuming'
                          if status == 'captcha' else 'Check incomplete; saved work is retained for retry'), finished_at=now())

    def report(self, check_id):
        return self.store.get(check_id)

    def transcript(self, check_id, offset=0, limit=80):
        job = self.report(check_id)
        if job['status'] not in ('done', 'partial'):
            return {'check_id': check_id, 'status': job['status'], 'stage': job['stage']}
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValueError('Offset must be a nonnegative integer')
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 300:
            raise ValueError('Limit must be between 1 and 300')
        path = Path(job['files']['segments.json'])
        if not originals_match(job) or source_sha256(path) != job['source_sha256']:
            raise ValueError('Original transcript changed')
        source = json.loads(path.read_text(encoding='utf-8'))
        page = source[offset:offset + limit]
        end = offset + len(page)
        return {'check_id': check_id, 'status': job['status'], 'source_sha256': job['source_sha256'],
                'total_segments': len(source), 'offset': offset,
                'segments': [dict(item, index=offset+i) for i, item in enumerate(page)],
                'next_offset': end if end < len(source) else None, 'files': job['files']}

    def review(self, check_id, request):
        with self.lock:
            job = self.report(check_id)
            if job['status'] not in ('done', 'partial'):
                raise ValueError('Raw transcript is not ready')
            if not originals_match(job) or request.get('source_sha256') != job['source_sha256']:
                raise ValueError('Original transcript changed; review cannot advance delivery')
            result = run_review(job['files']['report.json'], request)
            job.update(review=result, review_status=result['status'], delivery_complete=bool(job['complete']),
                       stage='Review completed with unresolved wording' if result['pending_segments'] else 'Reviewed transcript and overview ready')
            self.store.save(job)
            self.store.deliver(job)
            return result

    async def resolve(self, creator, urls):
        from .discovery import resolve_from_public_videos
        result = await asyncio.wait_for(resolve_from_public_videos(creator, urls, self.paths.session), timeout=120)
        if result.get('status') == 'resolved' and result.get('profile'):
            self.store.bind(creator, result['profile'])
            result['binding_saved'] = True
        return result

    def close(self):
        with self.lock:
            self.stopping.set()
        self.pool.shutdown(wait=True, cancel_futures=True)

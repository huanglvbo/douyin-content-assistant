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
                if job['query'] == creator and job['asr_profile'] == profile:
                    return job
            if len(active) >= 8:
                raise ValueError('Queue is full; wait for current checks')
            job = {'check_id': uuid.uuid4().hex, 'query': creator, 'asr_profile': profile,
                   'status': 'queued', 'stage': 'Waiting for local collection', 'complete': False,
                   'delivery_complete': False, 'review_status': 'not_started',
                   'owner_pid': os.getpid(), 'created_at': now()}
            self.store.save(job)
            self.pool.submit(self.run, job['check_id'], refresh_identity)
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
            update(status='running')
            progress('Verifying creator identity')

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

            collected = asyncio.run(collect())
            if collected is None:
                return
            video, identity = collected['latest'], collected.get('profile') or job['profile']
            history = self.store.history(identity['sec_uid'])
            previous = history.get(video['video_id'])
            latest_time = max((entry['latest'].get('create_time') or 0 for entry in history.values()), default=0)
            state = 'no_update' if previous else ('first_check' if not history else
                'new_video' if (video.get('create_time') or 0) > latest_time else 'previously_unseen_latest')
            update(profile=identity, latest=video, update_state=state, coverage=collected.get('coverage'), collected_at=collected['collected_at'])
            if video.get('kind') == 'image':
                update(status='unsupported_latest', stage='The newest work is an image post; no spoken transcript can be delivered', finished_at=now())
                return
            missing = [name for name in METRICS if video['statistics'].get(name) is None]
            update(missing_metrics=missing)
            model = MODELS[job['asr_profile']]
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
                except (OSError, ValueError, KeyError):
                    transcript = None
            reused = transcript is not None
            if transcript is None:
                progress('Acquiring video audio')
                acquire = self.acquire
                if acquire is None:
                    from .media import acquire_video
                    acquire = acquire_video
                recognize = self.recognize
                if recognize is None:
                    from .recognition import transcribe_file
                    recognize = transcribe_file
                media = acquire(video['url'], self.paths.work / check_id,
                    state_path=self.paths.session / 'storage-state.json', progress=progress)
                transcript = recognize(media, self.paths.models, model=model, progress=progress)
            if not transcript.get('segments') or not transcript.get('transcript', '').strip():
                update(status='no_speech', stage='No deliverable speech was recognized', finished_at=now())
                return
            job.update(status='partial' if missing else 'done', complete=not missing, review_status='pending',
                       stage='Raw transcript ready; host AI review, corrections, and overview are pending', finished_at=now(),
                       transcription={'model': model, 'duration_seconds': transcript['duration'],
                           'segments': len(transcript['segments']), 'characters': len(transcript['transcript']),
                           'reused': reused, 'machine_transcript': True})
            export_report(job, transcript, self.paths.reports)
            self.store.save(job)
        except Exception as exc:
            code = 'dependency_missing' if isinstance(exc, ImportError) else getattr(exc, 'code', type(exc).__name__)
            # Never serialize arbitrary exception strings, URLs or credentials.
            known = {'captcha', 'login_required', 'identity_unverified', 'no_data', 'not_found', 'ambiguous'}
            status = 'interrupted' if isinstance(exc, InterruptedError) else code if code in known else 'error'
            update(status=status, complete=False,
                   delivery_complete=False, error_code=code, stage='Check incomplete; delivery history was not advanced', finished_at=now())

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
        self.stopping.set()
        self.pool.shutdown(wait=True, cancel_futures=True)

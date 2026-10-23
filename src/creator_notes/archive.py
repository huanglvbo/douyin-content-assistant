"""Durable public-work inventories, separate from speech and review jobs.

An inventory is complete only when the collector explicitly observes the end
of the creator's public works. A bounded or challenged read remains partial.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import csv
from datetime import datetime
import html
import io
import json
import math
import os
from pathlib import Path
import re
import threading
import time
import uuid

from .engine import MODELS, now, process_alive, valid_id


ACTIVE = {'queued', 'running'}
TERMINAL = {'inventory_done', 'inventory_partial', 'captcha', 'login_required',
            'needs_selection', 'error', 'interrupted'}
METRICS = ('like', 'comment', 'collect', 'share')
PROFILE_FIELDS = ('nickname', 'unique_id', 'short_id', 'uid', 'signature', 'matched_by')
COVERAGE_FIELDS = {
    'end_observed', 'all_history_scanned', 'count_mismatch', 'advertised_count',
    'advertised_work_count', 'advertised_public_works', 'expected_count', 'pages_captured', 'scrolls',
    'scroll_count', 'scroll_attempts', 'elapsed_seconds', 'has_more', 'stop_reason',
    'unique_works', 'works_collected', 'max_scrolls', 'max_seconds',
    'oldest_create_time', 'newest_create_time', 'visible_count', 'scope',
    'unavailable_metrics', 'identity_verified', 'scrolls_attempted',
    'rejected_author_rows', 'pagination_progress',
    'pagination_complete', 'chain_status',
}


def _integer(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _text(value, limit=4096):
    return value[:limit] if isinstance(value, str) else ''


def _date(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.isoformat() if parsed.tzinfo else None
    except ValueError:
        return None


def _profile(value):
    if not isinstance(value, dict):
        raise ValueError('A verified public creator profile is required')
    sec_uid = value.get('sec_uid')
    if not isinstance(sec_uid, str) or not re.fullmatch(r'[A-Za-z0-9_=-]{1,256}', sec_uid):
        raise ValueError('Invalid verified creator identifier')
    result = {key: _text(value.get(key)) for key in PROFILE_FIELDS if key in value}
    result.update(sec_uid=sec_uid, profile_url=f'https://www.douyin.com/user/{sec_uid}')
    return result


def _work(value, sec_uid):
    if not isinstance(value, dict):
        raise ValueError('Invalid public work')
    video_id = value.get('video_id')
    if not isinstance(video_id, str) or not re.fullmatch(r'[0-9]{1,32}', video_id):
        raise ValueError('Invalid public work identifier')
    author = value.get('author_sec_uid')
    if author and author != sec_uid:
        raise ValueError('Inventory work belongs to another creator')
    statistics = value.get('statistics')
    statistics = statistics if isinstance(statistics, dict) else {}
    duration = value.get('duration_seconds')
    if (isinstance(duration, bool) or not isinstance(duration, (int, float))
            or not math.isfinite(duration) or duration < 0):
        duration = None
    result = {
        'video_id': video_id, 'url': f'https://www.douyin.com/video/{video_id}',
        'title': _text(value.get('title'), 16384), 'kind': 'image' if value.get('kind') == 'image' else 'video',
        'create_time': _integer(value.get('create_time')),
        'published_at': _date(value.get('published_at')), 'duration_seconds': duration,
        'statistics': {key: _integer(statistics.get(key)) for key in METRICS},
        'pinned': bool(value.get('pinned')), 'aweme_type': _integer(value.get('aweme_type')),
        'author_sec_uid': sec_uid,
    }
    for key in ('sampled_at', 'collected_at'):
        if _date(value.get(key)):
            result[key] = _date(value[key])
    return result


def _coverage(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in COVERAGE_FIELDS & value.keys():
        item = value[key]
        if key == 'pagination_progress' and isinstance(item, list):
            progress = []
            for page in item[:10000]:
                if not isinstance(page, dict):
                    continue
                accepted = {}
                for field in ('sequence', 'request_cursor', 'response_cursor', 'has_more', 'items'):
                    raw = page.get(field)
                    if raw is None or (isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0):
                        accepted[field] = raw
                    elif field in ('request_cursor', 'response_cursor') and isinstance(raw, str) and re.fullmatch(r'[0-9]{1,32}', raw):
                        accepted[field] = raw
                progress.append(accepted)
            result[key] = progress
        elif item is None or isinstance(item, bool):
            result[key] = item
        elif isinstance(item, (int, float)) and math.isfinite(item):
            result[key] = item
        elif isinstance(item, str):
            # Diagnostic fields must contain controlled labels, never URLs,
            # request bodies, or arbitrary exception messages.
            if key in ('scope', 'stop_reason', 'chain_status') and re.fullmatch(r'[A-Za-z0-9_ .-]{1,96}', item):
                result[key] = item
    return result


def _complete(coverage, total):
    if coverage.get('all_history_scanned') is not True or coverage.get('end_observed') is not True:
        return False
    advertised = any(coverage.get(key) is not None for key in
                     ('advertised_count', 'advertised_work_count', 'advertised_public_works', 'expected_count'))
    if advertised:
        counts = [coverage[key] for key in ('advertised_count', 'advertised_work_count',
                  'advertised_public_works', 'expected_count') if coverage.get(key) is not None]
        return coverage.get('count_mismatch') is False and all(_integer(count) == total for count in counts)
    return coverage.get('count_mismatch') is not True


def _atomic(path, text):
    path = Path(path)
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        with temporary.open('x', encoding='utf-8', newline='') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _csv_text(value):
    value = '' if value is None else str(value)
    return "'" + value if value.startswith(('=', '+', '-', '@', '\t', '\r')) else value


class ArchiveManager:
    """One inventory worker with atomic snapshots and durable check links."""

    def __init__(self, engine):
        self.engine = engine
        self.paths = engine.paths
        self.directory = self.paths.home / 'archives'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='creator-notes-archive')
        self.stopping = threading.Event()
        self.owned = set()
        with self.lock, self._admission():
            for job in self._jobs():
                self._interrupt_stale(job)

    @contextmanager
    def _admission(self):
        """Serialize admission with an OS lock that is released on process exit."""
        path = self.directory / '.admission.lock'
        stream = path.open('a+b')
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b'\0')
            stream.flush()
        deadline = time.monotonic() + 3
        acquired = False
        try:
            while not acquired:
                try:
                    stream.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError:
                    if time.monotonic() >= deadline:
                        raise ValueError('Archive admission is busy; retry after the current operation') from None
                    time.sleep(.02)
            yield
        finally:
            try:
                if acquired:
                    stream.seek(0)
                    if os.name == 'nt':
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            finally:
                stream.close()

    def _load(self, archive_id):
        path = self.directory / f'{valid_id(archive_id)}.json'
        if path.is_symlink():
            raise ValueError('Invalid archive file')
        try:
            job = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            raise ValueError('Archive not found') from None
        if not isinstance(job, dict) or job.get('archive_id') != archive_id:
            raise ValueError('Invalid archive file')
        return job

    def _jobs(self):
        result = []
        for path in self.directory.glob('*.json'):
            if re.fullmatch(r'[a-f0-9]{32}', path.stem):
                result.append(self._load(path.stem))
        return result

    def _save(self, job):
        job['updated_at'] = now()
        _atomic(self.directory / f"{valid_id(job['archive_id'])}.json", json.dumps(job, ensure_ascii=False, indent=2))

    def _interrupt_stale(self, job):
        if job['status'] in ACTIVE and not process_alive(job.get('owner_pid')):
            job.update(status='interrupted', inventory_complete=False,
                       stage='The previous inventory process ended; captured public works are retained', finished_at=now())
            self._export(job)
            self._save(job)
        return job

    def _export(self, job):
        folder = self.paths.reports / 'archives' / valid_id(job['archive_id'])
        folder.mkdir(parents=True, exist_ok=True)
        if folder.is_symlink() or (hasattr(folder, 'is_junction') and folder.is_junction()):
            raise ValueError('Invalid archive export directory')
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        fields = ('video_id', 'url', 'title', 'kind', 'published_at', 'duration_seconds',
                  'like', 'comment', 'collect', 'share', 'sampled_at', 'check_id')
        writer.writerow(fields)
        lines = ['# Public creator works inventory', '', f"- Archive ID: {job['archive_id']}",
                 f"- Status: {job['status']}", f"- Public works captured: {len(job['works'])}",
                 f"- Complete public enumeration: {'Yes' if job.get('inventory_complete') else 'No'}",
                 f"- Sampled at: {job.get('collected_at') or 'Unavailable'}", '',
                 'Unknown counters remain unavailable. This inventory does not establish speech transcription or AI review completion.', '',
                 '| Published at | Work | Likes | Comments | Saves | Shares |',
                 '| --- | --- | ---: | ---: | ---: | ---: |']
        for work in job['works']:
            statistics = work['statistics']
            linked = job.get('submissions', {}).get(work['video_id'], {})
            row = {key: work.get(key) for key in fields}
            row.update(statistics, sampled_at=work.get('sampled_at') or work.get('collected_at') or job.get('collected_at'),
                       check_id=linked.get('check_id'))
            writer.writerow([_csv_text(row.get(key)) for key in fields])
            title = html.escape(work.get('title', '').replace('\r', ' ').replace('\n', ' ')).replace('|', '&#124;').replace('[', '&#91;').replace(']', '&#93;')
            counts = ' | '.join('Unavailable' if statistics[key] is None else str(statistics[key]) for key in METRICS)
            lines.append(f"| {work.get('published_at') or 'Unavailable'} | [{title or work['video_id']}]({work['url']}) | {counts} |")
        _atomic(folder / 'inventory.csv', output.getvalue())
        _atomic(folder / 'inventory.md', '\n'.join(lines) + '\n')
        job['export_paths'] = {'inventory.csv': str((folder / 'inventory.csv').resolve()),
                               'inventory.md': str((folder / 'inventory.md').resolve())}

    def start(self, creator, max_scrolls=2000, max_seconds=1800):
        if not isinstance(creator, str) or not 1 <= len(creator.strip()) <= 256:
            raise ValueError('Provide a creator name, ID or profile URL (1-256 characters)')
        for value, maximum, label in ((max_scrolls, 2000, 'Max scrolls'), (max_seconds, 7200, 'Max seconds')):
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
                raise ValueError(f'{label} must be between 1 and {maximum}')
        creator = creator.strip()
        with self.lock, self._admission():
            if self.stopping.is_set():
                raise ValueError('The inventory worker is stopping; reconnect the MCP service')
            bound = self.engine.store.identity(creator)
            verified = _profile(bound) if bound else None
            key = verified['sec_uid'] if verified else creator.casefold()
            active = []
            for entry in self._jobs():
                self._interrupt_stale(entry)
                if entry['status'] in ACTIVE:
                    active.append(entry)
                    if entry.get('creator_key') == key or entry['query'].casefold() == creator.casefold():
                        return self._summary(entry)
            if len(active) >= 4:
                raise ValueError('Inventory queue is full; wait for the active inventory')
            job = {'archive_id': uuid.uuid4().hex, 'query': creator, 'creator_key': key,
                   'status': 'queued', 'stage': 'Waiting to enumerate public creator works',
                   'inventory_complete': False, 'works': [], 'coverage': {}, 'submissions': {},
                   'profile': verified, 'created_at': now(), 'owner_pid': os.getpid(),
                   'max_scrolls': max_scrolls, 'max_seconds': max_seconds}
            self._save(job)
            self.owned.add(job['archive_id'])
            self.pool.submit(self._run, job['archive_id'])
            return self._summary(job)

    @staticmethod
    def _summary(job):
        return {key: job[key] for key in ('archive_id', 'query', 'status', 'stage', 'inventory_complete')}

    def _snapshot(self, job, snapshot):
        if not isinstance(snapshot, dict):
            raise ValueError('Invalid inventory snapshot')
        verified = _profile(snapshot.get('profile') or job.get('profile'))
        if job.get('profile') and verified['sec_uid'] != job['profile']['sec_uid']:
            raise ValueError('Inventory creator identity changed')
        rows = snapshot.get('works')
        if not isinstance(rows, list):
            raise ValueError('Inventory snapshot must contain public works')
        merged = {work['video_id']: work for work in job['works']}
        for row in rows:
            item = _work(row, verified['sec_uid'])
            merged[item['video_id']] = item
        job.update(profile=verified, creator_key=verified['sec_uid'],
                   works=sorted(merged.values(), key=lambda row: (row.get('create_time') or 0, row['video_id']), reverse=True),
                   coverage=_coverage(snapshot.get('coverage', job.get('coverage', {}))),
                   collected_at=_date(snapshot.get('collected_at')) or job.get('collected_at') or now())
        self._export(job)
        self._save(job)

    def _run(self, archive_id):
        with self.lock:
            job = self._load(archive_id)
            job.update(status='running', stage='Resolving the creator and enumerating public works')
            self._save(job)
        try:
            async def collect():
                factory = self.engine.collector_factory
                if factory is None:
                    from .collector import BrowserCollector
                    factory = BrowserCollector
                async with factory(data_dir=self.paths.session) as collector:
                    if job.get('profile') is None:
                        result = await collector.resolve_creator(job['query'])
                        if result.get('status') != 'resolved':
                            job.update(status='needs_selection', stage='Select one verified creator profile',
                                       candidates=[_profile(row) for row in result.get('candidates', [])], finished_at=now())
                            return None
                        job['profile'] = _profile(result['profile'])
                        job['creator_key'] = job['profile']['sec_uid']
                        self.engine.store.bind(job['query'], job['profile'])
                        with self.lock:
                            self._save(job)

                    def progress(snapshot):
                        if self.stopping.is_set():
                            raise InterruptedError('Inventory worker stopping')
                        with self.lock:
                            self._snapshot(job, snapshot)

                    return await collector.collect_archive(job['profile'], max_scrolls=job['max_scrolls'],
                        max_seconds=job['max_seconds'], on_progress=progress)

            result = asyncio.run(collect())
            with self.lock:
                if result is not None:
                    self._snapshot(job, result)
                    complete = _complete(job['coverage'], len(job['works']))
                    job.update(status='inventory_done' if complete else 'inventory_partial', inventory_complete=complete,
                               stage='Public creator inventory ready' if complete else 'Public inventory retained with incomplete enumeration',
                               finished_at=now())
                self._export(job)
                self._save(job)
        except Exception as exc:
            code = getattr(exc, 'code', None)
            status = code if code in {'captcha', 'login_required'} else 'needs_selection' if code == 'ambiguous' else (
                'interrupted' if isinstance(exc, InterruptedError) else 'error')
            with self.lock:
                job.update(status=status, inventory_complete=False, stage=(
                    'Human verification is required; retained public works remain available' if status == 'captcha' else
                    'A dedicated login is required; retained public works remain available' if status == 'login_required' else
                    'Public inventory incomplete; captured works are retained'), finished_at=now(),
                    error_code=code if isinstance(code, str) and re.fullmatch(r'[a-z_]{1,48}', code) else 'inventory_failed')
                self._export(job)
                self._save(job)

    def report(self, archive_id, offset=0, limit=50):
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValueError('Offset must be a nonnegative integer')
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 300:
            raise ValueError('Limit must be between 1 and 300')
        with self.lock:
            job = self._interrupt_stale(self._load(archive_id))
            rows = []
            for source in job['works'][offset:offset + limit]:
                row = dict(source)
                linked = job.get('submissions', {}).get(row['video_id'])
                if linked:
                    row['check_id'] = linked['check_id']
                    try:
                        check = self.engine.report(linked['check_id'])
                        row.update(check_status=check['status'], delivery_complete=bool(check.get('delivery_complete')))
                    except ValueError:
                        row['check_status'] = 'unavailable'
                rows.append(row)
            end = offset + len(rows)
            result = {key: value for key, value in job.items() if key not in ('works', 'submissions', 'creator_key', 'owner_pid')}
            result.update(works=rows, total_works=len(job['works']), offset=offset,
                          next_offset=end if end < len(job['works']) else None)
            return result

    def submit_work(self, archive_id, video_id, profile='balanced'):
        if not isinstance(video_id, str) or not re.fullmatch(r'[0-9]{1,32}', video_id):
            raise ValueError('Invalid public work identifier')
        if not isinstance(profile, str) or profile not in MODELS:
            raise ValueError('Unknown transcription profile')
        with self.lock, self._admission():
            job = self._interrupt_stale(self._load(archive_id))
            if job['status'] in ACTIVE:
                raise ValueError('Wait for public inventory enumeration before starting speech jobs')
            if job['status'] not in TERMINAL or not job.get('profile'):
                raise ValueError('A verified inventory is required')
            work = next((row for row in job['works'] if row['video_id'] == video_id), None)
            if work is None:
                raise ValueError('This work does not belong to the persisted inventory')
            linked = job.get('submissions', {}).get(video_id)
            if linked:
                return self.engine.report(linked['check_id'])
            result = self.engine.submit_archive_work(archive_id, _profile(job['profile']),
                _work(work, job['profile']['sec_uid']),
                work.get('sampled_at') or work.get('collected_at') or job.get('collected_at'), profile)
            check_id = valid_id(result['check_id'])
            job.setdefault('submissions', {})[video_id] = {
                'check_id': check_id, 'submitted_at': now(), 'asr_profile': profile}
            self._export(job)
            self._save(job)
            return result

    def close(self):
        self.stopping.set()
        self.pool.shutdown(wait=True, cancel_futures=True)
        with self.lock:
            for job in self._jobs():
                if job['status'] in ACTIVE and job['archive_id'] in self.owned:
                    job.update(status='interrupted', inventory_complete=False, finished_at=now(),
                               stage='Inventory service stopped; captured works are retained')
                    self._export(job)
                    self._save(job)

"""Durable inventory contracts with controlled public data and no network."""
import copy
import csv
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import uuid

from creator_notes.archive import ArchiveManager
from creator_notes.collector import CollectorError
from creator_notes.engine import Store
from creator_notes.paths import AppPaths


PROFILE = {'sec_uid': 'fixture-author', 'nickname': 'Fixture creator', 'unique_id': 'fixture',
           'profile_url': 'https://www.douyin.com/user/fixture-author'}
SAMPLED = '2026-10-23T12:00:00+00:00'


def work(number=1, **changes):
    value = {'video_id': str(1234567890123456700 + number), 'title': f'Public work {number}',
             'url': f'https://www.douyin.com/video/{1234567890123456700 + number}',
             'author_sec_uid': PROFILE['sec_uid'], 'kind': 'video', 'create_time': number,
             'published_at': SAMPLED, 'sampled_at': SAMPLED, 'duration_seconds': 6.2,
             'statistics': {'like': 1, 'comment': 0, 'collect': None, 'share': 3}}
    value.update(changes)
    return value


def snapshot(rows=None, **coverage):
    return {'profile': copy.deepcopy(PROFILE), 'works': copy.deepcopy(rows if rows is not None else [work()]),
            'coverage': {'scope': 'public_creator_works', 'end_observed': True,
                         'all_history_scanned': True, 'identity_verified': True,
                         'count_mismatch': False, 'stop_reason': 'end_observed', **coverage},
            'collected_at': SAMPLED}


class Collector:
    def __init__(self, result=None, progress=None, error=None, gate=None, resolution=None, **kwargs):
        self.result = result or snapshot()
        self.progress = progress or []
        self.error = error
        self.gate = gate
        self.resolution = resolution or {'status': 'resolved', 'profile': copy.deepcopy(PROFILE)}
        self.resolved = []
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def resolve_creator(self, creator):
        self.resolved.append(creator)
        return self.resolution

    async def collect_archive(self, profile, *, max_scrolls, max_seconds, on_progress):
        self.calls += 1
        for value in self.progress:
            on_progress(value)
        if self.gate:
            self.gate.wait(2)
        if self.error:
            raise self.error
        return self.result


class Engine:
    def __init__(self, paths, collector):
        self.paths = paths.prepare()
        self.store = Store(self.paths.home)
        self.collector_factory = lambda **kwargs: collector
        self.submitted = []
        self.jobs = {}

    def submit_archive_work(self, archive_id, profile, row, collected_at, asr_profile):
        self.submitted.append((archive_id, profile, row, collected_at, asr_profile))
        result = {'check_id': uuid.uuid4().hex, 'status': 'queued', 'delivery_complete': False}
        self.jobs[result['check_id']] = result
        return copy.deepcopy(result)

    def report(self, check_id):
        if check_id not in self.jobs:
            raise ValueError('Check not found')
        return copy.deepcopy(self.jobs[check_id])


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='creator-notes-archive-')
        self.collector = Collector()
        self.engine = Engine(AppPaths(Path(self.directory.name)), self.collector)
        self.manager = ArchiveManager(self.engine)
        self.extra_managers = []

    def tearDown(self):
        if self.collector.gate:
            self.collector.gate.set()
        self.manager.close()
        for manager in self.extra_managers:
            manager.close()
        self.directory.cleanup()

    def wait(self, job):
        for _ in range(400):
            value = self.manager.report(job['archive_id'])
            if value['status'] not in ('queued', 'running'):
                return value
            time.sleep(.005)
        raise AssertionError('Controlled inventory did not finish')

    def test_verified_complete_inventory_pagination_and_exports(self):
        self.collector.result = snapshot([work(1), work(3), work(2)], advertised_public_works=3)
        job = self.wait(self.manager.start('fixture'))
        self.assertEqual(job['status'], 'inventory_done')
        self.assertTrue(job['inventory_complete'])
        self.assertEqual(job['total_works'], 3)
        first = self.manager.report(job['archive_id'], limit=2)
        second = self.manager.report(job['archive_id'], offset=first['next_offset'], limit=2)
        self.assertEqual([row['create_time'] for row in first['works'] + second['works']], [3, 2, 1])
        self.assertIsNone(second['next_offset'])
        self.assertIsNone(first['works'][0]['statistics']['collect'])
        self.assertEqual(self.engine.store.identity('fixture')['sec_uid'], PROFILE['sec_uid'])
        for path in job['export_paths'].values():
            self.assertTrue(Path(path).is_file())
        with Path(job['export_paths']['inventory.csv']).open(encoding='utf-8', newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]['collect'], '')

    def test_bound_identity_skips_name_resolution(self):
        self.engine.store.bind('fixture', PROFILE)
        self.wait(self.manager.start('fixture'))
        self.assertEqual(self.collector.resolved, [])

    def test_explicit_end_and_matching_advertised_count_are_required(self):
        cases = [dict(end_observed=False), dict(all_history_scanned=False),
                 dict(advertised_public_works=2, count_mismatch=True),
                 dict(advertised_public_works=2, count_mismatch=False),
                 dict(advertised_public_works=1, count_mismatch=None)]
        for changes in cases:
            with self.subTest(changes=changes):
                self.collector.result = snapshot(**changes)
                job = self.wait(self.manager.start('fixture'))
                self.assertEqual(job['status'], 'inventory_partial')
                self.assertFalse(job['inventory_complete'])

    def test_captcha_keeps_last_partial_snapshot_without_retry(self):
        self.collector.progress = [snapshot([work(1)], end_observed=False, all_history_scanned=False,
                                            stop_reason='captcha')]
        self.collector.error = CollectorError('captcha', 'secret challenge request details')
        job = self.wait(self.manager.start('fixture'))
        self.assertEqual(job['status'], 'captcha')
        self.assertEqual(job['total_works'], 1)
        self.assertFalse(job['inventory_complete'])
        self.assertEqual(self.collector.calls, 1)
        self.assertNotIn('secret challenge', json.dumps(job))
        saved = json.loads((self.manager.directory / f"{job['archive_id']}.json").read_text(encoding='utf-8'))
        self.assertEqual(saved['works'][0]['sampled_at'], SAMPLED)

    def test_login_required_keeps_progress_without_retry(self):
        self.collector.progress = [snapshot(end_observed=False, all_history_scanned=False)]
        self.collector.error = CollectorError('login_required', 'login needed')
        job = self.wait(self.manager.start('fixture'))
        self.assertEqual(job['status'], 'login_required')
        self.assertEqual(job['total_works'], 1)
        self.assertEqual(self.collector.calls, 1)

    def test_duplicate_active_queries_and_bound_aliases_reuse_inventory(self):
        self.collector.gate = threading.Event()
        self.engine.store.bind('fixture', PROFILE)
        self.engine.store.bind('@fixture', PROFILE)
        first = self.manager.start('fixture')
        self.assertEqual(self.manager.start(' FIXTURE ')['archive_id'], first['archive_id'])
        second_manager = ArchiveManager(self.engine)
        self.extra_managers.append(second_manager)
        self.assertEqual(second_manager.start('@fixture')['archive_id'], first['archive_id'])
        second_manager.close()
        self.assertIn(self.manager.report(first['archive_id'])['status'], ('queued', 'running'))
        self.collector.gate.set()
        self.wait(first)
        self.assertEqual(self.collector.calls, 1)

    def test_stale_jobs_become_interrupted_and_keep_progress(self):
        job = self.wait(self.manager.start('fixture'))
        path = self.manager.directory / f"{job['archive_id']}.json"
        saved = json.loads(path.read_text(encoding='utf-8'))
        saved.update(status='running', owner_pid=-1)
        path.write_text(json.dumps(saved), encoding='utf-8')
        new = ArchiveManager(self.engine)
        self.extra_managers.append(new)
        restored = new.report(job['archive_id'])
        self.assertEqual(restored['status'], 'interrupted')
        self.assertEqual(restored['total_works'], 1)
        self.assertFalse(restored['inventory_complete'])

    def test_selected_work_submission_is_durable_and_idempotent(self):
        job = self.wait(self.manager.start('fixture'))
        check = self.manager.submit_work(job['archive_id'], work()['video_id'])
        self.assertEqual(len(self.engine.submitted), 1)
        arguments = self.engine.submitted[0]
        self.assertEqual(arguments[0], job['archive_id'])
        self.assertEqual(arguments[1]['sec_uid'], PROFILE['sec_uid'])
        self.assertEqual(arguments[2]['video_id'], work()['video_id'])
        self.assertEqual(arguments[3], SAMPLED)
        self.assertEqual(arguments[4], 'balanced')
        self.engine.jobs[check['check_id']].update(status='error')
        new = ArchiveManager(self.engine)
        self.extra_managers.append(new)
        repeated = new.submit_work(job['archive_id'], work()['video_id'])
        self.assertEqual(repeated['check_id'], check['check_id'])
        self.assertEqual(len(self.engine.submitted), 1)
        linked = new.report(job['archive_id'])['works'][0]
        self.assertEqual(linked['check_id'], check['check_id'])
        self.assertEqual(linked['check_status'], 'error')
        self.assertFalse(linked['delivery_complete'])

    def test_work_not_in_inventory_and_active_submission_are_rejected(self):
        self.collector.gate = threading.Event()
        job = self.manager.start('fixture')
        with self.assertRaises(ValueError):
            self.manager.submit_work(job['archive_id'], work()['video_id'])
        self.collector.gate.set()
        job = self.wait(job)
        with self.assertRaises(ValueError):
            self.manager.submit_work(job['archive_id'], work(99)['video_id'])
        self.assertEqual(self.engine.submitted, [])

    def test_submission_preserves_the_individual_work_metric_sample_time(self):
        item_sampled = '2026-10-23T11:15:00+00:00'
        self.collector.result = snapshot([work(sampled_at=item_sampled)])
        job = self.wait(self.manager.start('fixture'))
        self.manager.submit_work(job['archive_id'], work()['video_id'])
        self.assertEqual(self.engine.submitted[0][3], item_sampled)
        self.assertNotEqual(self.engine.submitted[0][3], job['collected_at'])

    def test_ambiguous_resolution_never_collects_works(self):
        self.collector.resolution = {'status': 'ambiguous', 'candidates': [PROFILE]}
        job = self.wait(self.manager.start('duplicate name'))
        self.assertEqual(job['status'], 'needs_selection')
        self.assertEqual(job['candidates'][0]['sec_uid'], PROFILE['sec_uid'])
        self.assertEqual(self.collector.calls, 0)

    def test_cross_author_progress_is_rejected(self):
        self.collector.progress = [snapshot([work(author_sec_uid='different-author')])]
        job = self.wait(self.manager.start('fixture'))
        self.assertEqual(job['status'], 'error')
        self.assertEqual(job['total_works'], 0)

    def test_unknown_fields_and_exception_text_are_not_persisted(self):
        self.collector.progress = [snapshot([work(cookie='private-token')], raw='private-token')]
        self.collector.error = RuntimeError('private-token https://example.invalid/secret')
        job = self.wait(self.manager.start('fixture'))
        self.assertEqual(job['status'], 'error')
        encoded = (self.manager.directory / f"{job['archive_id']}.json").read_text(encoding='utf-8')
        self.assertNotIn('private-token', encoded)
        self.assertNotIn('example.invalid', encoded)

    def test_public_exports_escape_formula_and_markup(self):
        self.collector.result = snapshot([work(title='=1+1 [unsafe] <script> | line\nnext')])
        job = self.wait(self.manager.start('fixture'))
        with Path(job['export_paths']['inventory.csv']).open(encoding='utf-8', newline='') as stream:
            row = next(csv.DictReader(stream))
        self.assertTrue(row['title'].startswith("'=1+1"))
        markdown = Path(job['export_paths']['inventory.md']).read_text(encoding='utf-8')
        self.assertNotIn('<script>', markdown)
        self.assertIn('&#124;', markdown)
        self.assertIn('&#91;unsafe&#93;', markdown)

    def test_coverage_pagination_allowlist_retains_only_public_cursor_numbers(self):
        self.collector.result = snapshot(pagination_progress=[{'sequence': 1, 'request_cursor': '0',
            'response_cursor': '100', 'has_more': 0, 'items': 1, 'cookie': 'secret'}],
            pagination_complete=True, chain_status='connected_end')
        job = self.wait(self.manager.start('fixture'))
        progress = job['coverage']['pagination_progress'][0]
        self.assertEqual(progress['response_cursor'], '100')
        self.assertNotIn('cookie', progress)
        self.assertTrue(job['coverage']['pagination_complete'])
        self.assertEqual(job['coverage']['chain_status'], 'connected_end')

    def test_argument_bounds_and_traversal_are_rejected(self):
        for arguments in [('',), (None,), ('x', True), ('x', 2001), ('x', 1, 0), ('x', 1, 7201)]:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                self.manager.start(*arguments)
        job = self.wait(self.manager.start('fixture'))
        for offset, limit in [(-1, 50), (True, 50), (0, 0), (0, 301), (0, False)]:
            with self.subTest(offset=offset, limit=limit), self.assertRaises(ValueError):
                self.manager.report(job['archive_id'], offset, limit)
        with self.assertRaises(ValueError):
            self.manager.report('../../outside')
        with self.assertRaises(ValueError):
            self.manager.submit_work(job['archive_id'], work()['video_id'], [])
        with self.assertRaises(ValueError):
            self.manager.submit_work(job['archive_id'], '../../outside')


if __name__ == '__main__':
    unittest.main()

"""Integration contracts with controlled collectors and recognizers; no network."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest

from creator_notes.engine import Manager, Store
from creator_notes.paths import AppPaths

PROFILE = {'sec_uid': 'author-one', 'nickname': '\u6d4b\u8bd5\u535a\u4e3b', 'unique_id': 'test001',
           'profile_url': 'https://www.douyin.com/user/author-one'}
LATEST = {'video_id': '1234567890123456789', 'url': 'https://www.douyin.com/video/1234567890123456789',
          'title': '\u6d4b\u8bd5\u6807\u9898', 'kind': 'video', 'create_time': 100,
          'published_at': '2026-10-21T10:00:00+00:00',
          'statistics': {'like': 5, 'comment': 4, 'collect': 3, 'share': 2}}


class FakeCollector:
    latest = LATEST
    def __init__(self, **kwargs):
        pass
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass
    async def resolve_creator(self, query):
        return {'status': 'resolved', 'profile': copy.deepcopy(PROFILE)}
    async def collect_latest(self, profile):
        return {'latest': copy.deepcopy(self.latest), 'profile': profile, 'collected_at': '2026-10-21T11:00:00+00:00',
                'coverage': {'scope': 'recent_visible_posts'}}


def wait(manager, job):
    for _ in range(200):
        result = manager.report(job['check_id'])
        if result['status'] not in ('queued', 'running'):
            return result
        time.sleep(.01)
    raise AssertionError('Fixture job did not finish')


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='\u6587\u7a3f \u7a7a\u683c ')
        self.paths = AppPaths(Path(self.temp.name))
        self.calls = 0
        self.manager = Manager(self.paths, collector_factory=FakeCollector, acquire=self.acquire, recognize=self.recognize)

    def tearDown(self):
        self.manager.close()
        self.temp.cleanup()

    def acquire(self, url, directory, **kwargs):
        return Path('controlled-test-audio.wav')

    def recognize(self, source, model_dir, **kwargs):
        self.calls += 1
        return {'segments': [{'start': float(i), 'end': i+.8, 'text': '\u643a\u5e26\u5417' if i == 1 else f'\u539f\u53e5{i}'} for i in range(229)],
                'transcript': '\u5b8c\u6574\u673a\u5668\u7a3f', 'duration': 230, 'model': kwargs['model']}

    def review_request(self, job):
        return {'source_sha256': job['source_sha256'], 'edits': [{'index': 1, 'before': '\u643a\u5e26\u5417', 'after': '\u5199\u4ee3\u7801',
                'reason': '\u7528\u6237\u786e\u8ba4', 'evidence': '\u6d4b\u8bd5\u7528\u6237\u63d0\u4f9b\u66f4\u6b63', 'evidence_level': 'user_confirmed', 'confidence': 'high'}],
                'pending_review': [{'index': 2, 'issue': '\u6b64\u5904\u4ec5\u4fdd\u7559\u539f\u6587'}],
                'overview': {'summary': '\u89c6\u9891\u4ecb\u7ecd\u64cd\u4f5c\u6d41\u7a0b\u3002', 'sections': [{'start': 0, 'end': 229, 'title': '\u64cd\u4f5c\u6d41\u7a0b', 'summary': '\u4f5c\u8005\u8bb2\u89e3\u6d41\u7a0b\u3002'}],
                             'limitations': ['\u672a\u9010\u5b57\u56de\u542c\uff0c\u4fdd\u7559\u5f85\u6838\u8bcd\u3002']}}

    def test_complete_workflow_review_and_repeat(self):
        job = wait(self.manager, self.manager.submit('\u6d4b\u8bd5\u535a\u4e3b'))
        self.assertEqual(job['status'], 'done')
        self.assertTrue(job['complete'])
        self.assertFalse(job['delivery_complete'])
        self.assertEqual(self.manager.store.history('author-one'), {})
        collected = []
        offset = 0
        while True:
            page = self.manager.transcript(job['check_id'], offset=offset, limit=80)
            collected.extend(page['segments'])
            if page['next_offset'] is None:
                break
            offset = page['next_offset']
        self.assertEqual(len(collected), 229)
        self.assertEqual([s['index'] for s in collected], list(range(229)))
        originals = {name: hashlib.sha256(Path(path).read_bytes()).hexdigest() for name, path in job['files'].items()}
        review = self.manager.review(job['check_id'], self.review_request(job))
        self.assertTrue(review['originals_preserved'])
        for name, path in job['files'].items():
            self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(), originals[name])
        repeat = wait(self.manager, self.manager.submit('\u6d4b\u8bd5\u535a\u4e3b'))
        self.assertEqual(repeat['update_state'], 'no_update')
        self.assertEqual(self.calls, 1)
        self.assertTrue(repeat['transcription']['reused'])

    def test_partial_metrics_does_not_advance_delivery(self):
        class Partial(FakeCollector):
            latest = copy.deepcopy(LATEST)
            latest['statistics']['share'] = None
        self.manager.collector_factory = Partial
        job = wait(self.manager, self.manager.submit('\u6d4b\u8bd5\u535a\u4e3b'))
        self.assertEqual(job['status'], 'partial')
        self.manager.review(job['check_id'], self.review_request(job))
        self.assertFalse(self.manager.report(job['check_id'])['delivery_complete'])
        self.assertEqual(self.manager.store.history('author-one'), {})

    def test_errors_do_not_persist_exception_payload(self):
        def fail(*args, **kwargs):
            raise RuntimeError('secret-cookie https://example.invalid?private-token=123')
        self.manager.acquire = fail
        job = wait(self.manager, self.manager.submit('\u6d4b\u8bd5\u535a\u4e3b'))
        self.assertEqual(job['status'], 'error')
        self.assertNotIn('private-token', json.dumps(job))
        self.assertEqual(self.manager.store.history('author-one'), {})

    def test_missing_dependency_finishes_with_safe_error(self):
        def missing(*args, **kwargs):
            raise ModuleNotFoundError('Optional dependency absent')
        self.manager.collector_factory = missing
        job = wait(self.manager, self.manager.submit('\u6d4b\u8bd5\u535a\u4e3b'))
        self.assertEqual(job['status'], 'error')
        self.assertEqual(job['error_code'], 'dependency_missing')

    def test_invalid_review_cannot_mark_delivered(self):
        job = wait(self.manager, self.manager.submit('\u6d4b\u8bd5\u535a\u4e3b'))
        request = self.review_request(job)
        request['source_sha256'] = 'incorrect'
        with self.assertRaises(ValueError):
            self.manager.review(job['check_id'], request)
        self.assertFalse(self.manager.report(job['check_id'])['delivery_complete'])

    def test_identity_conflict_requires_explicit_refresh(self):
        self.manager.store.bind('\u535a\u4e3b', PROFILE)
        with self.assertRaises(ValueError):
            self.manager.store.bind('\u535a\u4e3b', dict(PROFILE, sec_uid='other'))

    def test_image_latest_does_not_fall_back_to_old_video(self):
        class Image(FakeCollector):
            latest = dict(LATEST, kind='image')
        self.manager.collector_factory = Image
        job = wait(self.manager, self.manager.submit('\u6d4b\u8bd5\u535a\u4e3b'))
        self.assertEqual(job['status'], 'unsupported_latest')
        self.assertEqual(self.calls, 0)

    def test_invalid_requests_and_pagination(self):
        with self.assertRaises(ValueError):
            self.manager.submit('')
        with self.assertRaises(ValueError):
            self.manager.submit('\u535a\u4e3b', 'unsupported')
        job = wait(self.manager, self.manager.submit('\u6d4b\u8bd5\u535a\u4e3b'))
        with self.assertRaises(ValueError):
            self.manager.transcript(job['check_id'], -1)
        with self.assertRaises(ValueError):
            self.manager.transcript('../../anything')


if __name__ == '__main__':
    unittest.main()

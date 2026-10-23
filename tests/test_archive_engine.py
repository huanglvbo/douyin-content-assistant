"""Exact archived-work ASR contracts with controlled media; no live services."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from creator_notes.engine import Manager, originals_match
from creator_notes.paths import AppPaths
from test_engine import wait


ARCHIVE = 'a' * 32
OTHER_ARCHIVE = 'b' * 32
SAMPLED_AT = '2026-10-23T11:14:58+00:00'
IDENTITY = {
    'sec_uid': 'MS4wLjABverified-creator', 'nickname': 'Controlled creator',
    'unique_id': 'controlled_creator',
    'profile_url': 'https://www.douyin.com/user/MS4wLjABverified-creator',
}
OLD_WORK = {
    'video_id': '1234567890123456789',
    'url': 'https://www.douyin.com/video/1234567890123456789',
    'title': 'An older archived work', 'kind': 'video', 'create_time': 100,
    'published_at': '2020-01-01T08:00:00+00:00', 'duration_seconds': 1.5,
    'author_sec_uid': IDENTITY['sec_uid'],
    'statistics': {'like': 91, 'comment': 17, 'collect': 8, 'share': 6},
}


class ForbiddenDiscovery:
    """Any collection or identity discovery is a regression for archived work."""

    calls = 0

    def __init__(self, **kwargs):
        type(self).calls += 1
        raise AssertionError('Archive transcription must not open a collector')


class ArchiveEngineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='creator-archive-')
        self.paths = AppPaths(Path(self.temporary.name))
        self.acquisitions = []
        self.recognitions = []
        ForbiddenDiscovery.calls = 0
        self.manager = Manager(self.paths, collector_factory=ForbiddenDiscovery,
                               acquire=self.acquire, recognize=self.recognize)

    def tearDown(self):
        self.manager.close()
        self.temporary.cleanup()

    def acquire(self, url, directory, **kwargs):
        self.acquisitions.append(url)
        directory.mkdir(parents=True, exist_ok=True)
        media = directory / (url.rsplit('/', 1)[-1] + '.wav')
        media.write_bytes(('Controlled audio for ' + url).encode('ascii'))
        return media

    def recognize(self, source, model_dir, **kwargs):
        source = Path(source)
        self.recognitions.append(source.stem)
        text = 'Controlled speech for ' + source.stem
        return {'segments': [{'start': 0.1, 'end': 1.2, 'text': text}],
                'transcript': text, 'duration': 1.5, 'model': kwargs['model']}

    def submit(self, work=None, archive_id=ARCHIVE, sampled_at=SAMPLED_AT):
        return self.manager.submit_archive_work(archive_id, copy.deepcopy(IDENTITY),
                                                copy.deepcopy(work or OLD_WORK), sampled_at)

    def test_exact_old_work_is_used_without_latest_discovery(self):
        job = wait(self.manager, self.submit())
        self.assertEqual(job['status'], 'done')
        self.assertEqual(job['update_state'], 'archive_item')
        self.assertEqual(job['archive_video_id'], OLD_WORK['video_id'])
        self.assertEqual(job['latest']['url'], OLD_WORK['url'])
        self.assertEqual(job['latest']['statistics'], OLD_WORK['statistics'])
        self.assertEqual(job['latest']['published_at'], OLD_WORK['published_at'])
        self.assertEqual(job['collected_at'], SAMPLED_AT)
        self.assertEqual(self.acquisitions, [OLD_WORK['url']])
        self.assertEqual(self.recognitions, [OLD_WORK['video_id']])
        self.assertEqual(ForbiddenDiscovery.calls, 0)
        self.assertTrue(job['complete'])
        self.assertFalse(job['delivery_complete'])
        self.assertEqual(job['review_status'], 'pending')
        self.assertEqual(self.manager.store.history(IDENTITY['sec_uid']), {})
        exported = json.loads(Path(job['files']['report.json']).read_text(encoding='utf-8'))
        self.assertEqual(exported['latest']['statistics'], OLD_WORK['statistics'])
        self.assertEqual(exported['collected_at'], SAMPLED_AT)
        page = self.manager.transcript(job['check_id'])
        self.assertEqual(page['segments'][0]['text'], 'Controlled speech for ' + OLD_WORK['video_id'])
        self.assertIsNone(page['next_offset'])

    def test_same_archive_and_video_deduplicate_before_and_after_completion(self):
        with patch.object(self.manager.pool, 'submit') as schedule:
            first = self.submit()
            second = self.submit()
            self.assertEqual(first['check_id'], second['check_id'])
            schedule.assert_called_once()
        self.manager.run(first['check_id'])
        finished = self.manager.report(first['check_id'])
        self.assertEqual(finished['status'], 'done')
        repeat = self.submit()
        self.assertEqual(repeat['check_id'], first['check_id'])
        self.assertEqual(len(self.manager.store.jobs()), 1)
        self.assertEqual(self.acquisitions, [OLD_WORK['url']])

    def test_two_inventory_items_keep_distinct_jobs_media_and_transcripts(self):
        other = copy.deepcopy(OLD_WORK)
        other.update(video_id='2345678901234567890', title='A different archived work',
                     url='https://www.douyin.com/video/2345678901234567890', create_time=200)
        first, second = self.submit(), self.submit(other)
        first, second = wait(self.manager, first), wait(self.manager, second)
        self.assertNotEqual(first['check_id'], second['check_id'])
        self.assertEqual((first['status'], second['status']), ('done', 'done'))
        self.assertEqual(self.acquisitions, [OLD_WORK['url'], other['url']])
        self.assertNotEqual(first['files']['segments.json'], second['files']['segments.json'])
        for job, expected in ((first, OLD_WORK), (second, other)):
            self.assertEqual(job['latest']['video_id'], expected['video_id'])
            self.assertIn(expected['video_id'], self.manager.transcript(job['check_id'])['segments'][0]['text'])
            self.assertTrue(originals_match(job))
        self.assertEqual(ForbiddenDiscovery.calls, 0)

    def test_another_archive_reuses_valid_raw_cache_but_keeps_its_own_sampling(self):
        first = wait(self.manager, self.submit())
        originals = dict(first['original_hashes'])
        refreshed = copy.deepcopy(OLD_WORK)
        refreshed['statistics']['like'] = 999
        sampled_later = '2026-10-24T12:00:00+00:00'
        second = wait(self.manager, self.submit(refreshed, OTHER_ARCHIVE, sampled_later))
        self.assertNotEqual(first['check_id'], second['check_id'])
        self.assertTrue(second['transcription']['reused'])
        self.assertEqual(self.acquisitions, [OLD_WORK['url']])
        self.assertEqual(self.recognitions, [OLD_WORK['video_id']])
        self.assertEqual(second['latest']['statistics']['like'], 999)
        self.assertEqual(second['collected_at'], sampled_later)
        self.assertEqual(first['original_hashes'], originals)
        self.assertTrue(originals_match(first))
        self.assertTrue(originals_match(second))
        self.assertFalse(second['delivery_complete'])

    def test_invalid_work_url_or_author_is_rejected_before_a_job_exists(self):
        mutations = [
            {'url': 'https://example.invalid/video/' + OLD_WORK['video_id']},
            {'url': OLD_WORK['url'] + '?redirect=other'},
            {'url': 'https://www.douyin.com/video/2345678901234567890'},
            {'video_id': '../other'},
            {'author_sec_uid': 'MS4wLjABdifferent-author'},
            {'author_sec_uid': None},
            {'kind': 'unknown'},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.submit(dict(OLD_WORK, **mutation))
        invalid_identity = dict(IDENTITY, sec_uid='unverified-author')
        with self.assertRaises(ValueError):
            self.manager.submit_archive_work(ARCHIVE, invalid_identity, OLD_WORK, SAMPLED_AT)
        self.assertEqual(self.manager.store.jobs(), [])
        self.assertEqual(self.acquisitions, [])

    def test_invalid_metric_shapes_and_values_are_rejected(self):
        invalid_statistics = [None, [], {'like': 1},
            dict(OLD_WORK['statistics'], unexpected=1),
            dict(OLD_WORK['statistics'], like=True),
            dict(OLD_WORK['statistics'], comment=-1),
            dict(OLD_WORK['statistics'], collect=1.5),
            dict(OLD_WORK['statistics'], share='2')]
        for statistics in invalid_statistics:
            with self.subTest(statistics=statistics), self.assertRaises(ValueError):
                self.submit(dict(OLD_WORK, statistics=statistics))
        self.assertEqual(self.manager.store.jobs(), [])
        self.assertEqual(ForbiddenDiscovery.calls, 0)

    def test_missing_metric_remains_partial_and_cannot_be_raw_delivery(self):
        work = copy.deepcopy(OLD_WORK)
        work['statistics']['share'] = None
        job = wait(self.manager, self.submit(work))
        self.assertEqual(job['status'], 'partial')
        self.assertEqual(job['missing_metrics'], ['share'])
        self.assertFalse(job['complete'])
        self.assertFalse(job['delivery_complete'])
        self.assertEqual(job['review_status'], 'pending')
        self.assertIsNone(job['latest']['statistics']['share'])
        self.assertEqual(self.acquisitions, [OLD_WORK['url']])

    def test_invalid_archive_id_sampling_timestamp_and_profile_are_rejected(self):
        for sampled_at in ('2026-10-23T11:14:58', '2026-10-23', '', 'not-a-time', None):
            with self.subTest(sampled_at=sampled_at), self.assertRaises(ValueError):
                self.submit(sampled_at=sampled_at)
        for archive_id in ('../outside', 'a' * 31, 'A' * 32, None):
            with self.subTest(archive_id=archive_id), self.assertRaises(ValueError):
                self.submit(archive_id=archive_id)
        with self.assertRaises(ValueError):
            self.manager.submit_archive_work(ARCHIVE, IDENTITY, OLD_WORK, SAMPLED_AT, profile='unknown')
        self.assertEqual(self.manager.store.jobs(), [])
        self.assertEqual(self.acquisitions, [])

    def test_image_post_is_explicitly_unsupported_without_using_another_video(self):
        image = dict(OLD_WORK, kind='image', title='An archived image post')
        job = wait(self.manager, self.submit(image))
        self.assertEqual(job['status'], 'unsupported_latest')
        self.assertIn('archived work is an image post', job['stage'])
        self.assertEqual(job['latest']['video_id'], OLD_WORK['video_id'])
        self.assertFalse(job['complete'])
        self.assertFalse(job['delivery_complete'])
        self.assertEqual(self.acquisitions, [])
        self.assertEqual(self.recognitions, [])
        self.assertEqual(ForbiddenDiscovery.calls, 0)
        self.assertNotIn('files', job)

    def test_persisted_inventory_snapshot_cannot_inject_job_or_file_state(self):
        identity, work = copy.deepcopy(IDENTITY), copy.deepcopy(OLD_WORK)
        identity['storage_state'] = 'do-not-copy-private-state'
        work.update(files={'transcript.txt': '../outside'}, check_id='injected',
                    status='done', media_checkpoint={'path': '../outside'}, delivery_complete=True)
        with patch.object(self.manager.pool, 'submit'):
            queued = self.manager.submit_archive_work(ARCHIVE, identity, work, SAMPLED_AT)
        identity['sec_uid'] = 'MS4wLjABchanged-after-submit'
        work['statistics']['like'] = 888
        work['url'] = 'https://example.invalid/changed-after-submit'
        self.manager.run(queued['check_id'])
        job = self.manager.report(queued['check_id'])
        self.assertEqual(job['status'], 'done')
        self.assertEqual(job['profile']['sec_uid'], IDENTITY['sec_uid'])
        self.assertEqual(job['latest']['statistics'], OLD_WORK['statistics'])
        self.assertNotIn('storage_state', job['profile'])
        for field in ('files', 'check_id', 'status', 'media_checkpoint', 'delivery_complete'):
            self.assertNotIn(field, job['archive_source']['latest'])
        self.assertEqual(self.acquisitions, [OLD_WORK['url']])
        self.assertTrue(originals_match(job))
        self.assertFalse(job['delivery_complete'])


if __name__ == '__main__':
    unittest.main()

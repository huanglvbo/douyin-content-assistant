"""Recovery regressions: preserve work, wait for humans, never retry a CAPTCHA loop."""
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from creator_notes.engine import Manager
from creator_notes.login import _write_status
from creator_notes.paths import AppPaths
from creator_notes.recognition import RecognitionError
from test_engine import FakeCollector, wait


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.paths = AppPaths(Path(self.temporary.name))
        self.collected = 0
        self.downloaded = 0
        self.recognized = 0
        owner = self

        class CountingCollector(FakeCollector):
            async def collect_latest(self, profile):
                owner.collected += 1
                return await super().collect_latest(profile)

        self.factory = CountingCollector
        self.manager = Manager(self.paths, collector_factory=self.factory,
                               acquire=self.acquire, recognize=self.recognize)

    def tearDown(self):
        self.manager.close()
        self.temporary.cleanup()

    def acquire(self, url, directory, **kwargs):
        self.downloaded += 1
        directory.mkdir(parents=True, exist_ok=True)
        media = directory / 'complete-audio.mp4'
        media.write_bytes(b'complete-controlled-audio')
        return media

    def recognize(self, source, model_dir, **kwargs):
        self.recognized += 1
        if self.recognized == 1:
            raise RecognitionError('model_unavailable', 'Controlled model failure')
        return {'segments': [{'start': 0., 'end': 1., 'text': 'Controlled full speech'}],
                'transcript': 'Controlled full speech', 'duration': 1.5, 'model': kwargs['model']}

    def failed(self):
        job = wait(self.manager, self.manager.submit('test001', profile='fast'))
        self.assertEqual(job['status'], 'error')
        self.assertTrue(job['resume_available'])
        self.assertTrue(job.get('media_checkpoint'))
        return job

    def test_model_retry_keeps_job_audio_metrics_and_sampling_time(self):
        failed = self.failed()
        resumed = wait(self.manager, self.manager.resume(failed['check_id'], profile='balanced'))
        self.assertEqual(resumed['check_id'], failed['check_id'])
        self.assertEqual(resumed['status'], 'done')
        self.assertEqual((self.collected, self.downloaded, self.recognized), (1, 1, 2))
        self.assertEqual(resumed['latest'], failed['latest'])
        self.assertEqual(resumed['collected_at'], failed['collected_at'])
        self.assertEqual(resumed['transcription']['model'], 'small')
        self.assertTrue(resumed['transcription']['media_reused'])
        self.assertEqual(resumed['attempt_history'][0]['error_code'], 'model_unavailable')
        self.assertNotIn('error_code', resumed)
        self.assertFalse(resumed['resume_available'])
        self.assertFalse(resumed['delivery_complete'])

    def test_reconnect_retains_checkpoint_without_recollecting(self):
        failed = self.failed()
        self.manager.close()
        self.manager = Manager(self.paths, collector_factory=self.factory,
                               acquire=self.acquire, recognize=self.recognize)
        resumed = wait(self.manager, self.manager.resume(failed['check_id']))
        self.assertEqual(resumed['status'], 'done')
        self.assertEqual((self.collected, self.downloaded), (1, 1))

    def test_changed_or_missing_media_is_rejected_before_network_or_asr(self):
        failed = self.failed()
        Path(failed['media_checkpoint']['path']).write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'Saved media changed'):
            self.manager.resume(failed['check_id'])
        self.assertEqual((self.collected, self.downloaded, self.recognized), (1, 1, 1))

    def test_changed_media_after_queue_claim_does_not_silently_recollect(self):
        failed = self.failed()
        with patch.object(self.manager.pool, 'submit'):
            self.manager.resume(failed['check_id'])
        Path(failed['media_checkpoint']['path']).write_bytes(b'changed-after-queue')
        self.manager.run(failed['check_id'])
        stopped = self.manager.report(failed['check_id'])
        self.assertEqual(stopped['error_code'], 'saved_media_changed')
        self.assertEqual((self.collected, self.downloaded, self.recognized), (1, 1, 1))

    def test_worker_shutdown_does_not_leave_a_claimed_job_queued(self):
        failed = self.failed()
        with patch.object(self.manager.pool, 'submit', side_effect=RuntimeError('Controlled shutdown')):
            with self.assertRaisesRegex(ValueError, 'worker stopped'):
                self.manager.resume(failed['check_id'])
        self.assertEqual(self.manager.report(failed['check_id'])['status'], 'interrupted')

    def test_database_claim_prevents_duplicate_resume_across_managers(self):
        failed = self.failed()
        other = Manager(self.paths, collector_factory=self.factory,
                        acquire=self.acquire, recognize=self.recognize)
        barrier, release = threading.Barrier(2), threading.Event()
        results, errors = [], []

        def hold(*args, **kwargs):
            if not release.wait(5):
                raise AssertionError('Controlled worker was not released')
            return self.recognize(*args, **kwargs)

        for manager in (self.manager, other):
            manager.recognize = hold
            claim = manager.store.claim_resume

            def synchronized_claim(job, previous, original=claim):
                barrier.wait(5)
                return original(job, previous)

            manager.store.claim_resume = synchronized_claim

        def resume(manager):
            try:
                results.append(manager.resume(failed['check_id']))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=resume, args=(manager,)) for manager in (self.manager, other)]
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(5)
            self.assertFalse(errors)
            self.assertEqual(len(results), 2)
            self.assertEqual({item['resume_count'] for item in results}, {1})
            release.set()
            self.assertEqual(wait(self.manager, results[0])['status'], 'done')
        finally:
            release.set()
            for thread in threads:
                thread.join(5)
            other.close()
        self.assertEqual(self.recognized, 2)

    def test_checkpoint_cannot_point_to_another_jobs_audio(self):
        failed = self.failed()
        forged = copy.deepcopy(failed)
        forged['latest']['video_id'] = '9876543210987654321'
        self.manager.store.save(forged)
        with self.assertRaisesRegex(ValueError, 'Saved media changed'):
            self.manager.resume(failed['check_id'])

    def test_duplicate_resume_queues_one_attempt(self):
        failed = self.failed()
        started, release = threading.Event(), threading.Event()
        original = self.manager.recognize

        def hold(*args, **kwargs):
            started.set()
            if not release.wait(5):
                raise AssertionError('Controlled worker was not released')
            return original(*args, **kwargs)

        self.manager.recognize = hold
        try:
            first = self.manager.resume(failed['check_id'])
            self.assertTrue(started.wait(5))
            second = self.manager.resume(failed['check_id'])
            self.assertEqual(first['check_id'], second['check_id'])
            self.assertEqual(second['resume_count'], 1)
        finally:
            release.set()
        self.assertEqual(wait(self.manager, first)['status'], 'done')
        self.assertEqual(self.recognized, 2)

    def test_captcha_needs_new_verification_and_stops_again_without_a_loop(self):
        class Challenge(RuntimeError):
            code = 'captcha'

        class Challenged(FakeCollector):
            async def collect_latest(self, profile):
                raise Challenge('Controlled human verification')

        self.manager.collector_factory = Challenged
        job = wait(self.manager, self.manager.submit('test001'))
        self.assertEqual(job['status'], 'captcha')
        with self.assertRaisesRegex(ValueError, 'new dedicated login'):
            self.manager.resume(job['check_id'])
        (self.paths.session / 'storage-state.json').write_text('{}')
        _write_status(self.paths.session, 'logged_in',
                      updated_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
        with self.assertRaisesRegex(ValueError, 'new dedicated login'):
            self.manager.resume(job['check_id'])
        _write_status(self.paths.session, 'logged_in', proof='verified_account_response', run_id='new-proof')
        second = wait(self.manager, self.manager.resume(job['check_id']))
        self.assertEqual(second['status'], 'captcha')
        self.assertEqual(second['resume_count'], 1)
        with self.assertRaisesRegex(ValueError, 'new dedicated login'):
            self.manager.resume(job['check_id'])
        self.assertEqual(self.recognized, 0)

    def test_complete_originals_cannot_be_restarted_or_overwritten(self):
        self.recognized = 1
        job = wait(self.manager, self.manager.submit('test001'))
        with self.assertRaisesRegex(ValueError, 'cannot resume'):
            self.manager.resume(job['check_id'])

    def test_model_preparation_fails_before_browser_or_download(self):
        self.manager.recognize = None
        with patch('creator_notes.recognition.ensure_model_cache',
                   side_effect=RecognitionError('model_unavailable', 'Controlled setup failure')):
            job = wait(self.manager, self.manager.submit('test001'))
        self.assertEqual(job['error_code'], 'model_unavailable')
        self.assertEqual((self.collected, self.downloaded), (0, 0))
        self.assertNotIn('profile', job)

    def test_preflight_selects_cached_small_for_fast_and_records_actual_model(self):
        self.manager.recognize = None
        self.recognized = 1
        selection = {'requested_model': 'base', 'selected_model': 'small',
                     'reason': 'cached_alternative', 'download_required': False}
        with patch('creator_notes.recognition.ensure_model_cache', return_value=selection) as prepare, \
             patch('creator_notes.recognition.transcribe_file', side_effect=self.recognize):
            job = wait(self.manager, self.manager.submit('test001', profile='fast'))
        self.assertEqual(job['status'], 'done')
        self.assertEqual(job['model_selection'], selection)
        self.assertEqual(job['transcription']['requested_model'], 'base')
        self.assertEqual(job['transcription']['model'], 'small')
        prepare.assert_called_once()


if __name__ == '__main__':
    unittest.main()

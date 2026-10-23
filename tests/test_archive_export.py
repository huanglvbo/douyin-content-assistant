"""Portable archive fixtures only; no browser, network, or real creator data."""
import base64
import copy
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from creator_notes.archive_export import ArchiveExporter
from creator_notes.engine import Manager, export_report
from creator_notes.paths import AppPaths


ARCHIVE = 'a' * 32
SPEECH = '9' * 19
NO_SPEECH = '8' * 19
IMAGE = '7' * 19
PROFILE = {'sec_uid': 'synthetic-author', 'nickname': 'Synthetic Creator', 'unique_id': 'fixture_creator'}
SAMPLED = '2025-01-02T03:04:05+00:00'
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jX1EAAAAASUVORK5CYII=')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


class ArchiveExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='creator-export-fixture-')
        home = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {
            'CREATOR_NOTES_OUTPUT': str(home / 'reports'), 'CREATOR_NOTES_MODELS': str(home / 'models')})
        self.environment.start()
        self.engine = Manager(AppPaths(home))
        self.exporter = ArchiveExporter(self.engine)
        self.rows, self.links = [], {}

    def tearDown(self):
        self.engine.close()
        self.environment.stop()
        self.temporary.cleanup()

    def work(self, video_id=SPEECH, kind='video', **values):
        item = {'video_id': video_id, 'kind': kind, 'author_sec_uid': PROFILE['sec_uid'],
                'url': 'https://www.douyin.com/video/' + video_id, 'title': 'Synthetic public work',
                'create_time': 1735787045, 'published_at': SAMPLED, 'sampled_at': SAMPLED,
                'duration_seconds': 6, 'statistics': {'like': 12, 'comment': 3, 'collect': 2, 'share': 1}}
        item.update(values)
        self.rows.append(item)
        return item

    def persist(self, complete=True):
        snapshot = {'archive_id': ARCHIVE, 'profile': PROFILE, 'works': self.rows,
                    'submissions': self.links, 'status': 'inventory_done' if complete else 'inventory_partial',
                    'inventory_complete': complete, 'collected_at': '2025-01-02T09:00:00+00:00',
                    'coverage': {'all_history_scanned': complete, 'end_observed': complete,
                                 'count_mismatch': False, 'advertised_public_works': len(self.rows)}}
        dump(self.engine.paths.home / 'archives' / (ARCHIVE + '.json'), snapshot)

    def report(self, work, check_id='b' * 32, *, reviewed=True, pending=False, no_speech=False, media=True):
        report = {'check_id': check_id, 'archive_id': ARCHIVE, 'archive_video_id': work['video_id'],
                  'profile': PROFILE, 'latest': copy.deepcopy(work), 'query': 'fixture_creator',
                  'asr_profile': 'balanced', 'status': 'error' if no_speech else 'done',
                  'stage': 'Synthetic fixture only', 'complete': not no_speech, 'delivery_complete': False,
                  'update_state': 'new', 'collected_at': SAMPLED,
                  'transcription': {'duration_seconds': 6, 'characters': 45, 'reused': False}}
        if media:
            path = self.engine.paths.work / check_id / 'fixture.mp4'
            path.parent.mkdir(parents=True)
            path.write_bytes(b'\x00\x00\x00\x18ftypisom-synthetic-test-only')
            report['media_checkpoint'] = {'path': str(path), 'sha256': digest(path), 'bytes': path.stat().st_size,
                                          'video_id': work['video_id'], 'author': PROFILE['sec_uid']}
        if no_speech:
            report['error_code'] = 'no_speech'
        else:
            transcript = {'segments': [{'start': 0.2, 'end': 1.8, 'text': 'First synthetic sentence.'},
                                       {'start': 3.0, 'end': 4.8, 'text': 'Second synthetic sentence.'}],
                          'transcript': 'First synthetic sentence. Second synthetic sentence.', 'duration': 6}
            export_report(report, transcript, self.engine.paths.reports)
        self.engine.store.save(report)
        self.links[work['video_id']] = {'check_id': check_id}
        if reviewed and not no_speech:
            request = {'source_sha256': report['source_sha256'], 'edits': [],
                       'pending_review': [{'index': 0, 'issue': 'Synthetic wording uncertainty.'}] if pending else [],
                       'overview': {'summary': 'A synthetic fixture summary.',
                           'sections': [{'start': 0, 'end': 6, 'title': 'Fixture chapter', 'summary': 'Fixture text only.'}],
                           'limitations': ['No real video or acoustic claim is made by this fixture.']}}
            self.engine.review(check_id, request)
        return self.engine.report(check_id)

    def visual(self, report, *, quality='not_assessed'):
        work = report['latest']
        frame_set = 'f' * 32
        root = self.engine.paths.reports / 'archives' / ARCHIVE / 'visual' / work['video_id']
        folder = root / frame_set
        folder.mkdir(parents=True)
        frames = []
        for index, seconds in enumerate((1.0, 3.0, 5.0), 1):
            path = folder / f'frame-{index:03d}.png'
            path.write_bytes(PNG)
            frames.append({'time_seconds': seconds, 'path': str(path), 'sha256': digest(path), 'bytes': len(PNG)})
        manifest = {'archive_id': ARCHIVE, 'video_id': work['video_id'], 'check_id': report['check_id'],
                    'frame_set_id': frame_set, 'media_sha256': report['media_checkpoint']['sha256'],
                    'media_check_id': report['check_id'], 'duration_seconds': 6, 'frames': frames,
                    'asr_status': 'no_speech' if report.get('error_code') == 'no_speech' else 'speech'}
        dump(folder / 'frames.json', manifest)
        overview = dict(manifest, summary='Synthetic sampled-frame fixture.',
                        observations=[{'time_seconds': item['time_seconds'], 'description': 'Synthetic image fixture.'} for item in frames],
                        limitations=['Only synthetic frames are described; no real media was observed.'],
                        speech_quality=quality, quality_note='Synthetic unresolved recognition warning.' if quality == 'unresolved' else '')
        dump(root / 'overview.json', overview)
        return root, overview

    def exported(self, result):
        return json.loads(Path(result['export_paths']['manifest']).read_text(encoding='utf-8'))

    def test_reviewed_export_preserves_hashes_exact_ids_times_and_zip_exclusions(self):
        work = self.work(title='Escaped </script><script>alert("fixture")</script> title')
        report = self.report(work)
        before = {name: digest(path) for name, path in report['files'].items()}
        self.persist()
        result = self.exporter.export(ARCHIVE, include_media=True)
        summary = result['summary'];manifest = self.exported(result);record = manifest['works'][0]
        self.assertTrue(summary['delivery_complete'])
        self.assertEqual((summary['raw_transcripts'], summary['reviewed_videos'], summary['original_media_files']), (1, 1, 1))
        self.assertEqual(record['video_id'], SPEECH)
        self.assertEqual(record['sampled_at'], SAMPLED)
        self.assertEqual(before, {name: digest(path) for name, path in report['files'].items()})
        raw = json.loads((Path(result['export_paths']['folder']) / record['links']['raw_segments']).read_text())
        revised = json.loads((Path(result['export_paths']['folder']) / record['links']['corrected_segments']).read_text())
        self.assertEqual([(s['start'], s['end']) for s in raw], [(s['start'], s['end']) for s in revised])
        html = Path(result['export_paths']['index']).read_text(encoding='utf-8')
        self.assertNotIn('</script><script>alert', html)
        data = Path(result['export_paths']['csv']).read_bytes();self.assertTrue(data.startswith(b'\xef\xbb\xbf'))
        row = next(csv.DictReader(io.StringIO(data.decode('utf-8-sig'))))
        self.assertEqual(row['Video ID (text)'], "'" + SPEECH)
        with zipfile.ZipFile(result['export_paths']['companion_zip']) as archive:
            self.assertIsNone(archive.testzip())
            self.assertFalse(any(name.endswith(('.mp4', '.sqlite3', '.log')) or Path(name).name in ('report.json', 'review.json', 'photos.json') for name in archive.namelist()))
            packed = archive.read('content-manifest.json')
            self.assertNotIn(str(self.engine.paths.home).encode(), packed)
            self.assertNotIn(b'media_checkpoint', packed)
            metadata = json.loads(packed)
            self.assertTrue(metadata['summary']['media_excluded'])
            self.assertFalse(metadata['summary']['folder_delivery_complete'])
            self.assertNotIn('video_original', metadata['works'][0]['links'])
            for line in archive.read('SHA256SUMS.txt').decode().splitlines():
                expected, name = line.split('  ', 1)
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), expected)

    def test_unique_snapshots_do_not_overwrite_a_previous_export(self):
        self.report(self.work());self.persist()
        first = self.exporter.export(ARCHIVE)
        before = Path(first['export_paths']['manifest']).read_bytes()
        second = self.exporter.export(ARCHIVE)
        self.assertNotEqual(first['export_id'], second['export_id'])
        self.assertEqual(Path(first['export_paths']['manifest']).read_bytes(), before)
        self.assertEqual(second['summary']['original_media_files'], 0)

    def test_pending_wording_is_separate_from_unresolved_speech_quality(self):
        self.report(self.work(), pending=True);self.persist()
        result = self.exporter.export(ARCHIVE)
        self.assertEqual(result['summary']['review_pending_segments'], 1)
        self.assertEqual(result['summary']['speech_quality_flagged_videos'], 0)
        self.assertTrue(any('Uncertain wording' in text for text in result['quality_caveats']))

    def test_raw_ready_does_not_claim_reviewed_or_complete_delivery(self):
        self.report(self.work(), reviewed=False);self.persist()
        result = self.exporter.export(ARCHIVE)
        self.assertEqual(result['summary']['raw_transcripts'], 1)
        self.assertEqual(result['summary']['reviewed_videos'], 0)
        self.assertFalse(result['summary']['delivery_complete'])
        self.assertNotIn('corrected_md', self.exported(result)['works'][0]['links'])

    def test_unprocessed_and_unknown_metrics_remain_explicit(self):
        self.work(statistics={'like': None, 'comment': 0, 'collect': None, 'share': 1});self.persist(complete=False)
        result = self.exporter.export(ARCHIVE, include_media=True)
        record = self.exported(result)['works'][0]
        self.assertIsNone(record['statistics']['like'])
        self.assertEqual(record['statistics']['comment'], 0)
        self.assertEqual(record['status'], 'pending')
        self.assertFalse(result['summary']['inventory_complete'])
        self.assertEqual(result['summary']['unprocessed_videos'], 1)

    def test_no_speech_without_visual_context_remains_partial_without_fake_transcripts(self):
        self.report(self.work(NO_SPEECH), no_speech=True);self.persist()
        result = self.exporter.export(ARCHIVE, include_media=True)
        record = self.exported(result)['works'][0]
        self.assertEqual(record['status'], 'no_speech')
        self.assertEqual(result['summary']['raw_transcripts'], 0)
        self.assertEqual(result['summary']['visual_video_overviews'], 0)
        self.assertFalse(result['summary']['delivery_complete'])
        self.assertNotIn('raw_md', record['links'])
        self.assertIn('video_original', record['links'])

    def test_no_speech_visual_delivery_requires_media_in_folder_not_companion(self):
        report = self.report(self.work(NO_SPEECH), no_speech=True);self.visual(report);self.persist()
        text_only = self.exporter.export(ARCHIVE)
        self.assertTrue(text_only['summary']['content_processing_complete'])
        self.assertFalse(text_only['summary']['folder_delivery_complete'])
        result = self.exporter.export(ARCHIVE, include_media=True)
        self.assertTrue(result['summary']['folder_delivery_complete'])
        self.assertEqual(result['summary']['sampled_video_frames'], 3)
        with zipfile.ZipFile(result['export_paths']['companion_zip']) as archive:
            packed = json.loads(archive.read('content-manifest.json'))
            self.assertFalse(packed['summary']['delivery_complete'])
            self.assertFalse(packed['works'][0]['video_content_ready'])
            self.assertEqual(len(packed['works'][0]['video_frames']), 3)
            self.assertIn(b'PRIMARY LOCAL FOLDER', archive.read('README.md'))

    def test_speech_visual_context_flags_quality_without_double_counting_or_replacing_asr(self):
        report = self.report(self.work(), pending=True);self.visual(report, quality='unresolved');self.persist()
        result = self.exporter.export(ARCHIVE, include_media=True)
        self.assertEqual(result['summary']['video_content_processed'], 1)
        self.assertEqual(result['summary']['speech_quality_flagged_videos'], 1)
        record = self.exported(result)['works'][0]
        self.assertTrue(record['speech_transcribed'])
        self.assertIn('raw_md', record['links'])
        self.assertIn('visual_overview', record['links'])

    def test_native_image_checkpoint_exports_only_published_images(self):
        work = self.work(IMAGE, kind='image')
        folder = self.engine.paths.reports / 'archives' / ARCHIVE / 'images' / IMAGE
        folder.mkdir(parents=True);image = folder / 'image-001.png';image.write_bytes(PNG)
        screenshot = folder / 'post-page.png';screenshot.write_bytes(PNG)
        dump(folder / 'photos.json', {'post_url': work['url'], 'content_kind': 'image_post', 'status': 'done',
            'complete': True, 'images': [str(image)], 'image_count': 1, 'screenshot_path': str(screenshot), 'captured_at': SAMPLED})
        self.persist();result = self.exporter.export(ARCHIVE)
        self.assertTrue(result['summary']['delivery_complete'])
        self.assertEqual(result['summary']['captured_images'], 1)
        with zipfile.ZipFile(result['export_paths']['companion_zip']) as archive:
            self.assertFalse(any('post-page' in name or name.endswith('photos.json') for name in archive.namelist()))

    def test_original_hash_change_stops_export_and_preserves_changed_source(self):
        report = self.report(self.work());self.persist()
        source = Path(report['files']['transcript.txt']);source.write_text('Changed fixture', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'immutable raw source changed'):
            self.exporter.export(ARCHIVE)
        self.assertEqual(source.read_text(), 'Changed fixture')

    def test_review_cannot_change_timeline(self):
        report = self.report(self.work());self.persist()
        path = Path(report['review']['files']['corrected_segments.json'])
        revised = json.loads(path.read_text());revised[0]['end'] += .1;dump(path, revised)
        with self.assertRaisesRegex(ValueError, 'timestamps or identity changed'):
            self.exporter.export(ARCHIVE)

    def test_raw_source_outside_owned_check_is_rejected_even_with_same_hash(self):
        report = self.report(self.work());self.persist()
        outside = self.engine.paths.home / 'outside' / 'transcript.txt'
        outside.parent.mkdir();outside.write_bytes(Path(report['files']['transcript.txt']).read_bytes())
        report['files']['transcript.txt'] = str(outside);self.engine.store.save(report)
        with self.assertRaisesRegex(ValueError, 'outside its application-owned directory'):
            self.exporter.export(ARCHIVE)

    def test_native_frame_hash_change_is_rejected(self):
        report = self.report(self.work(NO_SPEECH), no_speech=True);root, overview = self.visual(report);self.persist()
        Path(overview['frames'][0]['path']).write_bytes(PNG + b'changed')
        with self.assertRaisesRegex(ValueError, 'frame size changed|frame checksum changed'):
            self.exporter.export(ARCHIVE, include_media=True)

    def test_incomplete_coverage_does_not_claim_all_history(self):
        self.report(self.work());self.persist()
        path = self.engine.paths.home / 'archives' / (ARCHIVE + '.json')
        snapshot = json.loads(path.read_text());snapshot['coverage']['count_mismatch'] = True;dump(path, snapshot)
        self.assertFalse(self.exporter.export(ARCHIVE)['summary']['delivery_complete'])

    def test_input_validation_and_bounded_portable_size(self):
        self.work();self.persist()
        for invalid in ('../outside', 12, None):
            with self.subTest(archive=invalid), self.assertRaises(ValueError):self.exporter.export(invalid)
        with self.assertRaises(ValueError):self.exporter.export(ARCHIVE, include_media='true')
        with patch('creator_notes.archive_export.MAX_PORTABLE_BYTES', 100):
            with self.assertRaisesRegex(ValueError, 'bounded export size limit'):self.exporter.export(ARCHIVE)

    def test_missing_optional_media_is_partial_when_original_media_was_requested(self):
        self.report(self.work(), media=False);self.persist()
        result = self.exporter.export(ARCHIVE, include_media=True)
        self.assertEqual(result['summary']['reviewed_videos'], 1)
        self.assertTrue(result['summary']['content_processing_complete'])
        self.assertFalse(result['summary']['folder_delivery_complete'])

    def test_reused_asr_gets_only_verified_matching_prior_native_media(self):
        work = self.work();prior = self.report(work, check_id='c' * 32)
        repeat = self.report(work, check_id='d' * 32, media=False)
        repeat['transcription']['reused'] = True;self.engine.store.save(repeat);self.persist()
        result = self.exporter.export(ARCHIVE, include_media=True)
        record = self.exported(result)['works'][0]
        copied = Path(result['export_paths']['folder']) / record['links']['video_original']
        self.assertEqual(digest(copied), prior['media_checkpoint']['sha256'])
        self.assertTrue(result['summary']['folder_delivery_complete'])

    def test_queued_native_job_uses_its_bound_archive_source_and_remains_pending(self):
        work = self.work();cid = 'e' * 32
        self.engine.store.save({'check_id': cid, 'archive_id': ARCHIVE, 'archive_video_id': work['video_id'],
            'status': 'queued', 'archive_source': {'profile': PROFILE, 'latest': work, 'collected_at': SAMPLED}})
        self.links[work['video_id']] = {'check_id': cid};self.persist()
        result = self.exporter.export(ARCHIVE, include_media=True)
        self.assertEqual(self.exported(result)['works'][0]['status'], 'pending')
        self.assertFalse(result['summary']['folder_delivery_complete'])

    def test_corrupt_present_media_checkpoint_stops_export(self):
        report = self.report(self.work());self.persist()
        Path(report['media_checkpoint']['path']).write_bytes(b'changed synthetic media')
        with self.assertRaisesRegex(ValueError, 'Saved media changed or is missing'):
            self.exporter.export(ARCHIVE, include_media=True)

    def test_missing_reused_media_returns_an_honest_partial_snapshot(self):
        report = self.report(self.work(), media=False)
        report['transcription']['reused'] = True;self.engine.store.save(report);self.persist()
        result = self.exporter.export(ARCHIVE, include_media=True)
        self.assertTrue(self.exported(result)['works'][0]['media_unavailable'])
        self.assertEqual(result['summary']['reviewed_videos'], 1)
        self.assertFalse(result['summary']['folder_delivery_complete'])

    def test_counter_sampling_mismatch_does_not_relabel_old_data(self):
        report = self.report(self.work());self.persist()
        report['collected_at'] = '2025-01-02T07:00:00+00:00';self.engine.store.save(report)
        with self.assertRaisesRegex(ValueError, 'sampling time differ'):
            self.exporter.export(ARCHIVE)

    def test_linked_check_for_another_creator_is_rejected(self):
        report = self.report(self.work());self.persist()
        report['profile'] = dict(PROFILE, sec_uid='another-synthetic-author');self.engine.store.save(report)
        with self.assertRaisesRegex(ValueError, 'not bound to this public archive work'):
            self.exporter.export(ARCHIVE)

    def test_csv_neutralizes_formula_like_title_and_quality_note(self):
        report = self.report(self.work(title=' =1+1'))
        root, overview = self.visual(report, quality='unresolved')
        overview['quality_note'] = '=1+1';dump(root / 'overview.json', overview);self.persist()
        result = self.exporter.export(ARCHIVE)
        rows = csv.DictReader(io.StringIO(Path(result['export_paths']['csv']).read_text(encoding='utf-8-sig')))
        row = next(rows)
        self.assertEqual(row['Title'], "' =1+1")
        self.assertEqual(row['Speech quality warning'], "'=1+1")


if __name__ == '__main__':
    unittest.main()

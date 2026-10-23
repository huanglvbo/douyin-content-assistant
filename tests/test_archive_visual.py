"""Native visual context against generated media, without network or login."""
import copy
import json
from pathlib import Path
import unittest

import av
import numpy as np

from creator_notes.archive import ArchiveManager
from creator_notes.archive_visual import ArchiveVisuals, load_video_overview, verified_media
import test_archive_export as fixtures


def make_video(path):
    with av.open(str(path), mode='w') as container:
        stream = container.add_stream('mpeg4', rate=12)
        stream.width, stream.height, stream.pix_fmt = 32, 32, 'yuv420p'
        for index in range(24):
            pixels = np.zeros((32, 32, 3), dtype=np.uint8)
            pixels[:, :, index % 3] = 100 + index
            frame = av.VideoFrame.from_ndarray(pixels, format='rgb24')
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


class ArchiveVisualTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ArchiveExportTests('test_reviewed_export_preserves_hashes_exact_ids_times_and_zip_exclusions')
        self.fixture.setUp()
        self.engine = self.fixture.engine
        self.work = self.fixture.work(fixtures.NO_SPEECH)
        self.report = self.fixture.report(self.work, no_speech=True)
        self.media = Path(self.report['media_checkpoint']['path'])
        make_video(self.media)
        self.report['media_checkpoint'].update(sha256=fixtures.digest(self.media), bytes=self.media.stat().st_size)
        self.engine.store.save(self.report)
        self.fixture.persist()
        self.archives = ArchiveManager(self.engine)
        self.visual = ArchiveVisuals(self.engine, self.archives)

    def tearDown(self):
        self.archives.close()
        self.fixture.tearDown()

    def overview(self, record, **extra):
        return {'summary': 'A generated color fixture, not a real video.',
                'observations': [{'time_seconds': row['time_seconds'], 'description': 'A generated color square.'}
                                 for row in record['frames']],
                'limitations': ['Three sampled frames do not establish complete speech or silence.'], **extra}

    def test_capture_submit_load_and_export_preserve_verified_media(self):
        before = fixtures.digest(self.media)
        record = self.visual.capture(fixtures.ARCHIVE, fixtures.NO_SPEECH)
        self.assertEqual(record['asr_status'], 'no_speech')
        self.assertEqual(len({row['time_seconds'] for row in record['frames']}), 3)
        self.assertTrue(all(Path(row['path']).read_bytes().startswith(b'\x89PNG') for row in record['frames']))
        saved = self.visual.submit(fixtures.ARCHIVE, fixtures.NO_SPEECH, record['frame_set_id'], self.overview(record))
        self.assertEqual(saved['status'], 'visual_context_saved')
        loaded = load_video_overview(self.engine.paths, fixtures.ARCHIVE, fixtures.NO_SPEECH, self.report['check_id'])
        self.assertEqual(loaded['media_sha256'], before)
        result = self.fixture.exporter.export(fixtures.ARCHIVE, include_media=True)
        self.assertTrue(result['summary']['delivery_complete'])
        self.assertEqual((result['summary']['raw_transcripts'], result['summary']['visual_video_overviews']), (0, 1))
        self.assertEqual(fixtures.digest(self.media), before)

    def test_missing_overview_is_explicit_and_missing_media_has_safe_code(self):
        self.assertIsNone(load_video_overview(self.engine.paths, fixtures.ARCHIVE, fixtures.NO_SPEECH, self.report['check_id']))
        record = copy.deepcopy(self.report)
        record.pop('media_checkpoint')
        with self.assertRaises(ValueError) as caught:
            verified_media(self.engine, record)
        self.assertEqual(caught.exception.code, 'media_unavailable')

    def test_changed_frame_and_media_are_rejected_without_rewriting_them(self):
        record = self.visual.capture(fixtures.ARCHIVE, fixtures.NO_SPEECH)
        frame = Path(record['frames'][0]['path'])
        frame.write_bytes(frame.read_bytes() + b'changed')
        with self.assertRaises(ValueError):
            self.visual.submit(fixtures.ARCHIVE, fixtures.NO_SPEECH, record['frame_set_id'], self.overview(record))
        self.media.write_bytes(b'changed-original')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.visual.capture(fixtures.ARCHIVE, fixtures.NO_SPEECH)
        self.assertEqual(self.media.read_bytes(), b'changed-original')

    def test_wrong_ids_times_and_uninspected_frames_cannot_be_submitted(self):
        for times in ([True], [float('nan')], [3.0], [0.5, 0.5], list(range(13))):
            with self.subTest(times=times), self.assertRaises(ValueError):
                self.visual.capture(fixtures.ARCHIVE, fixtures.NO_SPEECH, times)
        with self.assertRaises(ValueError):
            self.visual.capture(fixtures.ARCHIVE, fixtures.SPEECH)
        record = self.visual.capture(fixtures.ARCHIVE, fixtures.NO_SPEECH)
        overview = self.overview(record)
        overview['observations'].pop()
        with self.assertRaisesRegex(ValueError, 'every'):
            self.visual.submit(fixtures.ARCHIVE, fixtures.NO_SPEECH, record['frame_set_id'], overview)
        with self.assertRaises(ValueError):
            self.visual.submit(fixtures.ARCHIVE, fixtures.NO_SPEECH, 'c' * 32, self.overview(record))

    def test_cached_media_never_crosses_creator_identity(self):
        record = copy.deepcopy(self.report)
        record.pop('media_checkpoint')
        record['check_id'] = 'd' * 32
        record['transcription']['reused'] = True
        path, source_check, checksum = verified_media(self.engine, record)
        self.assertEqual(path, self.media.resolve())
        self.assertEqual(source_check, self.report['check_id'])
        self.assertEqual(checksum, fixtures.digest(self.media))
        record['profile'] = dict(record['profile'], sec_uid='different-synthetic-author')
        with self.assertRaises(ValueError):
            verified_media(self.engine, record)

    def test_inconsistent_archive_and_latest_identity_cannot_sample_another_work(self):
        changed = copy.deepcopy(self.report)
        changed['archive_video_id'] = fixtures.SPEECH
        self.engine.store.save(changed)
        other = self.fixture.work(fixtures.SPEECH)
        self.fixture.links[other['video_id']] = {'check_id': changed['check_id']}
        self.fixture.persist()
        with self.assertRaisesRegex(ValueError, 'exact archive'):
            self.visual.capture(fixtures.ARCHIVE, fixtures.SPEECH)

    def test_explicit_quality_warning_is_bound_to_inspected_media(self):
        record = self.visual.capture(fixtures.ARCHIVE, fixtures.NO_SPEECH)
        review = self.overview(record, speech_quality='unresolved', quality_note='Synthetic speech coverage is unresolved.')
        self.visual.submit(fixtures.ARCHIVE, fixtures.NO_SPEECH, record['frame_set_id'], review)
        exported = self.fixture.exporter.export(fixtures.ARCHIVE, include_media=True)
        self.assertEqual(exported['summary']['speech_quality_flagged_videos'], 1)
        self.assertFalse(self.engine.report(self.report['check_id']).get('files'))

    def test_manifest_cannot_point_outside_its_native_frame_directory(self):
        record = self.visual.capture(fixtures.ARCHIVE, fixtures.NO_SPEECH)
        folder = Path(record['frames'][0]['path']).parent
        altered = json.loads((folder / 'frames.json').read_text(encoding='utf-8'))
        altered['frames'][0]['path'] = str(self.media)
        (folder / 'frames.json').write_text(json.dumps(altered), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'path'):
            self.visual.submit(fixtures.ARCHIVE, fixtures.NO_SPEECH, record['frame_set_id'], self.overview(record))


if __name__ == '__main__':
    unittest.main()

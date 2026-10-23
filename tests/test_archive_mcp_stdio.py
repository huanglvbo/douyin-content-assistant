"""Native archive transport against generated local media, with no network."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import unittest
import zipfile

import av
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import test_archive_export as fixtures
from test_archive_visual import make_video


def payload(result):
    if result.isError:
        raise AssertionError('A native archive MCP tool returned an error')
    return result.structuredContent or json.loads(result.content[0].text)


class ArchiveMCPTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_frames_overview_export_and_reconnect(self):
        fixture = fixtures.ArchiveExportTests('test_no_speech_visual_delivery_requires_media_in_folder_not_companion')
        fixture.setUp()
        try:
            work = fixture.work(fixtures.NO_SPEECH, duration_seconds=2)
            report = fixture.report(work, no_speech=True)
            media = Path(report['media_checkpoint']['path'])
            make_video(media)
            original_hash = fixtures.digest(media)
            report['media_checkpoint'].update(sha256=original_hash, bytes=media.stat().st_size)
            report['transcription']['duration_seconds'] = 2
            fixture.engine.store.save(report)
            fixture.persist()
            paths = fixture.engine.paths
            fixture.engine.close()
            parameters = StdioServerParameters(command=sys.executable,
                args=['-m', 'creator_notes.server'], env={
                    'CREATOR_NOTES_HOME': str(paths.home), 'CREATOR_NOTES_OUTPUT': str(paths.reports),
                    'CREATOR_NOTES_MODELS': str(paths.models),
                    'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src'),
                    'PYTHONIOENCODING': 'utf-8', 'PYTHONDONTWRITEBYTECODE': '1',
                    'HF_HUB_OFFLINE': '1', 'HF_DATASETS_OFFLINE': '1',
                })

            async def call(session, name, arguments):
                return payload(await asyncio.wait_for(session.call_tool(name, arguments), timeout=30))

            def verify_export(result):
                summary = result['summary']
                self.assertTrue(summary['inventory_complete'])
                self.assertTrue(summary['content_processing_complete'])
                self.assertTrue(summary['folder_delivery_complete'])
                self.assertTrue(summary['delivery_complete'])
                self.assertEqual({key: summary[key] for key in ('total_works', 'videos', 'no_speech_videos',
                    'visual_video_overviews', 'sampled_video_frames', 'original_media_files')},
                    {'total_works': 1, 'videos': 1, 'no_speech_videos': 1, 'visual_video_overviews': 1,
                     'sampled_video_frames': 3, 'original_media_files': 1})
                self.assertEqual((summary['raw_transcripts'], summary['reviewed_videos']), (0, 0))
                manifest = json.loads(Path(result['export_paths']['manifest']).read_text(encoding='utf-8'))
                record = manifest['works'][0]
                self.assertEqual(record['video_id'], fixtures.NO_SPEECH)
                self.assertEqual(record['sampled_at'], fixtures.SAMPLED)
                self.assertEqual(record['status'], 'no_speech')
                self.assertFalse(record['speech_transcribed'])
                self.assertNotIn('raw_md', record['links'])
                self.assertNotIn('corrected_md', record['links'])
                folder = Path(result['export_paths']['folder'])
                self.assertEqual(fixtures.digest(folder / record['links']['video_original']), original_hash)
                self.assertEqual(record['source_media_receipt']['sha256'], original_hash)
                self.assertEqual(len(record['video_frames']), 3)
                for frame in record['video_frames']:
                    self.assertEqual(fixtures.digest(folder / frame['path']), frame['sha256'])
                with zipfile.ZipFile(result['export_paths']['companion_zip']) as archive:
                    self.assertIsNone(archive.testzip())
                    self.assertFalse(any(name.lower().endswith(('.mp4', '.sqlite3', '.log'))
                        or Path(name).name in ('report.json', 'review.json', 'photos.json', 'frames.json',
                                               'overview.json', 'storage-state.json') for name in archive.namelist()))
                    self.assertFalse(any('/raw-' in name or '/review-' in name for name in archive.namelist()))
                    portable = json.loads(archive.read('content-manifest.json'))
                    self.assertTrue(portable['summary']['media_excluded'])
                    self.assertEqual(portable['summary']['delivery_scope'], 'text_and_images_companion')
                    self.assertFalse(portable['summary']['folder_delivery_complete'])
                    self.assertFalse(portable['summary']['delivery_complete'])
                    self.assertNotIn('video_original', portable['works'][0]['links'])
                    self.assertEqual(len(portable['works'][0]['video_frames']), 3)
                    for line in archive.read('SHA256SUMS.txt').decode().splitlines():
                        expected, relative = line.split('  ', 1)
                        self.assertEqual(hashlib.sha256(archive.read(relative)).hexdigest(), expected)
                    self.assertNotIn(str(paths.home).encode(), archive.read('content-manifest.json'))
                return record

            async with stdio_client(parameters) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await asyncio.wait_for(session.initialize(), timeout=20)
                    names = {tool.name for tool in (await session.list_tools()).tools}
                    self.assertTrue({'capture_archive_video_frames', 'submit_archive_video_overview',
                                     'export_creator_archive'}.issubset(names))
                    captured = await call(session, 'capture_archive_video_frames',
                        {'archive_id': fixtures.ARCHIVE, 'video_id': fixtures.NO_SPEECH})
                    self.assertEqual(captured['media_sha256'], original_hash)
                    self.assertEqual(captured['asr_status'], 'no_speech')
                    self.assertEqual(len(captured['frames']), 3)
                    for frame in captured['frames']:
                        image = Path(frame['path'])
                        self.assertTrue(image.resolve().is_relative_to(paths.reports.resolve()))
                        self.assertEqual(fixtures.digest(image), frame['sha256'])
                        # Actually decode every generated PNG returned by the
                        # native tool; this remains a synthetic fixture claim.
                        with av.open(str(image)) as container:
                            decoded = next(container.decode(video=0))
                            self.assertEqual((decoded.width, decoded.height), (32, 32))
                    overview = {'summary': 'A generated color-square fixture, not a real creator video.',
                        'observations': [{'time_seconds': row['time_seconds'],
                            'description': 'A decoded generated color square from the synthetic fixture.'}
                            for row in captured['frames']],
                        'limitations': ['Only three generated frames were decoded; this test makes no real speech or silence claim.']}
                    saved = await call(session, 'submit_archive_video_overview', {
                        'archive_id': fixtures.ARCHIVE, 'video_id': fixtures.NO_SPEECH,
                        'frame_set_id': captured['frame_set_id'], 'overview': overview})
                    self.assertEqual(saved['status'], 'visual_context_saved')
                    self.assertTrue(saved['original_media_preserved'])
                    first = await call(session, 'export_creator_archive',
                        {'archive_id': fixtures.ARCHIVE, 'include_media': True})
                    first_record = verify_export(first)
                    unchanged = await call(session, 'get_report', {'check_id': report['check_id']})
                    self.assertEqual(unchanged['error_code'], 'no_speech')
                    self.assertFalse(unchanged.get('files'))
                    self.assertFalse(unchanged.get('review'))
            # A new server process loads the same durable native frame set and
            # host overview, then creates another independent export snapshot.
            async with stdio_client(parameters) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await asyncio.wait_for(session.initialize(), timeout=20)
                    second = await call(session, 'export_creator_archive',
                        {'archive_id': fixtures.ARCHIVE, 'include_media': True})
                    second_record = verify_export(second)
                    self.assertNotEqual(first['export_id'], second['export_id'])
                    self.assertEqual(first_record['video_frames'], second_record['video_frames'])
                    self.assertEqual(first_record['visual_observations'], second_record['visual_observations'])
            self.assertEqual(fixtures.digest(media), original_hash)
            self.assertFalse(any(paths.session.iterdir()))
            self.assertFalse(any(paths.models.iterdir()))
        finally:
            fixture.tearDown()


if __name__ == '__main__':
    unittest.main()

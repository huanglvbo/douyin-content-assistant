"""Actual MCP subprocess transport against controlled local report data."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from creator_notes.engine import Manager
from creator_notes.paths import AppPaths
from test_engine import FakeCollector, wait


def payload(result):
    if result.isError:
        raise AssertionError('MCP tool returned an error')
    return result.structuredContent or json.loads(result.content[0].text)


class MCPTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_tools_pagination_review_and_reconnect(self):
        with tempfile.TemporaryDirectory(prefix='\u539f\u751f MCP ') as folder:
            paths = AppPaths(Path(folder))
            manager = Manager(paths, collector_factory=FakeCollector,
                acquire=lambda *args, **kwargs: Path('fixture.wav'),
                recognize=lambda *args, **kwargs: {'segments': [{'start': float(i), 'end': i+.8, 'text': f'\u539f\u53e5{i}'} for i in range(229)],
                    'transcript': '\n'.join(f'\u539f\u53e5{i}' for i in range(229)), 'duration': 230, 'model': 'small'})
            try:
                job = wait(manager, manager.submit('\u6d4b\u8bd5\u535a\u4e3b'))
                self.assertEqual(job['status'], 'done')
            finally:
                manager.close()
            parameters = StdioServerParameters(command=sys.executable, args=['-m', 'creator_notes.server'],
                env={'CREATOR_NOTES_HOME': str(paths.home),
                     'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src'), 'PYTHONIOENCODING': 'utf-8'})
            async with stdio_client(parameters) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    tools = {tool.name for tool in (await session.list_tools()).tools}
                    self.assertEqual(tools, {'check_creator', 'get_report', 'get_transcript', 'submit_review',
                                            'start_login', 'login_status', 'resume_check', 'resolve_creator_from_videos',
                                            'start_creator_archive', 'get_creator_archive', 'transcribe_archive_work',
                                            'capture_archive_images', 'capture_archive_video_frames',
                                            'submit_archive_video_overview', 'export_creator_archive'})
                    schemas = {tool.name: tool.inputSchema for tool in (await session.list_tools()).tools}
                    self.assertTrue(schemas['start_login']['properties']['verification_window']['default'])
                    login = payload(await session.call_tool('login_status', {}))
                    self.assertEqual(login['status'], 'not_started')
                    report = payload(await session.call_tool('get_report', {'check_id': job['check_id']}))
                    self.assertFalse(report['delivery_complete'])
                    rejected = await session.call_tool('resume_check', {'check_id': job['check_id']})
                    self.assertTrue(rejected.isError)
                    segments, offset = [], 0
                    while True:
                        page = payload(await session.call_tool('get_transcript', {'check_id': job['check_id'], 'offset': offset, 'limit': 80}))
                        segments.extend(page['segments'])
                        offset = page['next_offset']
                        if offset is None:
                            break
                    self.assertEqual(len(segments), 229)
                    self.assertEqual([item['index'] for item in segments], list(range(229)))
                    request = {'source_sha256': page['source_sha256'], 'edits': [], 'pending_review': [],
                               'overview': {'summary': '\u53d7\u63a7\u6d4b\u8bd5\u539f\u7a3f\u3002', 'sections': [{'start': 0, 'end': 229, 'title': '\u6d4b\u8bd5', 'summary': '\u53d7\u63a7\u6d4b\u8bd5\u3002'}],
                                            'limitations': ['\u4ec5\u68c0\u9a8c\u534f\u8bae\u4f20\u8f93\uff0c\u4e0d\u662f\u771f\u5b9e\u89c6\u9891\u6216\u6a21\u578b\u9a8c\u6536\u3002']}}
                    review = payload(await session.call_tool('submit_review', {'check_id': job['check_id'], 'review': request}))
                    self.assertTrue(review['originals_preserved'])
            # Completed data and the editorial delivery survive a server restart.
            async with stdio_client(parameters) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    report = payload(await session.call_tool('get_report', {'check_id': job['check_id']}))
                    self.assertTrue(report['delivery_complete'])


if __name__ == '__main__':
    unittest.main()

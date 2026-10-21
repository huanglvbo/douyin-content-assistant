"""Public-source selection and downloader tests without a browser or network."""
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from creator_notes import media


VIDEO = '1234567890123456789'
CDN = 'https://v.example.douyinvod.com/work.mp4?secret=not-for-reports'


class Response:
    def __init__(self, status=200, body=b'media', headers=None):
        self.status = status
        self.body = io.BytesIO(body)
        self.headers = {'Content-Type': 'video/mp4', 'Content-Length': str(len(body))}
        self.headers.update(headers or {})

    def getheader(self, key):
        return self.headers.get(key)

    def read(self, length):
        return self.body.read(length)


class Connection:
    def __init__(self, response):
        self.response = response
        self.closed = False
        self.requests = []

    def request(self, *args, **kwargs):
        self.requests.append((args, kwargs))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class BrowserHarness:
    """Minimal public Playwright API fake; no browser is launched."""
    def __init__(self, captcha=False, goto_error=False):
        self.captcha = captcha
        self.goto_error = goto_error
        self.closed = False
        self.options = None
        self.callback = None
        self.chromium = SimpleNamespace(launch=lambda **_: self)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def new_context(self, **options):
        self.options = options
        return self

    def new_page(self):
        return self

    def on(self, name, callback):
        self.callback = callback

    def goto(self, *_args, **_kwargs):
        if self.goto_error:
            raise RuntimeError('secret?token=never-show')
        payload = {'aweme_detail': {'aweme_id': VIDEO, 'video': {'play_addr': {'url_list': [CDN]}}}}
        self.callback(SimpleNamespace(
            url='https://www.douyin.com/aweme/v1/web/aweme/detail/', ok=True,
            headers={}, body=lambda: json.dumps(payload).encode(),
        ))

    def locator(self, *_):
        return self

    def count(self):
        return int(self.captcha)

    def nth(self, *_):
        return self

    def is_visible(self):
        return self.captcha

    def close(self):
        self.closed = True


class MediaTests(unittest.TestCase):
    def fake_browser(self, browser):
        return patch.dict(sys.modules, {
            'playwright': SimpleNamespace(),
            'playwright.sync_api': SimpleNamespace(sync_playwright=lambda: browser),
        })

    def test_only_canonical_public_video_url_accepted(self):
        self.assertEqual(media._video_id(f'https://www.douyin.com/video/{VIDEO}'), VIDEO)
        for source in ('http://www.douyin.com/video/' + VIDEO,
                       'https://www.douyin.com.evil.test/video/' + VIDEO,
                       f'https://www.douyin.com/video/{VIDEO}?cookie=anything',
                       'file:///private.mp4', 'https://v.douyin.com/share'):
            with self.subTest(source=source), self.assertRaises(media.MediaError):
                media._video_id(source)

    def test_selects_requested_work_and_excludes_dash(self):
        payload = {'aweme_list': [
            {'aweme_id': '99999', 'video': {'play_addr': {'url_list': ['https://wrong.douyinvod.com/a']}}},
            {'aweme_id': VIDEO, 'video': {
                'play_addr': {'url_list': [CDN, CDN]},
                'bit_rate': [{'play_addr': {'url_key': 'video_dash', 'url_list': ['https://dash.douyinvod.com/a']}}],
            }},
        ]}
        self.assertEqual(media._addresses(payload, VIDEO), [CDN])

    def test_public_domain_and_dns_validation(self):
        with patch('creator_notes.media.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('8.8.8.8', 443))]):
            host, target, addresses = media._public_target(CDN)
            self.assertEqual(host, 'v.example.douyinvod.com')
            self.assertEqual(addresses, ['8.8.8.8'])
            self.assertIn('?secret=', target)
        for address in ('127.0.0.1', '10.0.0.1', '169.254.169.254', '::1'):
            with patch('creator_notes.media.socket.getaddrinfo', return_value=[(2, 1, 6, '', (address, 443))]):
                with self.subTest(address=address), self.assertRaises(media.MediaError):
                    media._public_target(CDN)
        for source in ('https://example.com/a', 'https://douyinvod.com.evil.test/a',
                       'https://name:password@v.douyinvod.com/a',
                       'http://v.douyinvod.com/a', 'https://v.douyinvod.com:8443/a'):
            with self.subTest(source=source), self.assertRaises(media.MediaError):
                media._public_target(source)

    def test_stream_download_uses_pinned_address_and_closes_connection(self):
        connection = Connection(Response(body=b'full-media'))
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'video.partial'
            with patch('creator_notes.media._public_target', return_value=('cdn.douyinvod.com', '/video', ['8.8.8.8'])), \
                 patch('creator_notes.media._PinnedHTTPSConnection', return_value=connection) as connect:
                media._download(CDN, destination)
            self.assertEqual(destination.read_bytes(), b'full-media')
            connect.assert_called_once_with('cdn.douyinvod.com', '8.8.8.8')
        self.assertTrue(connection.closed)

    def test_redirect_is_revalidated_before_request(self):
        connection = Connection(Response(status=302, headers={'Location': 'http://127.0.0.1/private'}))
        def validate(url):
            if url == CDN:
                return 'cdn.douyinvod.com', '/video', ['8.8.8.8']
            raise media.MediaError('unsafe_media_address', 'The media address failed safety checks.')
        with tempfile.TemporaryDirectory() as directory, \
             patch('creator_notes.media._public_target', side_effect=validate), \
             patch('creator_notes.media._PinnedHTTPSConnection', return_value=connection) as connect:
            with self.assertRaises(media.MediaError) as caught:
                media._download(CDN, Path(directory) / 'video.partial')
            self.assertEqual(caught.exception.code, 'unsafe_media_address')
            self.assertEqual(connect.call_count, 1)

    def test_rejects_html_incomplete_and_oversized_media(self):
        responses = [Response(headers={'Content-Type': 'text/html'}),
                     Response(body=b'123', headers={'Content-Length': '5'}),
                     Response(headers={'Content-Length': str(media.MAX_MEDIA_BYTES + 1)})]
        for index, response in enumerate(responses):
            connection = Connection(response)
            with self.subTest(case=index), tempfile.TemporaryDirectory() as directory, \
                 patch('creator_notes.media._public_target', return_value=('cdn.douyinvod.com', '/video', ['8.8.8.8'])), \
                 patch('creator_notes.media._PinnedHTTPSConnection', return_value=connection):
                with self.assertRaises(media.MediaError):
                    media._download(CDN, Path(directory) / 'video.partial')
            self.assertTrue(connection.closed)

    def test_stream_size_limit_and_cancellation(self):
        for size, callback, expected in ((2, None, 'media_size'), (100, lambda *_: False, 'cancelled')):
            connection = Connection(Response(body=b'12345', headers={'Content-Length': None}))
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory, \
                 patch('creator_notes.media.MAX_MEDIA_BYTES', size), \
                 patch('creator_notes.media._public_target', return_value=('cdn.douyinvod.com', '/video', ['8.8.8.8'])), \
                 patch('creator_notes.media._PinnedHTTPSConnection', return_value=connection):
                with self.assertRaises(media.MediaError) as caught:
                    media._download(CDN, Path(directory) / 'video.partial', callback)
                self.assertEqual(caught.exception.code, expected)

    def test_full_fake_acquisition_uses_dedicated_session_and_atomic_file(self):
        browser = BrowserHarness()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / 'dedicated-state.json'
            state.write_text('{}')
            def download(url, destination, progress):
                self.assertEqual(url, CDN)
                destination.write_bytes(b'example-audio-video')
            with self.fake_browser(browser), \
                 patch('creator_notes.media._download', side_effect=download), \
                 patch('creator_notes.media._has_audio', return_value=True):
                completed = media.acquire_video(f'https://www.douyin.com/video/{VIDEO}', root, state)
            self.assertEqual(browser.options['storage_state'], str(state))
            self.assertNotIn('user_data_dir', browser.options)
            self.assertEqual(completed.suffix, '.mp4')
            self.assertEqual(completed.read_bytes(), b'example-audio-video')
            self.assertEqual(list(root.glob('*.partial')), [])
        self.assertTrue(browser.closed)

    def test_captcha_stops_even_with_observed_metadata(self):
        browser = BrowserHarness(captcha=True)
        with tempfile.TemporaryDirectory() as directory, self.fake_browser(browser), \
             patch('creator_notes.media._download') as download:
            with self.assertRaises(media.MediaError) as caught:
                media.acquire_video(f'https://www.douyin.com/video/{VIDEO}', Path(directory))
            self.assertEqual(caught.exception.code, 'captcha')
            download.assert_not_called()
        self.assertTrue(browser.closed)

    def test_browser_failure_does_not_expose_source_errors(self):
        browser = BrowserHarness(goto_error=True)
        with tempfile.TemporaryDirectory() as directory, self.fake_browser(browser):
            with self.assertRaises(media.MediaError) as caught:
                media.acquire_video(f'https://www.douyin.com/video/{VIDEO}', Path(directory))
            self.assertEqual(caught.exception.code, 'browser_failed')
            self.assertNotIn('token', str(caught.exception))
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertTrue(browser.closed)

    def test_no_audio_file_is_not_returned_and_partial_is_removed(self):
        browser = BrowserHarness()
        with tempfile.TemporaryDirectory() as directory, self.fake_browser(browser), \
             patch('creator_notes.media._download', side_effect=lambda _url, path, _progress: path.write_bytes(b'video-only')), \
             patch('creator_notes.media._has_audio', return_value=False):
            with self.assertRaises(media.MediaError) as caught:
                media.acquire_video(f'https://www.douyin.com/video/{VIDEO}', Path(directory))
            self.assertEqual(caught.exception.code, 'no_audio')
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_total_download_deadline(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch('creator_notes.media.time.monotonic', side_effect=[0, 601]), \
             patch('creator_notes.media._PinnedHTTPSConnection') as connection:
            with self.assertRaises(media.MediaError) as caught:
                media._download(CDN, Path(directory) / 'video.partial')
            self.assertEqual(caught.exception.code, 'media_timeout')
            connection.assert_not_called()


if __name__ == '__main__':
    unittest.main()

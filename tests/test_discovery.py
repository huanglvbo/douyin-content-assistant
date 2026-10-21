"""Offline contracts for caller-observed video candidates and live authors."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from creator_notes import discovery
from creator_notes.collector import CollectorError

URL1 = "https://www.douyin.com/video/1234567890123456789"
URL2 = "https://www.douyin.com/video/2234567890123456789"
URL3 = "https://www.douyin.com/video/3234567890123456789"


def profile(sec_uid="MS4wLjABfirst", nickname="\u6d4b\u8bd5\u535a\u4e3b", unique_id="tester"):
    return {"sec_uid": sec_uid, "nickname": nickname, "unique_id": unique_id,
            "short_id": "", "profile_url": "https://www.douyin.com/user/" + sec_uid, "signature": ""}


class StubCollector:
    entered = 0

    def __init__(self, **kwargs):
        self.data_dir = kwargs.get("data_dir")

    async def __aenter__(self):
        type(self).entered += 1
        return self

    async def __aexit__(self, *_):
        pass


class CaptchaNode:
    async def count(self):
        return 1

    def nth(self, _index):
        return self

    async def is_visible(self, **_kwargs):
        return True


class CaptchaPage:
    def __init__(self):
        self.receiver = None
        self.reloads = 0
        self.closed = False

    def on(self, _event, receiver):
        self.receiver = receiver

    def remove_listener(self, _event, _receiver):
        self.receiver = None

    async def goto(self, _url, **_kwargs):
        async def payload():
            return {"status_code": 0, "aweme_detail": {"aweme_id": "1234567890123456789", "author": profile()}}
        self.receiver(SimpleNamespace(url="https://www.douyin.com/aweme/v1/web/aweme/detail/", ok=True, json=payload))
        await asyncio.sleep(0)

    async def reload(self, **_kwargs):
        self.reloads += 1

    def locator(self, _selector):
        return CaptchaNode()

    async def close(self):
        self.closed = True


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_visible_captcha_stops_even_with_matching_detail(self):
        page = CaptchaPage()
        collector = SimpleNamespace(_context=SimpleNamespace(new_page=AsyncMock(return_value=page)))
        with self.assertRaises(CollectorError) as error:
            await discovery.author_from_video(collector, URL1)
        self.assertEqual("captcha", error.exception.code)
        self.assertEqual(0, page.reloads)
        self.assertTrue(page.closed)

    def test_only_official_https_video_urls_are_accepted(self):
        self.assertEqual("1234567890123456789", discovery.video_id(URL1 + "?from=search#top"))
        invalid = [None, {}, "http://www.douyin.com/video/1234567890123456789",
            "https://www.douyin.com.evil.test/video/1234567890123456789",
            "https://user:password@www.douyin.com/video/1234567890123456789",
            "https://www.douyin.com:443/video/1234567890123456789",
            "https://www.douyin.com:notaport/video/1234567890123456789",
            "https://v.douyin.com/short", "file:///video/1234567890123456789",
            "https://www.douyin.com/video/12345"]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                discovery.video_id(value)

    async def test_every_url_is_validated_before_browser_start(self):
        StubCollector.entered = 0
        with patch.object(discovery, "BrowserCollector", StubCollector), \
             patch.object(discovery, "author_from_video", AsyncMock()) as author:
            with self.assertRaises(ValueError):
                await discovery.resolve_from_public_videos("\u6d4b\u8bd5\u535a\u4e3b", [URL1, "https://evil.test/"], Path("unused"))
            self.assertEqual(0, StubCollector.entered)
            author.assert_not_awaited()

    async def test_unique_exact_author_resolves_after_url_deduplication(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(discovery, "BrowserCollector", StubCollector), \
             patch.object(discovery, "author_from_video", AsyncMock(return_value=profile())) as author:
            result = await discovery.resolve_from_public_videos("\u6d4b\u8bd5\u535a\u4e3b", [URL1, URL1 + "?from=search"], tmp)
            self.assertEqual("resolved", result["status"])
            self.assertEqual(profile(), result["profile"])
            self.assertEqual([URL1], result["candidate_urls"])
            self.assertEqual(1, author.await_count)
            self.assertEqual([], list(Path(tmp).iterdir()))

    async def test_duplicate_exact_names_require_selection(self):
        with patch.object(discovery, "BrowserCollector", StubCollector), patch.object(discovery, "author_from_video", AsyncMock(
                side_effect=[profile(), profile("MS4wLjABsecond", unique_id="second")])):
            result = await discovery.resolve_from_public_videos("\u6d4b\u8bd5\u535a\u4e3b", [URL1, URL2], "unused")
        self.assertEqual("ambiguous", result["status"])
        self.assertEqual("duplicate_exact_match", result["reason"])

    async def test_partial_verification_cannot_resolve(self):
        with patch.object(discovery, "BrowserCollector", StubCollector), patch.object(discovery, "author_from_video", AsyncMock(
                side_effect=[profile(), CollectorError("no_data", "\u672a\u6838\u9a8c")])):
            result = await discovery.resolve_from_public_videos("\u6d4b\u8bd5\u535a\u4e3b", [URL1, URL2], "unused")
        self.assertEqual("ambiguous", result["status"])
        self.assertEqual("not_all_candidates_verified", result["reason"])

    async def test_captcha_stops_remaining_candidates(self):
        with patch.object(discovery, "BrowserCollector", StubCollector), patch.object(discovery, "author_from_video", AsyncMock(
                side_effect=[profile(), CollectorError("captcha", "\u9700\u9a8c\u8bc1"), profile()])) as author:
            result = await discovery.resolve_from_public_videos("\u6d4b\u8bd5\u535a\u4e3b", [URL1, URL2, URL3], "unused")
        self.assertEqual(2, author.await_count)
        self.assertEqual("ambiguous", result["status"])
        self.assertEqual("captcha", result["failures"][0]["error_code"])

    async def test_fuzzy_name_does_not_resolve(self):
        with patch.object(discovery, "BrowserCollector", StubCollector), patch.object(discovery, "author_from_video", AsyncMock(
                return_value=profile(nickname="\u6d4b\u8bd5\u535a\u4e3b\u5b98\u65b9"))):
            result = await discovery.resolve_from_public_videos("\u6d4b\u8bd5\u535a\u4e3b", [URL1], "unused")
        self.assertEqual("ambiguous", result["status"])
        self.assertEqual("no_exact_match", result["reason"])


if __name__ == "__main__":
    unittest.main()

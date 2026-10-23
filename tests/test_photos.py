"""Published-image capture fixtures; no live browser or network is used."""
import asyncio
import base64
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from creator_notes.photos import MAX_IMAGE_BYTES, _allowed_image_url, capture_image_post


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jX1EAAAAASUVORK5CYII=")
PROFILE = {"sec_uid": "target", "nickname": "Creator"}
WORK = {"video_id": "12345", "kind": "image", "author_sec_uid": "target",
        "url": "https://www.douyin.com/video/12345"}
IMAGE = "https://p3-sign.douyinpic.com/published/image.webp?token=SECRET"


def detail(rows, author="target", identifier="12345", **extra):
    payload = {"status_code": 0, "aweme_detail": {"aweme_id": identifier,
               "author": {"sec_uid": author}, "images": rows}, **extra}
    return SimpleNamespace(url="https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=12345",
                           ok=True, json=AsyncMock(return_value=payload))


def image_response(url=IMAGE, status=200, headers=None, data=PNG):
    return SimpleNamespace(url=url, status=status,
                           headers=headers or {"content-type": "image/png", "content-length": str(len(data))},
                           body=AsyncMock(return_value=data), dispose=AsyncMock())


class FakePage:
    def __init__(self, responses, challenge=False):
        self.responses = responses
        self.challenge = challenge
        self.handler = None
        self.close = AsyncMock()

    def on(self, event, handler):
        self.handler = handler

    def remove_listener(self, event, handler):
        self.handler = None

    async def goto(self, url, **kwargs):
        for response in self.responses:
            self.handler(response)

    def locator(self, selector):
        return SimpleNamespace(inner_text=AsyncMock(return_value="Public image post"))

    async def screenshot(self, path, **kwargs):
        Path(path).write_bytes(PNG)


class FakeCollector:
    def __init__(self, page, request):
        self._context = SimpleNamespace(new_page=AsyncMock(return_value=page), request=request)
        self._warm_up = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class ImageAddressTests(unittest.TestCase):
    def test_exact_official_cdn_suffix_and_https_are_required(self):
        self.assertTrue(_allowed_image_url(IMAGE))
        for address in ("https://douyinpic.com.evil.invalid/image.png", "http://byteimg.com/image.png",
                        "https://user:password@byteimg.com/image.png", "https://byteimg.com:444/image.png",
                        "https://byteimg.com/image.png#fragment", "https://127.0.0.1/image.png"):
            self.assertFalse(_allowed_image_url(address))


class PhotoCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.reports = Path(self.temp.name) / "reports"
        self.session = Path(self.temp.name) / "session"
        self.destination = self.reports / "photos"
        self.env = patch.dict(os.environ, {"CREATOR_NOTES_OUTPUT": str(self.reports)})
        self.env.start()
        self.addCleanup(self.env.stop)
        real_sleep = asyncio.sleep

        async def fast_sleep(*args):
            await real_sleep(0)

        self.sleep = patch("creator_notes.photos.asyncio.sleep", fast_sleep)
        self.sleep.start()
        self.addCleanup(self.sleep.stop)
        self.challenge = patch("creator_notes.photos._challenge_visible",
                               AsyncMock(side_effect=lambda page: page.challenge))
        self.challenge.start()
        self.addCleanup(self.challenge.stop)

    async def capture(self, rows, responses=None, challenge=False, work=None, extra_details=None):
        page = FakePage((extra_details or []) + [detail(rows)], challenge=challenge)
        request = SimpleNamespace(get=AsyncMock(side_effect=responses or [image_response() for _ in rows]))
        collector = FakeCollector(page, request)
        with patch("creator_notes.photos.BrowserCollector", return_value=collector):
            result = await capture_image_post(PROFILE, work or WORK, self.destination, self.session)
        return result, page, request

    async def test_all_image_slots_and_page_capture_are_saved_without_signed_urls(self):
        rows = [{"url_list": [IMAGE]}, {"display_image": {"url_list": [IMAGE]}}]
        result, page, request = await self.capture(rows)
        self.assertTrue(result["complete"])
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["image_count"], 2)
        self.assertEqual(len(result["images"]), 2)
        self.assertFalse(result["speech_transcribed"])
        self.assertTrue(all(Path(path).read_bytes() == PNG for path in result["images"]))
        self.assertTrue(Path(result["screenshot_path"]).is_file())
        manifest = next(self.destination.glob("capture-*/photos.json")).read_text(encoding="utf-8")
        self.assertNotIn("SECRET", manifest)
        self.assertNotIn("douyinpic.com", manifest)
        self.assertEqual(json.loads(manifest)["post_url"], WORK["url"])
        self.assertEqual(request.get.await_count, 2)
        page.close.assert_awaited_once()

    async def test_foreign_author_detail_does_not_supply_image_slots(self):
        foreign = detail([{"url_list": [IMAGE]}] * 3, author="other")
        result, _, _ = await self.capture([{"url_list": [IMAGE]}], extra_details=[foreign])
        self.assertEqual(result["image_count"], 1)
        self.assertTrue(result["complete"])

    async def test_captcha_stops_before_page_capture_and_download(self):
        result, _, request = await self.capture([{"url_list": [IMAGE]}], challenge=True)
        self.assertEqual(result["status"], "captcha")
        self.assertFalse(result["complete"])
        self.assertIsNone(result["screenshot_path"])
        request.get.assert_not_awaited()

    async def test_missing_asset_is_partial_and_keeps_other_images(self):
        result, _, _ = await self.capture([{"url_list": [IMAGE]}, {"url_list": ["https://evil.invalid/a.png"]}],
                                          responses=[image_response()])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["image_count"], 2)
        self.assertEqual(len(result["images"]), 1)
        self.assertEqual(result["errors"][-1]["code"], "image_address_missing")

    async def test_non_image_response_is_rejected(self):
        response = image_response(headers={"content-type": "text/html", "content-length": "5"}, data=b"login")
        result, _, _ = await self.capture([{"url_list": [IMAGE]}], responses=[response])
        self.assertFalse(result["complete"])
        self.assertEqual(result["errors"][-1]["code"], "invalid_image_type")
        response.body.assert_not_awaited()

    async def test_oversized_response_is_rejected_before_body_access(self):
        response = image_response(headers={"content-type": "image/png", "content-length": str(MAX_IMAGE_BYTES + 1)})
        result, _, _ = await self.capture([{"url_list": [IMAGE]}], responses=[response])
        self.assertEqual(result["errors"][-1]["code"], "image_size")
        response.body.assert_not_awaited()

    async def test_unknown_length_remains_partial(self):
        response = image_response(headers={"content-type": "image/png"})
        result, _, _ = await self.capture([{"url_list": [IMAGE]}], responses=[response])
        self.assertEqual(result["errors"][-1]["code"], "image_size_unknown")
        self.assertFalse(result["complete"])

    async def test_redirect_outside_official_cdn_is_never_requested(self):
        response = image_response(status=302, headers={"location": "https://evil.invalid/private"})
        result, _, request = await self.capture([{"url_list": [IMAGE]}], responses=[response])
        self.assertEqual(result["errors"][-1]["code"], "unsafe_image_address")
        self.assertEqual(request.get.await_count, 1)
        self.assertEqual(request.get.await_args.kwargs["max_redirects"], 0)

    async def test_image_signature_must_match_content_type(self):
        response = image_response(data=b"not an image")
        result, _, _ = await self.capture([{"url_list": [IMAGE]}], responses=[response])
        self.assertEqual(result["errors"][-1]["code"], "invalid_image")

    async def test_invalid_work_and_destination_are_rejected_before_browser(self):
        with patch("creator_notes.photos.BrowserCollector") as browser:
            with self.assertRaises(ValueError):
                await capture_image_post(PROFILE, dict(WORK, author_sec_uid="foreign"), self.destination, self.session)
            with self.assertRaises(ValueError):
                await capture_image_post(PROFILE, WORK, Path(self.temp.name) / "outside", self.session)
            browser.assert_not_called()


if __name__ == "__main__":
    unittest.main()

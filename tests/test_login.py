"""Session-file and launcher contracts; no real browser or login is opened."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, call, patch

from creator_notes import login


class LoginTests(unittest.TestCase):
    def test_missing_status_does_not_claim_live_login(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = login.login_status(tmp)
        self.assertEqual("not_started", result["status"])
        self.assertFalse(result["storage_state_ready"])
        self.assertEqual("not_live_checked", result["session_verification"])

    def test_status_only_returns_allowlisted_fields_and_local_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "login-status.json").write_text(json.dumps({"status": "logged_in", "cookie": "DO_NOT_RETURN", "token": "secret", "qr_path": str(directory.parent / "outside.png"), "diagnostic_path": str(directory.parent / "outside.png")}), encoding="utf-8")
            (directory / "storage-state.json").write_text("{}", encoding="utf-8")
            result = login.login_status(directory)
            self.assertEqual("logged_in", result["status"])
            self.assertTrue(result["storage_state_ready"])
            self.assertNotIn("cookie", result)
            self.assertNotIn("token", result)
            self.assertNotIn("qr_path", result)
            self.assertNotIn("diagnostic_path", result)

    def test_dead_helper_hides_its_expired_qr(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            image = directory / "qr.png"
            image.write_bytes(b"test")
            login._write_status(directory, "awaiting_scan", qr_path=str(image))
            with patch.object(login, "_alive", return_value=False):
                result = login.login_status(directory)
        self.assertEqual("interrupted", result["status"])
        self.assertNotIn("qr_path", result)

    def test_logged_in_without_session_is_reported_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            login._write_status(Path(tmp), "logged_in")
            result = login.login_status(tmp)
        self.assertEqual("session_missing", result["status"])

    def test_account_endpoint_needs_real_user_identity(self):
        self.assertFalse(login._account_confirmed("/aweme/v1/web/user/profile/self/", {"status_code": 0, "user": {"uid": 0}}))
        self.assertFalse(login._account_confirmed("/passport/web/get_qrcode", {"status_code": 0, "user": {"uid": 123}}))
        self.assertTrue(login._account_confirmed("/aweme/v1/web/user/profile/self/", {"status_code": 0, "user": {"uid": 123}}))

    def test_launcher_uses_current_python_and_explicit_user_data_dir(self):
        with tempfile.TemporaryDirectory(prefix="\u4e2d\u6587 \u7a7a\u683c ") as tmp, \
             patch.object(login.subprocess, "Popen", return_value=SimpleNamespace(pid=4321)) as spawn:
            result = login.begin_login(tmp, timeout=900)
            args = spawn.call_args.args[0]
            self.assertEqual("starting", result["status"])
            self.assertIn("creator_notes.login", args)
            self.assertIn(str(Path(tmp).resolve()), args)
            self.assertIn("600", args)
            self.assertEqual(login.subprocess.DEVNULL, spawn.call_args.kwargs["stdout"])
            if os.name == "nt":
                self.assertTrue(spawn.call_args.kwargs["creationflags"] & login.subprocess.CREATE_NO_WINDOW)

    def test_active_login_is_reused_without_starting_another_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            login._write_status(Path(tmp), "starting")
            with patch.object(login, "_alive", return_value=True), patch.object(login.subprocess, "Popen") as spawn:
                result = login.begin_login(tmp)
            self.assertEqual("starting", result["status"])
            spawn.assert_not_called()

    def test_visible_helper_flag_is_explicit_and_uses_the_same_entrypoint(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(login.subprocess, "Popen", return_value=SimpleNamespace(pid=4321)) as spawn:
            result = login.begin_login(tmp, verification_window=True)
        self.assertIn("--verification-window", spawn.call_args.args[0])
        self.assertTrue(result["verification_window_open"])

    def test_verification_handoff_survives_a_finished_headless_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            login._write_status(Path(tmp), "verification_required", verification_window_open=False)
            with patch.object(login, "_alive", return_value=False):
                result = login.login_status(tmp)
                with patch.object(login.subprocess, "Popen", return_value=SimpleNamespace(pid=4321)) as spawn:
                    login.begin_login(tmp, verification_window=True)
            self.assertEqual("verification_required", result["status"])
            self.assertFalse(result["helper_running"])
            self.assertIn("--verification-window", spawn.call_args.args[0])

    def test_scanned_and_confirmed_qr_are_distinct(self):
        self.assertEqual("scanned", login._qr_phase({"data": {"status": "scanned"}}))
        self.assertEqual("confirmed", login._qr_phase({"data": {"status": "confirmed", "redirect_url": "https://www.douyin.com/"}}))
        self.assertIsNone(login._qr_phase({"data": {"status": "confirmed"}}))
        self.assertIsNone(login._qr_phase({"data": {"status": 2}}))

    def test_cookie_or_qr_alone_does_not_prove_complete_login(self):
        self.assertIsNone(login._session_proof(False, True, False, False))
        self.assertIsNone(login._session_proof(True, False, False, False))
        self.assertIsNone(login._session_proof(False, False, True, False))
        self.assertEqual("qr_confirmation_and_session", login._session_proof(False, True, True, False))

    def test_saved_pending_state_is_loaded_only_for_visible_handoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            saved = {"cookies": [{"name": "sessionid", "value": "saved-test-cookie"}], "origins": []}
            pending = {"cookies": [{"name": "sessionid", "value": "pending-test-cookie"}], "origins": []}
            (directory / "storage-state.json").write_text(json.dumps(saved), encoding="utf-8")
            (directory / "pending-login-state.json").write_text(json.dumps(pending), encoding="utf-8")
            self.assertEqual(saved, login._saved_state(directory, False))
            self.assertEqual(pending, login._saved_state(directory, True))


class LoginFlowTests(unittest.IsolatedAsyncioTestCase):
    def make_browser(self, cookies=None):
        node = SimpleNamespace(inner_text=AsyncMock(return_value="\u626b\u7801\u767b\u5f55"))
        absent = SimpleNamespace(count=AsyncMock(return_value=0))
        page = SimpleNamespace(locator=lambda _selector: node, get_by_text=lambda *_args, **_kwargs: absent,
            wait_for_timeout=AsyncMock(), receiver=None)
        page.on = lambda _event, receiver: setattr(page, "receiver", receiver)

        async def goto(*_args, **_kwargs):
            response = SimpleNamespace(url="https://www.douyin.com/aweme/v1/web/user/profile/self/",
                json=AsyncMock(return_value={"status_code": 0, "user": {"uid": "123"}}))
            page.receiver(response)
            await asyncio.sleep(0)

        page.goto = AsyncMock(side_effect=goto)
        context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
        context.cookies = AsyncMock(return_value=[{"name": "sessionid", "value": "test-session"}])
        if cookies is not None:
            context.cookies.side_effect = cookies

        async def save(path):
            Path(path).write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")

        context.storage_state = AsyncMock(side_effect=save)
        browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
        runtime = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))
        class RuntimeEntry:
            async def __aenter__(self):
                return runtime

            async def __aexit__(self, *_args):
                return None

        return RuntimeEntry(), runtime, browser, context, page

    async def test_visible_slider_waits_then_completes_in_one_browser_context(self):
        entry, runtime, browser, context, page = self.make_browser()
        with tempfile.TemporaryDirectory() as tmp, patch.object(login, "async_playwright", return_value=entry), \
             patch.object(login, "_challenge_visible", AsyncMock(side_effect=[False, True, False])), \
             patch.object(login, "_write_status", wraps=login._write_status) as states:
            await login.run_login(tmp, timeout=30, verification_window=True)
            values = [call.args[1] for call in states.call_args_list]
            self.assertIn("verification_required", values)
            self.assertEqual("logged_in", values[-1])
            self.assertTrue((Path(tmp) / "storage-state.json").is_file())
        runtime.chromium.launch.assert_awaited_once_with(headless=False, channel="chromium")
        browser.new_context.assert_awaited_once()
        context.new_page.assert_awaited_once()
        page.goto.assert_awaited_once()
        context.storage_state.assert_awaited_once()

    async def test_headless_slider_hands_off_without_claiming_logged_in(self):
        entry, runtime, _browser, _context, _page = self.make_browser()
        with tempfile.TemporaryDirectory() as tmp, patch.object(login, "async_playwright", return_value=entry), \
             patch.object(login, "_challenge_visible", AsyncMock(side_effect=[False, True])):
            await login.run_login(tmp, timeout=30, verification_window=False)
            result = json.loads((Path(tmp) / "login-status.json").read_text(encoding="utf-8"))
            self.assertEqual("verification_required", result["status"])
            self.assertFalse((Path(tmp) / "storage-state.json").exists())
            self.assertTrue((Path(tmp) / "pending-login-state.json").is_file())
        runtime.chromium.launch.assert_awaited_once_with(headless=True, channel="chromium")

    async def test_account_response_waits_for_cookie_without_replacing_qr_status(self):
        entry, _runtime, _browser, context, _page = self.make_browser(cookies=[[], [{"name": "sessionid", "value": "test-session"}]])
        with tempfile.TemporaryDirectory() as tmp, patch.object(login, "async_playwright", return_value=entry), \
             patch.object(login, "_challenge_visible", AsyncMock(return_value=False)), \
             patch.object(login, "_capture_qr", AsyncMock()) as capture, \
             patch.object(login, "_write_status", wraps=login._write_status) as states:
            await login.run_login(tmp, timeout=30, verification_window=True)
            values = [call.args[1] for call in states.call_args_list]
            self.assertIn("finalizing_login", values)
            self.assertEqual("logged_in", values[-1])
            capture.assert_not_awaited()
        self.assertEqual(2, context.cookies.await_count)

    async def test_real_qr_miss_routes_once_to_sso_in_the_same_context(self):
        entry, _runtime, browser, context, page = self.make_browser()
        original_navigation = page.goto.side_effect

        async def navigate(url, **kwargs):
            page.url = url
            if url == login.SSO_LOGIN_URL:
                await original_navigation(url, **kwargs)

        page.goto.side_effect = navigate
        with tempfile.TemporaryDirectory() as tmp, patch.object(login, "async_playwright", return_value=entry), \
             patch.object(login, "_challenge_visible", AsyncMock(return_value=False)), \
             patch.object(login, "_capture_qr", AsyncMock(side_effect=[False, True])), \
             patch.object(login, "_write_status", wraps=login._write_status) as states:
            await login.run_login(tmp, timeout=30, verification_window=True)
            values = [call.args[1] for call in states.call_args_list]
            self.assertIn("awaiting_scan", values)
            self.assertEqual("logged_in", values[-1])
        self.assertEqual(["https://www.douyin.com/", login.SSO_LOGIN_URL], [call.args[0] for call in page.goto.await_args_list])
        browser.new_context.assert_awaited_once()
        context.new_page.assert_awaited_once()


class QrDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def test_official_sso_redirect_is_allowlisted_without_trusting_other_pages(self):
        self.assertTrue(login._trusted_sso_page("https://www.douyin.com/login_page"))
        self.assertTrue(login._trusted_sso_page(login.SSO_LOGIN_URL))
        for url in ("http://www.douyin.com/login_page", "https://www.douyin.com/", "https://www.douyin.com/video/1234567890",
                    "https://www.douyin.com.evil.invalid/login_page", "https://user@www.douyin.com/login_page",
                    "https://www.douyin.com:1234/login_page", "https://www.douyin.com/login_page/other"):
            with self.subTest(url=url):
                self.assertFalse(login._trusted_sso_page(url))

    def page(self, url=login.SSO_LOGIN_URL, cue="\u626b\u7801\u767b\u5f55", images=None):
        body = SimpleNamespace(inner_text=AsyncMock(return_value=cue))
        absent = SimpleNamespace(count=AsyncMock(return_value=0))
        image_list = images or []
        data_images = SimpleNamespace(count=AsyncMock(return_value=len(image_list)), nth=lambda index: image_list[index])
        page = SimpleNamespace(url=url, goto=AsyncMock(), wait_for_timeout=AsyncMock(),
            get_by_text=lambda *_args, **_kwargs: absent)
        page.locator = lambda selector: body if selector == "body" else data_images if selector == 'canvas,svg,img' else absent
        return page

    def image(self, geometry=True, width=180, height=180):
        return SimpleNamespace(is_visible=AsyncMock(return_value=True),
            bounding_box=AsyncMock(return_value={"width": width, "height": height}),
            evaluate=AsyncMock(return_value=geometry), screenshot=AsyncMock(return_value=b"fixture-png"))

    async def test_obfuscated_data_image_is_only_captured_on_trusted_sso_scan_page(self):
        image = self.image()
        with patch.object(login, "_challenge_visible", AsyncMock(return_value=False)):
            found = await login._capture_qr(self.page(images=[image]), Path("qr.png"))
        self.assertTrue(found)
        image.screenshot.assert_has_awaits([call(type="png"), call(path="qr.png")])

    async def test_avatar_ambiguous_images_and_missing_cue_are_not_claimed_as_qr(self):
        cases = [(self.page(images=[self.image(geometry=False)])),
                 self.page(images=[self.image(), self.image()]),
                 self.page(cue="Account profile", images=[self.image()]),
                 self.page(url="https://www.douyin.com/", images=[self.image()]),
                 self.page(url="https://sso.douyin.com.evil.invalid/login/", images=[self.image()]),
                 self.page(images=[self.image(width=500, height=500)]),
                 self.page(images=[self.image(width=180, height=120)])]
        with patch.object(login, "_challenge_visible", AsyncMock(return_value=False)):
            for page in cases:
                with self.subTest(url=page.url):
                    self.assertFalse(await login._capture_qr(page, Path("not-a-qr.png")))

    async def test_captcha_blocks_qr_capture_and_sso_navigation(self):
        image = self.image()
        page = self.page(url="https://www.douyin.com/", images=[image])
        with patch.object(login, "_challenge_visible", AsyncMock(return_value=True)):
            self.assertFalse(await login._capture_qr(page, Path("blocked.png")))
            self.assertFalse(await login._open_login(page, sso_fallback=True))
        page.goto.assert_not_awaited()
        image.screenshot.assert_not_awaited()

    async def test_sso_fallback_uses_one_fixed_normal_login_destination(self):
        page = self.page(url="https://www.douyin.com/", cue="Public home page")
        with patch.object(login, "_challenge_visible", AsyncMock(return_value=False)):
            self.assertTrue(await login._open_login(page, sso_fallback=True))
        page.goto.assert_awaited_once_with(login.SSO_LOGIN_URL, wait_until="domcontentloaded", timeout=30000)

    async def test_sso_fallback_does_not_interrupt_scan_confirmation(self):
        page = self.page(url="https://www.douyin.com/", cue="\u626b\u7801\u6210\u529f")
        with patch.object(login, "_challenge_visible", AsyncMock(return_value=False)):
            self.assertFalse(await login._open_login(page, sso_fallback=True))
        page.goto.assert_not_awaited()

    async def test_diagnostic_image_is_saved_locally_without_html_or_cookie_export(self):
        page = SimpleNamespace(screenshot=AsyncMock())
        with tempfile.TemporaryDirectory() as tmp:
            path = await login._diagnostic_image(page, Path(tmp), "test-run")
            self.assertEqual(Path(tmp), Path(path).parent)
            self.assertEqual(".png", Path(path).suffix)
        page.screenshot.assert_awaited_once_with(path=path, full_page=False)

    def test_candidate_metadata_cannot_expose_image_sources_or_credentials(self):
        rows = [{"tag": "canvas", "rendered_width": 180.125, "rendered_height": 180,
                 "intrinsic_width": 360, "intrinsic_height": 360, "src": "secret-data", "cookie": "secret"},
                {"tag": "input", "value": "secret"}]
        result = login._safe_qr_metadata(rows)
        self.assertEqual([{"tag": "canvas", "rendered_width": 180.12, "rendered_height": 180,
                           "intrinsic_width": 360, "intrinsic_height": 360}], result)


@unittest.skipUnless(os.environ.get("CREATOR_NOTES_BROWSER_TESTS") == "1", "Set CREATOR_NOTES_BROWSER_TESTS=1 for isolated offline Chromium fixtures")
class QrPixelFixtureTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_qr_pixels_in_tiny_image_canvas_svg_and_non_qr_avatar(self):
        # Generated non-secret QR encodes "creator-notes-offline-fixture".
        # Its 25-module matrix plus a quiet border is only 33px intrinsically;
        # CSS scales it to 198px. This reproduces the naturalWidth regression.
        matrix = """1111111000001100101111111
1000001010100011001000001
1011101000000000101011101
1011101011101111001011101
1011101001010100101011101
1000001011001001001000001
1111111010101010101111111
0000000001111010100000000
1111101111000111010101010
1110000100110100100100000
0000111010100111110010011
1001010010000000110110000
1011111101100110011011101
1010110011010000110101010
1010001110101011111100011
1011010011000011101110010
1000101100000100111110101
0000000010110101100010000
1111111011001010101011011
1000001000001011100010011
1011101010110111111111110
1011101011110000111010111
1011101010000111000000101
1000001010001000111010001
1111111011111110011000111""".splitlines()
        async with login.async_playwright() as runtime:
            browser = await runtime.chromium.launch(headless=True, channel="chromium")
            try:
                context = await browser.new_context(device_scale_factor=1)

                async def offline(route):
                    await route.fulfill(status=200, content_type="text/html", body="<!doctype html><html><body></body></html>")

                # Every HTTP request is fulfilled locally; no site or account is used.
                await context.route("**/*", offline)
                page = await context.new_page()
                await page.goto("https://www.douyin.com/login_page")
                with tempfile.TemporaryDirectory() as tmp:
                    for kind in ("img", "canvas", "svg", "avatar"):
                        await page.set_content('<div>\u626b\u7801\u767b\u5f55</div><main id="fixture"></main>')
                        await page.evaluate("""async ({matrix,kind}) => {
                            const target=document.querySelector('#fixture');
                            let node;
                            if(kind==='svg'||kind==='avatar'){
                                node=document.createElementNS('http://www.w3.org/2000/svg','svg');
                                node.setAttribute('viewBox','0 0 33 33');
                                node.setAttribute('width','198');node.setAttribute('height','198');
                                node.innerHTML='<rect width="33" height="33" fill="white"/>';
                                if(kind==='avatar')node.innerHTML+='<circle cx="16.5" cy="16.5" r="13" fill="black"/>';
                                else matrix.forEach((row,y)=>[...row].forEach((bit,x)=>{
                                    if(bit==='1')node.innerHTML+=`<rect x="${x+4}" y="${y+4}" width="1" height="1" fill="black"/>`;
                                }));
                            } else {
                                const canvas=document.createElement('canvas');canvas.width=33;canvas.height=33;
                                const ctx=canvas.getContext('2d');ctx.fillStyle='white';ctx.fillRect(0,0,33,33);ctx.fillStyle='black';
                                matrix.forEach((row,y)=>[...row].forEach((bit,x)=>{if(bit==='1')ctx.fillRect(x+4,y+4,1,1);}));
                                if(kind==='img'){node=new Image();node.src=canvas.toDataURL();await node.decode();}
                                else node=canvas;
                                node.style.width='198px';node.style.height='198px';node.style.imageRendering='pixelated';
                            }
                            node.id='candidate';target.appendChild(node);
                        }""", {"matrix": matrix, "kind": kind})
                        node = page.locator("#candidate")
                        self.assertEqual(kind != "avatar", await login._looks_like_qr(node), kind)
                        destination = Path(tmp) / (kind + ".png")
                        self.assertEqual(kind != "avatar", await login._capture_qr(page, destination), kind)
                        self.assertEqual(kind != "avatar", destination.exists(), kind)
                await context.close()
            finally:
                await browser.close()


if __name__ == "__main__":
    unittest.main()

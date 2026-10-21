"""User-scanned login, isolated from all other browser profiles and credentials."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

from .collector import USER_AGENT, _challenge_visible


ACTIVE_STATES = {"starting", "awaiting_scan", "qr_refreshed", "qr_not_found", "qr_expired"}
STATUS_FIELDS = {"status", "pid", "updated_at", "run_id", "qr_path", "error_type", "proof", "expires_in_seconds"}


def _alive(pid):
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = wintypes.DWORD()
        try:
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _write_status(data_dir: Path, status: str, **extra):
    data_dir.mkdir(parents=True, exist_ok=True)
    value = {"status": status, "pid": os.getpid(), "updated_at": datetime.now(timezone.utc).isoformat(), **extra}
    value = {key: entry for key, entry in value.items() if key in STATUS_FIELDS}
    temporary = data_dir / ("login-status-" + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(data_dir / "login-status.json")
    return value


def login_status(data_dir):
    """Read saved status without opening a browser or returning credentials.

    A saved session is not a fresh proof that Douyin still accepts it. The next
    actual collection request determines whether reauthentication is required.
    """
    directory = Path(data_dir).expanduser().resolve()
    try:
        source = directory / "login-status.json"
        if source.is_symlink() or source.stat().st_size > 65536:
            raise ValueError("Invalid login status file")
        raw = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Invalid login status")
        result = {key: value for key, value in raw.items() if key in STATUS_FIELDS}
    except (OSError, ValueError, TypeError):
        result = {"status": "not_started"}
    image = result.get("qr_path")
    if image:
        try:
            path = Path(image).resolve()
            if path.parent != directory or not path.is_file() or path.suffix.lower() != ".png":
                raise ValueError("Invalid QR image")
        except (TypeError, OSError, ValueError):
            result.pop("qr_path", None)
    ready = (directory / "storage-state.json").is_file()
    result["storage_state_ready"] = ready
    result["session_verification"] = "not_live_checked"
    if result.get("status") in ACTIVE_STATES and not _alive(result.get("pid")):
        result["status"] = "interrupted"
        result.pop("qr_path", None)
    if result.get("status") == "logged_in" and not ready:
        result["status"] = "session_missing"
    return result


def begin_login(data_dir, timeout=600):
    """Launch a headless helper; the user must scan the returned local QR."""
    directory = Path(data_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    current = login_status(directory)
    if current.get("status") in ACTIVE_STATES:
        return current
    timeout = max(30, min(600, int(timeout)))
    run_id = uuid.uuid4().hex
    args = [sys.executable, "-m", "creator_notes.login", "--data-dir", str(directory),
            "--timeout", str(timeout), "--run-id", run_id]
    options = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    # Do not write a parent-PID starting status: the child writes its own first
    # status, avoiding a race that could overwrite a ready QR or success.
    process = subprocess.Popen(args, **options)
    return {"status": "starting", "pid": process.pid, "run_id": run_id,
            "storage_state_ready": (directory / "storage-state.json").is_file(),
            "message": "Poll login status; when the QR image is ready, scan it using your own Douyin mobile app."}


async def _capture_qr(page, destination: Path):
    for selector in (
        'img[alt*="\u4e8c\u7ef4\u7801"]', 'img[alt*="\u626b\u7801"]', '[class*="qrcode"] img',
        '[class*="qr-code"] img', '[class*="QRCode"] img', '[class*="qrCode"] img',
        '[class*="qrcode"] canvas', '[class*="qrCode"] canvas',
    ):
        nodes = page.locator(selector)
        for index in range(min(await nodes.count(), 20)):
            node = nodes.nth(index)
            if not await node.is_visible():
                continue
            box = await node.bounding_box()
            if box and box["width"] >= 90 and box["height"] >= 90:
                await node.screenshot(path=str(destination))
                return True
    return False


def _account_confirmed(path, payload):
    if not isinstance(payload, dict):
        return False
    if "/user/profile/self/" in path:
        user = payload.get("user")
        return payload.get("status_code") in (0, "0") and isinstance(user, dict) and str(user.get("uid") or "") not in ("", "0")
    if "/account/info" in path or "/auth/user_info" in path:
        info = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        return payload.get("status_code", 0) in (0, "0") and str(info.get("user_id") or info.get("uid") or "") not in ("", "0")
    return False


async def run_login(data_dir, timeout=600, run_id=None):
    directory = Path(data_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    run_id = run_id or uuid.uuid4().hex
    _write_status(directory, "starting", run_id=run_id)
    verified, qr_confirmed = False, False
    tasks = set()
    async with async_playwright() as playwright:
        browser = None
        context = None
        try:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(user_agent=USER_AGENT, locale="zh-CN",
                viewport={"width": 1280, "height": 900}, device_scale_factor=2)
            page = await context.new_page()

            async def inspect(response):
                nonlocal verified, qr_confirmed
                parsed = urlsplit(response.url)
                host = parsed.hostname or ""
                if host != "douyin.com" and not host.endswith(".douyin.com"):
                    return
                path = parsed.path
                if not any(part in path for part in ("/check_qrconnect", "/account/info", "/auth/user_info", "/user/profile/self/")):
                    return
                try:
                    payload = await response.json()
                    if not isinstance(payload, dict):
                        return
                    info = payload.get("data") if isinstance(payload.get("data"), dict) else payload
                    if "/check_qrconnect" in path:
                        qr_confirmed = info.get("status") in ("confirmed", "success", "3", 3) and bool(info.get("redirect_url"))
                    verified = verified or _account_confirmed(path, payload)
                except Exception:
                    return

            def receive(response):
                task = asyncio.create_task(inspect(response))
                tasks.add(task)
                task.add_done_callback(tasks.discard)

            page.on("response", receive)
            await page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)
            if "\u626b\u7801\u767b\u5f55" not in await page.locator("body").inner_text():
                buttons = page.get_by_text("\u767b\u5f55", exact=True)
                for index in range(min(await buttons.count(), 20)):
                    if await buttons.nth(index).is_visible():
                        await buttons.nth(index).click(timeout=5000)
                        break
            started, last_capture, last_refresh = time.monotonic(), 0.0, 0.0
            while time.monotonic() - started < max(30, min(600, int(timeout))):
                if await _challenge_visible(page):
                    _write_status(directory, "captcha", run_id=run_id)
                    return
                body = await page.locator("body").inner_text()
                cookies = await context.cookies("https://www.douyin.com/")
                has_session = any(item.get("name") in ("sessionid", "sessionid_ss", "sid_guard") and item.get("value") for item in cookies)
                login_buttons = page.get_by_text("\u767b\u5f55", exact=True)
                login_visible = False
                for index in range(min(await login_buttons.count(), 20)):
                    if await login_buttons.nth(index).is_visible():
                        login_visible = True
                        break
                confirmed = qr_confirmed and has_session and not login_visible
                logout_ready = has_session and any(text in body for text in ("\u9000\u51fa\u767b\u5f55", "\u9000\u51fa\u5f53\u524d\u8d26\u53f7"))
                if verified or confirmed or logout_ready:
                    temporary = directory / ("storage-state-" + run_id + ".tmp")
                    await context.storage_state(path=str(temporary))
                    temporary.replace(directory / "storage-state.json")
                    _write_status(directory, "logged_in", run_id=run_id,
                        proof="verified_account_response" if verified else "qr_confirmation_and_session" if confirmed else "logout_control_and_session")
                    return
                expired = any(text in body for text in ("\u4e8c\u7ef4\u7801\u5df2\u5931\u6548", "\u4e8c\u7ef4\u7801\u5df2\u8fc7\u671f", "\u70b9\u51fb\u5237\u65b0", "\u5237\u65b0\u4e8c\u7ef4\u7801"))
                if expired and time.monotonic() - last_refresh > 10:
                    for text in ("\u70b9\u51fb\u5237\u65b0", "\u5237\u65b0\u4e8c\u7ef4\u7801", "\u91cd\u65b0\u83b7\u53d6"):
                        controls = page.get_by_text(text, exact=False)
                        refreshed = False
                        for index in range(min(await controls.count(), 20)):
                            if await controls.nth(index).is_visible():
                                await controls.nth(index).click(timeout=3000)
                                refreshed = True
                                break
                        if refreshed:
                            break
                    last_refresh = time.monotonic()
                if time.monotonic() - last_capture > 15:
                    image = directory / ("login-qr-" + run_id + "-" + uuid.uuid4().hex[:8] + ".png")
                    if await _capture_qr(page, image):
                        _write_status(directory, "awaiting_scan", run_id=run_id, qr_path=str(image),
                            expires_in_seconds=max(0, int(timeout - (time.monotonic() - started))))
                    else:
                        _write_status(directory, "qr_not_found", run_id=run_id)
                    last_capture = time.monotonic()
                await page.wait_for_timeout(1500)
            _write_status(directory, "timed_out", run_id=run_id)
        except Exception as exc:
            _write_status(directory, "error", run_id=run_id, error_type=type(exc).__name__)
        finally:
            for task in tuple(tasks):
                task.cancel()
            if tasks:
                await asyncio.gather(*tuple(tasks), return_exceptions=True)
            if context:
                await context.close()
            if browser:
                await browser.close()


def main():
    parser = argparse.ArgumentParser(description="Prepare a user-scanned Douyin login in a dedicated local session")
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--run-id")
    arguments = parser.parse_args()
    asyncio.run(run_login(arguments.data_dir, arguments.timeout, arguments.run_id))


if __name__ == "__main__":
    main()

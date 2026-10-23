"""User-scanned login, isolated from all other browser profiles and credentials."""
from __future__ import annotations

import argparse
import asyncio
import base64
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


ACTIVE_STATES = {"starting", "awaiting_scan", "awaiting_phone_confirmation", "finalizing_login",
                 "verification_required", "qr_refreshed", "qr_not_found", "qr_expired"}
STATUS_FIELDS = {"status", "pid", "updated_at", "run_id", "qr_path", "error_type", "proof", "expires_in_seconds",
                 "message", "verification_window_open", "diagnostic_path", "qr_candidates"}
SSO_LOGIN_URL = "https://sso.douyin.com/login/?service=https%3A%2F%2Fwww.douyin.com&type=2&from=merge"


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
    if "qr_candidates" in result:
        result["qr_candidates"] = _safe_qr_metadata(result["qr_candidates"])
    for image_key in ("qr_path", "diagnostic_path"):
        image = result.get(image_key)
        if not image:
            continue
        try:
            path = Path(image).resolve()
            if path.parent != directory or not path.is_file() or path.suffix.lower() != ".png":
                raise ValueError("Invalid QR image")
        except (TypeError, OSError, ValueError):
            result.pop(image_key, None)
    ready = (directory / "storage-state.json").is_file()
    result["storage_state_ready"] = ready
    result["session_verification"] = "not_live_checked"
    result["helper_running"] = _alive(result.get("pid")) if result.get("status") in ACTIVE_STATES else False
    if result.get("status") in ACTIVE_STATES and not result["helper_running"]:
        if result["status"] != "verification_required":
            result["status"] = "interrupted"
        result.pop("qr_path", None)
    if result.get("status") == "logged_in" and not ready:
        result["status"] = "session_missing"
    return result


def begin_login(data_dir, timeout=600, verification_window=False):
    """Start dedicated login; visible mode supports manual slider verification.

    Start with verification_window=True to retain the very same browser context
    from QR scan through a possible slider. An explicit headless attempt cannot
    later reveal its existing window; a visible retry may require a fresh QR.
    """
    directory = Path(data_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    current = login_status(directory)
    if current.get("status") in ACTIVE_STATES and current.get("helper_running"):
        return current
    timeout = max(30, min(600, int(timeout)))
    run_id = uuid.uuid4().hex
    args = [sys.executable, "-m", "creator_notes.login", "--data-dir", str(directory),
            "--timeout", str(timeout), "--run-id", run_id]
    if verification_window:
        args.append("--verification-window")
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
            "verification_window_open": bool(verification_window),
            "message": "Poll login status. Scan the QR in your own Douyin app if requested; complete any slider yourself in the dedicated verification window."}


def _trusted_sso_page(url):
    """Accept only official login routes, including the normal SSO redirect."""
    try:
        parsed = urlsplit(url)
        route = (parsed.hostname, parsed.path.rstrip("/"))
        return parsed.scheme == "https" and route in {
            ("sso.douyin.com", "/login"), ("www.douyin.com", "/login_page"),
        } and not parsed.username and not parsed.password and parsed.port in (None, 443)
    except (TypeError, ValueError):
        return False


def _qr_box(box):
    return bool(box and 90 <= box.get("width", 0) <= 400 and 90 <= box.get("height", 0) <= 400
                and abs(box["width"] - box["height"]) <= 12)


async def _looks_like_qr(node):
    """Check finder-pattern geometry without decoding or returning QR contents."""
    try:
        # Rendered PNG pixels cover img/canvas/SVG alike, including QR matrices
        # whose intrinsic dimensions are tiny or missing. No QR is decoded.
        pixels = await node.screenshot(type="png")
        if not isinstance(pixels, bytes) or len(pixels) > 2 * 1024 * 1024:
            return False
        encoded = base64.b64encode(pixels).decode("ascii")
        return bool(await node.evaluate("""async (node,encoded) => {
            const raw=Uint8Array.from(atob(encoded),c=>c.charCodeAt(0));
            const image=await createImageBitmap(new Blob([raw],{type:'image/png'}));
            const w=image.width,h=image.height;
            if(w<90||h<90||w>1024||h>1024||Math.abs(w-h)>12)return false;
            const canvas=document.createElement('canvas');canvas.width=w;canvas.height=h;
            const ctx=canvas.getContext('2d',{willReadFrequently:true});ctx.drawImage(image,0,0);
            const p=ctx.getImageData(0,0,w,h).data;
            const dark=(x,y)=>{const i=(y*w+x)*4;return p[i+3]>128&&(p[i]+p[i+1]+p[i+2])<384;};
            const ratio=counts=>{const m=counts.reduce((a,b)=>a+b,0)/7;
                return m>=1&&counts.every((n,i)=>Math.abs(n-(i===2?3:1)*m)<=m*.8);};
            const centers=[];
            for(let y=0;y<h;y+=2){
                const runs=[];let start=0,color=dark(0,y);
                for(let x=1;x<=w;x++)if(x===w||dark(x,y)!==color){
                    runs.push({start,n:x-start,color});start=x;color=x<w?dark(x,y):false;
                }
                for(let k=0;k+4<runs.length;k++){
                    const r=runs.slice(k,k+5);if(!r[0].color||!ratio(r.map(v=>v.n)))continue;
                    const x=Math.floor(r[2].start+r[2].n/2);if(!dark(x,y))continue;
                    let top=y,bottom=y;while(top>0&&dark(x,top-1))top--;while(bottom+1<h&&dark(x,bottom+1))bottom++;
                    let a=top-1,b=bottom+1;while(a>=0&&!dark(x,a))a--;while(b<h&&!dark(x,b))b++;
                    let aa=a,bb=b;while(aa>=0&&dark(x,aa))aa--;while(bb<h&&dark(x,bb))bb++;
                    if(!ratio([a-aa,top-a-1,bottom-top+1,b-bottom-1,bb-b]))continue;
                    const cy=(top+bottom)/2,m=r.reduce((n,v)=>n+v.n,0)/7;
                    if(!centers.some(v=>Math.hypot(v.x-x,v.y-cy)<m*3))centers.push({x,y:cy});
                    if(centers.length>16)return false;
                }
            }
            for(let a=0;a<centers.length;a++)for(let b=0;b<centers.length;b++)for(let c=b+1;c<centers.length;c++){
                if(a===b||a===c)continue;
                const u={x:centers[b].x-centers[a].x,y:centers[b].y-centers[a].y};
                const v={x:centers[c].x-centers[a].x,y:centers[c].y-centers[a].y};
                const du=Math.hypot(u.x,u.y),dv=Math.hypot(v.x,v.y);
                if(du>w*.2&&dv>h*.2&&du/dv>.65&&du/dv<1.55&&Math.abs(u.x*v.x+u.y*v.y)/(du*dv)<.2)return true;
            }
            return false;
        }""", encoded))
    except Exception:
        return False


async def _capture_qr(page, destination: Path):
    if await _challenge_visible(page):
        return False
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
            if _qr_box(box):
                await node.screenshot(path=str(destination))
                return True
    # Some official SSO builds obfuscate every QR class. Accept rendered media
    # only on an allowlisted official login route, with a scan cue and QR geometry.
    if not _trusted_sso_page(getattr(page, "url", "")):
        return False
    body = await page.locator("body").inner_text()
    if not any(cue in body for cue in ("\u626b\u7801\u767b\u5f55", "\u626b\u63cf\u4e8c\u7ef4\u7801", "\u626b\u4e8c\u7ef4\u7801\u767b\u5f55")):
        return False
    images = page.locator('canvas,svg,img')
    candidates = []
    for index in range(min(await images.count(), 32)):
        image = images.nth(index)
        if await image.is_visible() and _qr_box(await image.bounding_box()) and await _looks_like_qr(image):
            candidates.append(image)
    if len(candidates) == 1:
        await candidates[0].screenshot(path=str(destination))
        return True
    return False


async def _open_login(page, *, sso_fallback=False):
    """Use ordinary login navigation; never accept dialogs or challenges."""
    if await _challenge_visible(page):
        return False
    body = await page.locator("body").inner_text()
    if sso_fallback:
        parsed = urlsplit(getattr(page, "url", ""))
        if parsed.scheme != "https" or parsed.hostname not in ("www.douyin.com", "douyin.com") or parsed.username or parsed.password or parsed.port not in (None, 443):
            return False
        if any(cue in body for cue in ("\u626b\u7801\u6210\u529f", "\u8bf7\u5728\u624b\u673a\u4e0a\u786e\u8ba4", "\u8bf7\u5728\u624b\u673a\u7aef\u786e\u8ba4")):
            return False
        await page.goto(SSO_LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(1500)
        return True
    if "\u626b\u7801\u767b\u5f55" not in body:
        buttons = page.get_by_text("\u767b\u5f55", exact=True)
        for index in range(min(await buttons.count(), 20)):
            if await buttons.nth(index).is_visible():
                await buttons.nth(index).click(timeout=5000)
                await page.wait_for_timeout(1500)
                return True
    return False


async def _diagnostic_image(page, directory, run_id):
    destination = directory / ("login-diagnostic-" + run_id + "-" + uuid.uuid4().hex[:8] + ".png")
    try:
        await page.screenshot(path=str(destination), full_page=False)
        return str(destination)
    except Exception:
        return None


def _safe_qr_metadata(rows):
    if not isinstance(rows, list):
        return []
    result = []
    for row in rows[:32]:
        if not isinstance(row, dict) or row.get("tag") not in ("img", "canvas", "svg"):
            continue
        item = {"tag": row["tag"]}
        for key in ("rendered_width", "rendered_height", "intrinsic_width", "intrinsic_height"):
            value = row.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 8192:
                item[key] = round(value, 2)
        result.append(item)
    return result


async def _qr_candidate_metadata(page):
    """Only element tags and sizes; never src, pixels, text, or credentials."""
    if not _trusted_sso_page(getattr(page, "url", "")):
        return []
    results = []
    try:
        nodes = page.locator("canvas,svg,img")
        for index in range(min(await nodes.count(), 32)):
            node = nodes.nth(index)
            if not await node.is_visible():
                continue
            row = await node.evaluate("""node => {
                const box=node.getBoundingClientRect();
                return {tag:node.tagName.toLowerCase(), rendered_width:box.width, rendered_height:box.height,
                    intrinsic_width:typeof node.naturalWidth==='number'?node.naturalWidth:typeof node.width==='number'?node.width:0,
                    intrinsic_height:typeof node.naturalHeight==='number'?node.naturalHeight:typeof node.height==='number'?node.height:0};
            }""")
            results.append(row)
    except Exception:
        pass
    return _safe_qr_metadata(results)


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


def _qr_phase(payload):
    if not isinstance(payload, dict):
        return None
    info = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    state = str(info.get("status", "")).strip().lower()
    if state in ("confirmed", "success", "3") and info.get("redirect_url"):
        return "confirmed"
    if state in ("scanned", "scan_success", "awaiting_confirmation", "pending_confirmation"):
        return "scanned"
    return None


def _session_proof(verified_account, qr_confirmed, has_session, logout_visible):
    """Never treat a QR response or a cookie alone as completed login."""
    if not has_session:
        return None
    if verified_account:
        return "verified_account_response_and_session"
    if qr_confirmed:
        return "qr_confirmation_and_session"
    if logout_visible:
        return "logout_control_and_session"
    return None


def _saved_state(directory, verification_window):
    names = ("pending-login-state.json", "storage-state.json") if verification_window else ("storage-state.json",)
    for name in names:
        source = directory / name
        if source.is_file() and not source.is_symlink() and source.stat().st_size <= 16 * 1024 * 1024:
            try:
                value = json.loads(source.read_text(encoding="utf-8"))
                if isinstance(value, dict) and isinstance(value.get("cookies"), list) and isinstance(value.get("origins"), list):
                    return value
            except (OSError, ValueError):
                pass
    return None


async def _store_state(context, destination):
    temporary = destination.with_name(destination.name + "-" + uuid.uuid4().hex + ".tmp")
    await context.storage_state(path=str(temporary))
    temporary.replace(destination)


async def _visible_text(page, text):
    nodes = page.get_by_text(text, exact=True)
    for index in range(min(await nodes.count(), 20)):
        if await nodes.nth(index).is_visible():
            return True
    return False


async def run_login(data_dir, timeout=600, run_id=None, verification_window=False):
    directory = Path(data_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    run_id = run_id or uuid.uuid4().hex
    def status(value, **extra):
        return _write_status(directory, value, run_id=run_id,
                             verification_window_open=bool(verification_window), **extra)

    status("starting")
    verified, qr_scanned, qr_confirmed = False, False, False
    tasks = set()
    async with async_playwright() as playwright:
        browser = None
        context = None
        try:
            browser = await playwright.chromium.launch(headless=not verification_window, channel="chromium")
            options = {"user_agent": USER_AGENT, "locale": "zh-CN",
                "viewport": {"width": 1280, "height": 900}, "device_scale_factor": 2}
            saved = _saved_state(directory, verification_window)
            if saved is not None:
                options["storage_state"] = saved
            context = await browser.new_context(**options)
            page = await context.new_page()

            async def inspect(response):
                nonlocal verified, qr_scanned, qr_confirmed
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
                    if "/check_qrconnect" in path:
                        phase = _qr_phase(payload)
                        qr_scanned = qr_scanned or phase in ("scanned", "confirmed")
                        qr_confirmed = qr_confirmed or phase == "confirmed"
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
            await _open_login(page)
            started, last_capture, last_refresh = time.monotonic(), 0.0, 0.0
            sso_attempted = False
            while time.monotonic() - started < max(30, min(600, int(timeout))):
                if await _challenge_visible(page):
                    if verification_window:
                        status("verification_required", message="Complete the slider yourself in the open dedicated browser. This helper will wait and continue in the same context; do not rescan unless Douyin explicitly asks.")
                        await page.wait_for_timeout(1500)
                        continue
                    await _store_state(context, directory / "pending-login-state.json")
                    status("verification_required", message="The headless browser requires manual slider verification. Start login with verification_window=true to open a dedicated browser using saved pending state; Douyin may require a fresh QR.")
                    return
                body = await page.locator("body").inner_text()
                cookies = await context.cookies("https://www.douyin.com/")
                has_session = any(item.get("name") in ("sessionid", "sessionid_ss", "sid_guard") and item.get("value") for item in cookies)
                logout_visible = any([await _visible_text(page, text) for text in ("\u9000\u51fa\u767b\u5f55", "\u9000\u51fa\u5f53\u524d\u8d26\u53f7")])
                proof = _session_proof(verified, qr_confirmed, has_session, logout_visible)
                if proof:
                    await _store_state(context, directory / "storage-state.json")
                    (directory / "pending-login-state.json").unlink(missing_ok=True)
                    status("logged_in", proof=proof)
                    return
                if verified or qr_confirmed:
                    status("finalizing_login", message="Login approval was received; waiting for the dedicated browser session. The helper continues automatically.")
                elif qr_scanned or any(text in body for text in ("\u626b\u7801\u6210\u529f", "\u8bf7\u5728\u624b\u673a\u4e0a\u786e\u8ba4", "\u8bf7\u5728\u624b\u673a\u7aef\u786e\u8ba4")):
                    qr_scanned = True
                    status("awaiting_phone_confirmation", message="The QR was scanned. Confirm login in your own Douyin mobile app if it asks; the helper will continue automatically.")
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
                            qr_scanned = qr_confirmed = False
                            last_capture = 0.0
                            break
                    last_refresh = time.monotonic()
                if not (verified or qr_scanned or qr_confirmed) and time.monotonic() - last_capture > 15:
                    image = directory / ("login-qr-" + run_id + "-" + uuid.uuid4().hex[:8] + ".png")
                    captured = await _capture_qr(page, image)
                    if not captured and not sso_attempted and not (verified or qr_scanned or qr_confirmed):
                        sso_attempted = True
                        if await _open_login(page, sso_fallback=True):
                            captured = await _capture_qr(page, image)
                    if captured:
                        status("awaiting_scan", qr_path=str(image),
                            expires_in_seconds=max(0, int(timeout - (time.monotonic() - started))))
                    else:
                        diagnostic = await _diagnostic_image(page, directory, run_id)
                        status("qr_not_found", diagnostic_path=diagnostic,
                               qr_candidates=await _qr_candidate_metadata(page),
                               message="No login QR was identified. A local diagnostic image is available when screenshot capture succeeds; no challenge or agreement was accepted automatically.")
                    last_capture = time.monotonic()
                await page.wait_for_timeout(1500)
            status("timed_out", message="Login or manual verification did not finish within the time limit. No CAPTCHA, permission, security, or agreement prompt was accepted automatically.")
        except Exception as exc:
            status("error", error_type=type(exc).__name__)
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
    parser.add_argument("--verification-window", action="store_true", help="Open a dedicated visible login browser so the user can complete a slider manually")
    arguments = parser.parse_args()
    asyncio.run(run_login(arguments.data_dir, arguments.timeout, arguments.run_id, arguments.verification_window))


if __name__ == "__main__":
    main()

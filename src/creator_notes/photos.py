"""Archive published image posts through their ordinary official web page.

The returned manifest contains public post links and owned local paths only.
Observed image addresses are used in memory and are never serialized.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urljoin, urlsplit
import uuid

from .collector import BrowserCollector, CollectorError, _challenge_visible
from .paths import AppPaths


MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_IMAGES = 100
IMAGE_HOSTS = ("douyin.com", "byteimg.com", "ibytedtos.com", "douyinpic.com")
MIME_SUFFIX = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _allowed_image_url(value):
    if not isinstance(value, str) or len(value) > 16384:
        return False
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        return (parsed.scheme == "https" and not parsed.username and not parsed.password
                and parsed.port in (None, 443) and not parsed.fragment
                and any(host == suffix or host.endswith("." + suffix) for suffix in IMAGE_HOSTS))
    except ValueError:
        return False


def _image_slots(detail):
    rows = detail.get("images")
    if not isinstance(rows, list):
        return []
    result = []
    for row in rows:
        urls = []
        if isinstance(row, dict):
            candidates = [row.get("url_list"), (row.get("display_image") or {}).get("url_list")
                          if isinstance(row.get("display_image"), dict) else None]
            for values in candidates:
                if isinstance(values, list):
                    urls.extend(value for value in values if _allowed_image_url(value))
        result.append(list(dict.fromkeys(urls))[:5])
    return result


def _safe_destination(destination, session_dir):
    raw = Path(destination).expanduser().absolute()
    reports = AppPaths.current().reports.resolve()
    resolved = raw.resolve()
    if (not resolved.is_relative_to(reports)
            or resolved.is_relative_to(Path(session_dir).expanduser().resolve())):
        raise ValueError("Image reports must be inside the current reports directory and outside session state")
    for path in (raw, *raw.parents):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("Image report paths must not traverse symbolic links or junctions")
        if path == reports:
            break
    raw.mkdir(parents=True, exist_ok=True)
    folder = raw / (datetime.now(timezone.utc).strftime("capture-%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8])
    folder.mkdir(exist_ok=False)
    return folder


def _valid_image(data, mime):
    if not 0 < len(data) <= MAX_IMAGE_BYTES:
        return False
    if mime == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9")
    return mime == "image/webp" and len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"


async def _download_image(request, url, folder, index):
    current = url
    for hop in range(4):
        if not _allowed_image_url(current):
            return None, "unsafe_image_address"
        response = None
        try:
            response = await request.get(current, timeout=30000, max_redirects=0,
                                         headers={"Referer": "https://www.douyin.com/"})
            if not _allowed_image_url(response.url):
                return None, "unsafe_image_redirect"
            if response.status in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if not location or hop == 3:
                    return None, "image_redirect"
                current = urljoin(current, location)
                continue
            if response.status != 200:
                return None, "image_unavailable"
            mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if mime not in MIME_SUFFIX:
                return None, "invalid_image_type"
            length = response.headers.get("content-length")
            if length is None:
                return None, "image_size_unknown"
            try:
                if not 0 < int(length) <= MAX_IMAGE_BYTES:
                    return None, "image_size"
            except (ValueError, TypeError):
                return None, "image_size"
            data = await response.body()
            if len(data) != int(length) or not _valid_image(data, mime):
                return None, "invalid_image"
            path = folder / f"image-{index + 1:03d}{MIME_SUFFIX[mime]}"
            with path.open("xb") as stream:
                stream.write(data)
            return str(path.resolve()), None
        except Exception:
            return None, "image_download_failed"
        finally:
            if response is not None:
                await response.dispose()
    return None, "image_redirect"


async def capture_image_post(profile, work, destination, session_dir):
    """Return real local image assets and a page capture, without claiming speech."""
    sec_uid = profile.get("sec_uid") if isinstance(profile, dict) else None
    video_id = str(work.get("video_id", "")) if isinstance(work, dict) else ""
    if (not isinstance(sec_uid, str) or not re.fullmatch(r"[A-Za-z0-9_=-]+", sec_uid)
            or not re.fullmatch(r"[0-9]{5,30}", video_id) or work.get("kind") != "image"
            or work.get("author_sec_uid") not in (None, "", sec_uid)
            or work.get("url") != f"https://www.douyin.com/video/{video_id}"):
        raise ValueError("Provide an exact verified creator and canonical public image post")
    folder = _safe_destination(destination, session_dir)
    result = {"status": "running", "image_count": 0, "images": [], "screenshot_path": None,
              "post_url": work["url"], "complete": False, "captured_at": _now(),
              "errors": [], "content_kind": "image_post", "speech_transcribed": False}
    details = []
    flags = set()
    tasks = set()

    async def inspect(response):
        parsed = urlsplit(response.url)
        host = parsed.hostname or ""
        if (not (host == "douyin.com" or host.endswith(".douyin.com"))
                or not parsed.path.rstrip("/").endswith("/aweme/detail") or not response.ok):
            return
        requested = parse_qs(parsed.query).get("aweme_id", [])
        if requested and requested != [video_id]:
            return
        try:
            payload = await response.json()
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        if payload.get("verify_check") or payload.get("verify_data"):
            flags.add("captcha")
        message = " ".join(str(payload.get(key, "")) for key in ("status_msg", "message", "msg"))
        if re.search(r"\u767b\u5f55|login|passport", message, re.I):
            flags.add("login_required")
        if payload.get("status_code") not in (None, 0, "0"):
            return
        detail = payload.get("aweme_detail")
        if not isinstance(detail, dict) or str(detail.get("aweme_id", "")) != video_id:
            return
        author = detail.get("author")
        author = author if isinstance(author, dict) else {}
        author_id = author.get("sec_uid") or author.get("sec_user_id") or ""
        if author_id != sec_uid and not (not author_id and requested == [video_id]
                                         and work.get("author_sec_uid") == sec_uid):
            return
        slots = _image_slots(detail)
        if slots:
            details.append(slots)

    def receive(response):
        task = asyncio.create_task(inspect(response))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    try:
        async with BrowserCollector(data_dir=session_dir) as collector:
            await collector._warm_up()
            page = await collector._context.new_page()
            page.on("response", receive)
            try:
                try:
                    await page.goto(work["url"], wait_until="domcontentloaded", timeout=45000)
                except Exception:
                    pass
                deadline = asyncio.get_running_loop().time() + 30
                while not details and not flags and asyncio.get_running_loop().time() < deadline:
                    if await _challenge_visible(page):
                        flags.add("captcha")
                        break
                    await asyncio.sleep(0.5)
                if tasks:
                    await asyncio.wait(tuple(tasks), timeout=3)
                body = (await page.locator("body").inner_text(timeout=5000))[:16000]
                if (await _challenge_visible(page) or re.search(
                        r"\u62d6\u52a8\u6ed1\u5757|\u8bf7\u5b8c\u6210\u5b89\u5168\u9a8c\u8bc1|\u70b9\u51fb\u5b8c\u6210\u9a8c\u8bc1", body)):
                    flags.add("captcha")
                if re.search(r"\u767b\u5f55\u540e(?:\u5373\u53ef)?(?:\u67e5\u770b|\u89c2\u770b)|\u8bf7\u5148\u767b\u5f55", body):
                    flags.add("login_required")
                if flags:
                    result["status"] = "captcha" if "captcha" in flags else "login_required"
                    return result
                if not details:
                    result["status"] = "no_data"
                    return result
                slots = details[-1]
                result["image_count"] = len(slots)
                try:
                    screenshot = folder / "post-page.png"
                    await page.screenshot(path=str(screenshot), full_page=True, timeout=30000)
                    result["screenshot_path"] = str(screenshot.resolve())
                except Exception:
                    result["errors"].append({"code": "screenshot_failed"})
                if len(slots) > MAX_IMAGES:
                    result["errors"].append({"code": "image_limit"})
                for index, urls in enumerate(slots[:MAX_IMAGES]):
                    saved = None
                    error = "image_address_missing"
                    for url in urls:
                        saved, error = await _download_image(collector._context.request, url, folder, index)
                        if saved:
                            result["images"].append(saved)
                            break
                    if not saved:
                        result["errors"].append({"index": index, "code": error})
                result["complete"] = bool(result["screenshot_path"] and not result["errors"]
                                          and len(result["images"]) == len(slots))
                result["status"] = "done" if result["complete"] else "partial"
                return result
            finally:
                page.remove_listener("response", receive)
                for task in tuple(tasks):
                    task.cancel()
                if tasks:
                    await asyncio.gather(*tuple(tasks), return_exceptions=True)
                await page.close()
    except CollectorError as exc:
        result["status"] = exc.code if exc.code in ("captcha", "login_required") else "error"
        result["errors"].append({"code": exc.code if re.fullmatch(r"[a-z_]+", exc.code) else "capture_failed"})
        return result
    except Exception:
        result["status"] = "error"
        result["errors"].append({"code": "capture_failed"})
        return result
    finally:
        result["captured_at"] = _now()
        (folder / "photos.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

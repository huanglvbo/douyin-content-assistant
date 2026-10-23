"""Read public creator pages through normal Playwright navigation.

Only a dedicated, user-created storage-state file is ever loaded.  This module
does not extract browser cookies, replay signed APIs, or solve access challenges.
Returned objects contain an allowlist of public creator and video fields.
"""
from __future__ import annotations

import asyncio
import json
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from playwright.async_api import async_playwright

from .paths import AppPaths


PROFILE_PATH = re.compile(r"^/user/([A-Za-z0-9_=-]+)/*$")
SEC_UID = re.compile(r"^MS4wLjAB[A-Za-z0-9_=-]+$")
PUBLIC_HOSTS = {"www.douyin.com", "douyin.com"}
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class CollectorError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return str(value).strip() if isinstance(value, (str, int)) and not isinstance(value, bool) else ""


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = int(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return result if result >= 0 else None


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip().casefold()
    value = re.sub(r"^(?:\u6296\u97f3\u53f7|\u6296\u97f3id|douyin\s*id)\s*[:\uff1a]?\s*", "", value)
    return re.sub(r"\s+", " ", value.lstrip("@"))


def _public_profile(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    sec_uid = _text(value.get("sec_uid") or value.get("sec_user_id"))
    nickname = _text(value.get("nickname"))
    if not sec_uid or not nickname:
        return None
    return {
        "sec_uid": sec_uid,
        "nickname": nickname,
        "unique_id": _text(value.get("unique_id")),
        "short_id": _text(value.get("short_id")),
        "uid": _text(value.get("uid")),
        "profile_url": f"https://www.douyin.com/user/{quote(sec_uid, safe='=_-')}",
        "signature": _text(value.get("signature")),
    }


def extract_profiles(payload: Any) -> list[dict[str, Any]]:
    """Extract public profile fields; no raw payload or credentials escape."""
    found: dict[str, dict[str, Any]] = {}
    stack = [payload]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            profile = _public_profile(value)
            if profile:
                previous = found.get(profile["sec_uid"], {})
                found[profile["sec_uid"]] = {
                    key: profile.get(key) or previous.get(key, "") for key in profile
                }
            stack.extend(child for child in value.values() if isinstance(child, (dict, list)))
        elif isinstance(value, list):
            stack.extend(value)
    return list(found.values())


def extract_search_profiles(payload: Any) -> list[dict[str, Any]]:
    """Only accepted, explicit account-search results can resolve a name."""
    if not isinstance(payload, dict) or payload.get("status_code") not in (None, 0, "0"):
        return []
    rows = payload.get("user_list")
    if not isinstance(rows, list):
        return []
    profiles: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        public = _public_profile(row.get("user_info"))
        if public:
            profiles[public["sec_uid"]] = public
    return list(profiles.values())


def _public_video(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    video_id = _text(value.get("aweme_id"))
    if not video_id.isdigit():
        return None
    # Statistics and permission objects can repeat aweme_id.  They are not
    # works and must never overwrite the containing work's date and counters.
    if not any(key in value for key in ("create_time", "desc", "title", "video", "images", "aweme_type")):
        return None
    statistics = value.get("statistics")
    statistics = statistics if isinstance(statistics, dict) else {}
    created = _integer(value.get("create_time"))
    if created and created > 10_000_000_000:
        created //= 1000
    try:
        published = datetime.fromtimestamp(created, timezone.utc).isoformat() if created else None
    except (ValueError, OverflowError, OSError):
        created, published = None, None
    video = value.get("video")
    video = video if isinstance(video, dict) else {}
    duration = _integer(value.get("duration"))
    if duration is None:
        duration = _integer(video.get("duration"))
    aweme_type = _integer(value.get("aweme_type"))
    is_image = bool(value.get("images")) or aweme_type in {68, 150}
    return {
        "video_id": video_id,
        "title": _text(value.get("desc") or value.get("title")),
        "create_time": created,
        "published_at": published,
        "url": f"https://www.douyin.com/video/{video_id}",
        "duration_seconds": duration / 1000 if duration is not None else None,
        "statistics": {
            "like": _integer(statistics.get("digg_count")),
            "comment": _integer(statistics.get("comment_count")),
            "collect": _integer(statistics.get("collect_count"))
            if _integer(statistics.get("collect_count")) is not None
            else _integer(statistics.get("collect_count_v2")),
            "share": _integer(statistics.get("share_count")),
        },
        "pinned": bool(value.get("is_top") or value.get("is_pinned") or value.get("top")),
        "kind": "image" if is_image else "video",
        "aweme_type": aweme_type,
        "author_sec_uid": _text((value.get("author") or {}).get("sec_uid") or (value.get("author") or {}).get("sec_user_id"))
        if isinstance(value.get("author"), dict) else "",
    }


def extract_videos(
    payload: Any, sec_uid: str = "", *, allow_missing_author: bool = False,
) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    stack = [payload]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            video = _public_video(value)
            author_matches = video and (
                not sec_uid or video["author_sec_uid"] == sec_uid
                or (allow_missing_author and not video["author_sec_uid"])
            )
            if video and author_matches:
                found[video["video_id"]] = video
            stack.extend(child for child in value.values() if isinstance(child, (dict, list)))
        elif isinstance(value, list):
            stack.extend(value)
    return list(found.values())


def select_creator(query: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Resolve exact visible ID/name only; never silently choose a fuzzy result."""
    wanted = _normalized(query)
    candidates = list({p["sec_uid"]: p for p in candidates}.values())
    id_matches = [p for p in candidates if wanted in {
        _normalized(p.get("unique_id", "")), _normalized(p.get("short_id", "")),
        _normalized(p.get("sec_uid", "")),
        _normalized(p.get("uid", "")),
    } - {""}]
    exact = id_matches or [p for p in candidates if _normalized(p["nickname"]) == wanted]
    if len(exact) == 1:
        return {"status": "resolved", "profile": exact[0], "matched_by": "id" if id_matches else "nickname"}
    return {
        "status": "ambiguous",
        "reason": "duplicate_exact_match" if exact else "no_exact_match",
        "candidates": (exact or candidates)[:20],
        "message": "Multiple accounts have this exact name; select a candidate ID or profile." if exact else "No exact match was found; confirm an account from the candidates.",
    }


async def _challenge_visible(page: Any) -> bool:
    """Inspect all challenge containers; a hidden first node is not decisive."""
    try:
        nodes = page.locator("[id*='captcha'],[class*='captcha-verify'],[class*='verify-wrap']")
        for index in range(min(await nodes.count(), 20)):
            if await nodes.nth(index).is_visible():
                return True
    except Exception:
        pass
    return False


class BrowserCollector:
    def __init__(
        self,
        data_dir: str | Path | None = None,
        *,
        headless: bool = True,
        timeout_seconds: float = 35,
    ):
        self.data_dir = Path(data_dir).expanduser().resolve() if data_dir is not None else AppPaths.current().session
        self.state_path = self.data_dir / "storage-state.json"
        self.headless = headless
        self.timeout_seconds = timeout_seconds
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._warmed_up = False

    async def __aenter__(self) -> BrowserCollector:
        await self._start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def _start(self) -> None:
        if self._context is not None:
            return
        self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.launch(channel='chromium', headless=self.headless)
            options: dict[str, Any] = {
                "locale": "zh-CN", "viewport": {"width": 1440, "height": 1080},
                "user_agent": USER_AGENT,
            }
            if self.state_path.is_file():
                options["storage_state"] = str(self.state_path)
            self._context = await self._browser.new_context(**options)
        except Exception as exc:
            await self.close()
            raise CollectorError("browser_start_failed", "The dedicated collection browser could not start.", {"type": type(exc).__name__}) from exc

    async def close(self) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._context = self._browser = self._playwright = None
        self._warmed_up = False

    async def _warm_up(self) -> None:
        if self._warmed_up:
            return
        await self._start()
        warm_page = await self._context.new_page()
        try:
            await warm_page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(3)
        except Exception:
            pass
        finally:
            await warm_page.close()
        self._warmed_up = True

    async def _read_page(
        self, url: str, *, mode: str, sec_uid: str = "", video_id: str = "",
    ) -> dict[str, Any]:
        await self._start()
        await self._warm_up()
        page = await self._context.new_page()
        page.set_default_navigation_timeout(45_000)
        profiles: dict[str, dict[str, Any]] = {}
        videos: dict[str, dict[str, Any]] = {}
        post_pages: list[dict[str, Any]] = []
        tasks: set[asyncio.Task[Any]] = set()
        response_flags: set[str] = set()
        search_responses = 0

        async def inspect(response: Any) -> None:
            nonlocal search_responses
            parsed = urlparse(response.url)
            if not (parsed.hostname == "douyin.com" or (parsed.hostname or "").endswith(".douyin.com")):
                return
            path = parsed.path
            is_search = path.rstrip("/").endswith("/discover/search")
            is_posts = path.rstrip("/").endswith("/aweme/post")
            is_profile = path.rstrip("/").endswith("/user/profile/other")
            is_detail = path.rstrip("/").endswith("/aweme/detail")
            relevant = ((mode == "search" and is_search)
                        or (mode == "profile" and (is_posts or is_profile))
                        or (mode == "video" and is_detail))
            if not relevant or not response.ok:
                return
            requested_user = parse_qs(parsed.query).get("sec_user_id", [])
            requested_video = parse_qs(parsed.query).get("aweme_id", [])
            if is_posts and sec_uid and requested_user and requested_user != [sec_uid]:
                return
            if is_detail and video_id and requested_video and requested_video != [video_id]:
                return
            try:
                payload = await response.json()
            except Exception:
                return
            if not isinstance(payload, dict):
                return
            if payload.get("verify_check") or payload.get("verify_data"):
                response_flags.add("captcha")
            message = " ".join(_text(payload.get(k)) for k in ("status_msg", "message", "msg"))
            if re.search(r"\u767b\u5f55|login|passport", message, re.I):
                response_flags.add("login_required")
            accepted = payload.get("status_code") in (None, 0, "0")
            if not accepted:
                return
            if mode == "search":
                source_profiles = extract_search_profiles(payload) if is_search else []
            else:
                source_profiles = extract_profiles(payload)
            for profile in source_profiles:
                old = profiles.get(profile["sec_uid"], {})
                profiles[profile["sec_uid"]] = {key: profile[key] or old.get(key, "") for key in profile}
            if is_search:
                search_responses += 1
            if is_posts or is_detail:
                # Author-less rows are trustworthy only within a response whose
                # actual request explicitly targets this creator's sec_user_id.
                page_videos = extract_videos(
                    payload, sec_uid,
                    allow_missing_author=bool(
                        (is_posts and sec_uid and requested_user == [sec_uid])
                        or (is_detail and video_id and requested_video == [video_id])
                    ),
                )
                if video_id:
                    page_videos = [v for v in page_videos if v["video_id"] == video_id]
                if page_videos:
                    signature = tuple(sorted(v["video_id"] for v in page_videos))
                    if is_posts and not any(p["signature"] == signature for p in post_pages):
                        post_pages.append({
                            "signature": signature,
                            "items": len(page_videos),
                            "has_more": payload.get("has_more"),
                        })
                    videos.update({v["video_id"]: v for v in page_videos})

        def receive(response: Any) -> None:
            task = asyncio.create_task(inspect(response))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

        page.on("response", receive)
        navigated = False
        try:
            try:
                await page.goto(url, wait_until="domcontentloaded")
                navigated = True
            except Exception:
                # A streaming resource can time out while a valid document is visible.
                pass
            deadline = asyncio.get_running_loop().time() + self.timeout_seconds
            reload_at = asyncio.get_running_loop().time() + 7
            reloaded = False
            while asyncio.get_running_loop().time() < deadline:
                if (mode == "search" and profiles and search_responses) or (mode == "profile" and videos):
                    break
                if "captcha" in response_flags:
                    break
                if await _challenge_visible(page):
                    response_flags.add("captcha")
                    break
                if not reloaded and asyncio.get_running_loop().time() >= reload_at:
                    reloaded = True
                    try:
                        await page.reload(wait_until="domcontentloaded", timeout=30_000)
                    except Exception:
                        pass
                await asyncio.sleep(0.5)
            if tasks:
                await asyncio.wait(tuple(tasks), timeout=3)
            # Read only JSON hydration blocks, including percent-encoded RENDER_DATA.
            for raw in await page.locator("script[type='application/json'],script#RENDER_DATA,script#__NEXT_DATA__").all_text_contents():
                if len(raw) > 8_000_000:
                    continue
                for candidate in (raw, unquote(raw)):
                    try:
                        payload = json.loads(candidate)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if mode in {"profile", "video"}:
                        for profile in extract_profiles(payload):
                            profiles.setdefault(profile["sec_uid"], profile)
                        for video in extract_videos(payload, sec_uid):
                            if video_id and video["video_id"] != video_id:
                                continue
                            videos.setdefault(video["video_id"], video)
                    break
            scrolled = False
            if mode == "profile" and videos:
                await page.mouse.wheel(0, 1800)
                scrolled = True
                await asyncio.sleep(3)
                if tasks:
                    await asyncio.wait(tuple(tasks), timeout=3)
            body = (await page.locator("body").inner_text(timeout=5000))[:16000]
            captcha_visible = await _challenge_visible(page)
            challenge = captcha_visible or bool(re.search(r"\u8bf7\u5b8c\u6210\u4e0b\u65b9\u9a8c\u8bc1|\u62d6\u52a8\u6ed1\u5757|\u8bf7\u5b8c\u6210\u5b89\u5168\u9a8c\u8bc1|\u70b9\u51fb\u5b8c\u6210\u9a8c\u8bc1", body))
            if challenge or "captcha" in response_flags:
                raise CollectorError("captcha", "Douyin requires manual verification; collection has stopped.", {"page_url": url})
            has_data = bool(profiles) if mode == "search" else bool(videos)
            if not has_data:
                if "login_required" in response_flags or re.search(r"\u767b\u5f55\u540e(?:\u5373\u53ef)?(?:\u641c\u7d22|\u67e5\u770b|\u89c2\u770b)|\u8bf7\u5148\u767b\u5f55|\u767b\u5f55\u5373\u53ef(?:\u67e5\u770b|\u641c\u7d22)", body):
                    raise CollectorError("login_required", "Douyin requires login to read this page; sign in normally using the dedicated browser.", {"page_url": url})
                raise CollectorError("no_data", "The page did not return verifiable public account or work data.", {"page_url": url, "document_loaded": navigated})
            return {
                "profiles": list(profiles.values()),
                "videos": list(videos.values()),
                "coverage": {
                    "pages_captured": len(post_pages),
                    "one_scroll_attempted": scrolled,
                    "has_more": post_pages[-1]["has_more"] if post_pages else None,
                    "selection": "maximum create_time among captured public profile entries, including pinned entries",
                    "all_history_scanned": False,
                },
            }
        except CollectorError:
            raise
        except Exception as exc:
            raise CollectorError("no_data", "The public page could not be read.", {"type": type(exc).__name__}) from exc
        finally:
            page.remove_listener("response", receive)
            for task in tuple(tasks):
                task.cancel()
            if tasks:
                await asyncio.gather(*tuple(tasks), return_exceptions=True)
            await page.close()

    async def resolve_creator(self, query: str) -> dict[str, Any]:
        query = query.strip()
        if not query or len(query) > 160:
            raise CollectorError("invalid_query", "Provide a Douyin creator name, public ID, or profile URL.")
        parsed = urlparse(query)
        match = PROFILE_PATH.fullmatch(parsed.path) if parsed.hostname in PUBLIC_HOSTS else None
        direct_id = match.group(1) if match else query if SEC_UID.fullmatch(query) else None
        if direct_id:
            result = await self._read_page(f"https://www.douyin.com/user/{quote(direct_id, safe='=_-')}", mode="profile", sec_uid=direct_id)
            profiles = [p for p in result["profiles"] if p["sec_uid"] == direct_id]
            if len(profiles) != 1:
                raise CollectorError("no_data", "The profile page did not return a verifiable creator identity.")
            return {"status": "resolved", "profile": profiles[0], "matched_by": "profile", "resolved_at": _utc_now()}
        collected: dict[str, dict[str, Any]] = {}
        search_query = re.sub(
            r"^(?:\u6296\u97f3\u53f7|\u6296\u97f3id|douyin\s*id)\s*[:\uff1a]?\s*", "", query, flags=re.I,
        ).lstrip("@")
        result = await self._read_page(
            f"https://www.douyin.com/jingxuan/search/{quote(search_query, safe='')}"
            f"?aid={uuid.uuid4()}&type=user&enter_from=discover&source=normal_search",
            mode="search",
        )
        collected.update({p["sec_uid"]: p for p in result["profiles"]})
        resolution = select_creator(query, list(collected.values()))
        resolution["resolved_at"] = _utc_now()
        return resolution

    async def collect_latest(self, profile: dict[str, Any]) -> dict[str, Any]:
        sec_uid = _text(profile.get("sec_uid"))
        if not sec_uid or not re.fullmatch(r"[A-Za-z0-9_=-]+", sec_uid):
            raise CollectorError("invalid_profile", "A confirmed creator sec_uid is required.")
        result = await self._read_page(f"https://www.douyin.com/user/{quote(sec_uid, safe='=_-')}", mode="profile", sec_uid=sec_uid)
        timestamps = [v for v in result["videos"] if v["create_time"]]
        if not timestamps or len(timestamps) != len(result["videos"]):
            raise CollectorError("no_publish_time", "Some works lack valid publication times; the newest video cannot be verified.")
        videos = sorted(timestamps, key=lambda v: (v["create_time"], v["video_id"]), reverse=True)
        verified = next((p for p in result["profiles"] if p["sec_uid"] == sec_uid), None)
        if verified is None:
            raise CollectorError("identity_unverified", "This profile read did not verify the requested creator identity.")
        if all(v["pinned"] for v in videos):
            raise CollectorError("latest_unverified", "Only pinned works were returned; the newest work cannot be verified.")
        latest = videos[0]
        if any(value is None for value in latest["statistics"].values()):
            result["coverage"]["detail_followup_attempted"] = True
            try:
                details = await self._read_page(
                    latest["url"], mode="video", sec_uid=sec_uid, video_id=latest["video_id"],
                )
                detail = next((v for v in details["videos"] if v["video_id"] == latest["video_id"]), None)
                if detail:
                    for metric, value in detail["statistics"].items():
                        if value is not None:
                            latest["statistics"][metric] = value
                    result["coverage"]["detail_followup_obtained"] = True
            except CollectorError as exc:
                if exc.code in {"captcha", "login_required"}:
                    raise
                result["coverage"]["detail_followup_error"] = exc.code
        return {"profile": verified, "latest": videos[0], "videos": videos, "coverage": result["coverage"], "collected_at": _utc_now()}


async def resolve_creator(query: str, *, data_dir: str | Path | None = None) -> dict[str, Any]:
    async with BrowserCollector(data_dir) as collector:
        return await collector.resolve_creator(query)


async def collect_latest(profile: dict[str, Any], *, data_dir: str | Path | None = None) -> dict[str, Any]:
    async with BrowserCollector(data_dir) as collector:
        return await collector.collect_latest(profile)

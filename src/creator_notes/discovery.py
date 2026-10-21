"""Verify public-video author candidates discovered by the caller's web search.

This path never requests the blocked Douyin search endpoint. Publication order
and engagement figures must still come from live, verified profile reads.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import re
from urllib.parse import urlparse

from .collector import BrowserCollector, CollectorError, extract_profiles, select_creator, _challenge_visible


def video_id(url):
    if not isinstance(url, str) or len(url) > 4096:
        raise ValueError('Provide an observed official Douyin /video/<id> URL')
    parsed = urlparse(url)
    match = re.fullmatch(r'/video/(\d{10,25})/?', parsed.path)
    if parsed.scheme != 'https' or parsed.hostname not in ('www.douyin.com', 'douyin.com') or parsed.username or parsed.password or parsed.port or not match:
        raise ValueError('Provide an observed official Douyin /video/<id> URL')
    return match.group(1)


async def author_from_video(collector, url):
    wanted = video_id(url)
    found = None
    tasks = set()
    page = await collector._context.new_page()

    async def inspect(response):
        nonlocal found
        parsed = urlparse(response.url)
        if parsed.hostname != 'www.douyin.com' or parsed.path.rstrip('/') != '/aweme/v1/web/aweme/detail' or not response.ok:
            return
        try:
            payload = await response.json()
            if payload.get('status_code') not in (0, '0'):
                return
            item = payload.get('aweme_detail') or {}
            if str(item.get('aweme_id')) != wanted:
                return
            profiles = extract_profiles(item.get('author'))
            if len(profiles) == 1:
                found = profiles[0]
        except Exception:
            return

    def receive(response):
        task = asyncio.create_task(inspect(response))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    page.on('response', receive)
    try:
        await page.goto('https://www.douyin.com/video/' + wanted, wait_until='domcontentloaded', timeout=30000)
        deadline = asyncio.get_running_loop().time() + 30
        reloaded = False
        while asyncio.get_running_loop().time() < deadline and found is None:
            if await _challenge_visible(page):
                raise CollectorError('captcha', 'The public video requires manual verification; no account was bound.')
            await asyncio.sleep(.5)
            if not reloaded and asyncio.get_running_loop().time() > deadline - 23:
                reloaded = True
                await page.reload(wait_until='domcontentloaded', timeout=20000)
        if tasks:
            await asyncio.wait(tuple(tasks), timeout=3)
        if await _challenge_visible(page):
            raise CollectorError('captcha', 'The public video requires manual verification; no account was bound.')
        if found:
            return found
        raise CollectorError('no_data', 'The public video did not return a verifiable author; no account was bound.')
    except CollectorError:
        raise
    except Exception as exc:
        raise CollectorError('no_data', 'The public video could not be read; no account was bound.', {'type': type(exc).__name__}) from exc
    finally:
        page.remove_listener('response', receive)
        for task in tuple(tasks):
            task.cancel()
        if tasks:
            await asyncio.gather(*tuple(tasks), return_exceptions=True)
        await page.close()


async def resolve_from_public_videos(creator, candidate_video_urls, data_dir):
    """Return verified identity candidates; the caller owns durable binding.

    No cached result or public-search ordering proves an account is current.
    Every observed URL is validated before creating a browser context.
    """
    query, urls = creator, candidate_video_urls
    if not isinstance(query, str) or not isinstance(urls, (list, tuple)):
        raise ValueError('Provide a name/ID and 1-5 observed candidate URLs')
    if not query.strip() or len(query) > 160 or not 1 <= len(urls) <= 5:
        raise ValueError('Provide a name/ID and 1-5 observed candidate URLs')
    # Validate all candidates before navigating anywhere.
    urls = list(dict.fromkeys('https://www.douyin.com/video/' + video_id(url) for url in urls))
    profiles = []
    failures = []
    async with BrowserCollector(data_dir=data_dir) as collector:
        for url in urls:
            try:
                profile = await author_from_video(collector, url)
                profiles.append(profile)
            except CollectorError as exc:
                failures.append({'url': url, 'error_code': exc.code})
                if exc.code == 'captcha':
                    break
    result = select_creator(query, profiles)
    result.update(source='public_index_candidates_verified_by_live_video_author', candidate_urls=urls,
                  resolved_at=datetime.now(timezone.utc).isoformat(), failures=failures,
                  discovery_scope='caller-observed public search candidates, not an exhaustive name registry')
    if failures and result['status'] == 'resolved':
        result.update(status='ambiguous', reason='not_all_candidates_verified', candidates=profiles)
    return result

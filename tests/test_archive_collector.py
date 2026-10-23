"""Archive traversal contracts: explicit exhaustion, checkpoints and isolation."""
import asyncio
import copy
from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from creator_notes.collector import BrowserCollector, CollectorError


PROFILE = {"sec_uid": "target", "nickname": "Creator", "unique_id": "Creator"}


def work(identifier, author="target"):
    return {"aweme_id": str(identifier), "desc": f"Work {identifier}", "create_time": 1700000000 + identifier,
            "author": {"sec_uid": author, "nickname": "Creator"},
            "statistics": {"digg_count": identifier, "comment_count": 0,
                           "collect_count": 0, "share_count": 0}}


def posts(rows, more=1, cursor=100, requested="target", request_cursor=0, **extra):
    payload = {"status_code": 0, "aweme_list": rows, "max_cursor": cursor, **extra}
    if more is not None:
        payload["has_more"] = more
    return SimpleNamespace(url=f"https://www.douyin.com/aweme/v1/web/aweme/post/?sec_user_id={requested}&max_cursor={request_cursor}",
                           ok=True, json=AsyncMock(return_value=payload))


def account(count=None, author="target"):
    payload = {"status_code": 0, "user": {"sec_uid": author, "nickname": "Creator", "unique_id": "Creator"}}
    if count is not None:
        payload["user"]["aweme_count"] = count
    return SimpleNamespace(url="https://www.douyin.com/aweme/v1/web/user/profile/other/?sec_user_id=target",
                           ok=True, json=AsyncMock(return_value=payload))


class FakePage:
    def __init__(self, initial, steps=()):
        self.initial = initial
        self.steps = list(steps)
        self.listeners = {}
        self.challenge = False
        self.body = "Creator profile"
        self.viewport_size = {"width": 1440, "height": 1080}
        self.mouse = SimpleNamespace(move=AsyncMock(), wheel=AsyncMock(side_effect=self.scroll))
        self.close = AsyncMock()

    def set_default_navigation_timeout(self, value):
        pass

    def on(self, event, handler):
        self.listeners[event] = handler

    def remove_listener(self, event, handler):
        self.listeners.pop(event, None)

    async def goto(self, url, **kwargs):
        for response in self.initial:
            self.listeners["response"](response)

    async def scroll(self, *args):
        step = self.steps.pop(0) if self.steps else []
        if step == "captcha":
            self.challenge = True
        else:
            for response in step:
                self.listeners["response"](response)

    def locator(self, selector):
        if selector == "body":
            return SimpleNamespace(inner_text=AsyncMock(side_effect=lambda **kwargs: self.body))
        return SimpleNamespace(all_text_contents=AsyncMock(return_value=[]))


class ArchiveCollectorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        real_sleep = asyncio.sleep

        async def fast_sleep(*args):
            await real_sleep(0)

        self.sleep_patch = patch("creator_notes.collector.asyncio.sleep", fast_sleep)
        self.sleep_patch.start()
        self.challenge_patch = patch("creator_notes.collector._challenge_visible",
                                     AsyncMock(side_effect=lambda page: page.challenge))
        self.challenge_patch.start()
        self.addCleanup(self.sleep_patch.stop)
        self.addCleanup(self.challenge_patch.stop)

    def collector(self, page):
        collector = BrowserCollector(timeout_seconds=0.01)
        collector._start = AsyncMock()
        collector._warm_up = AsyncMock()
        collector._context = SimpleNamespace(new_page=AsyncMock(return_value=page))
        return collector

    async def test_empty_last_page_proves_exhaustion_and_duplicate_work_is_not_lost(self):
        page = FakePage([account(3), posts([work(1), work(2)])], [
            [posts([work(2), work(3)], cursor=50, request_cursor=100)],
            [posts([], more=0, cursor=0, request_cursor=50)]])
        checkpoints = []
        result = await self.collector(page).collect_archive(PROFILE,
                    on_progress=lambda value: checkpoints.append(copy.deepcopy(value)))
        self.assertEqual([item["video_id"] for item in result["works"]], ["3", "2", "1"])
        coverage = result["coverage"]
        self.assertTrue(coverage["all_history_scanned"])
        self.assertTrue(coverage["end_observed"])
        self.assertFalse(coverage["count_mismatch"])
        self.assertEqual(coverage["pages_captured"], 3)
        self.assertEqual(coverage["pagination_progress"][-1]["items"], 0)
        self.assertEqual(coverage["stop_reason"], "end_observed")
        self.assertTrue(coverage["pagination_complete"])
        self.assertEqual(coverage["chain_status"], "connected_end")
        self.assertGreaterEqual(len(checkpoints), 3)
        self.assertEqual(datetime.fromisoformat(result["works"][0]["sampled_at"]).utcoffset().total_seconds(), 0)
        page.close.assert_awaited_once()

    async def test_matching_advertised_count_does_not_replace_explicit_end(self):
        page = FakePage([account(1), posts([work(1)])])
        result = await self.collector(page).collect_archive(PROFILE, max_scrolls=2)
        self.assertFalse(result["coverage"]["all_history_scanned"])
        self.assertFalse(result["coverage"]["end_observed"])
        self.assertFalse(result["coverage"]["count_mismatch"])
        self.assertEqual(result["coverage"]["stop_reason"], "scroll_limit")

    async def test_stalled_repeated_pages_remain_partial(self):
        page = FakePage([account(), posts([work(1)])], [[posts([work(1)])] for _ in range(9)])
        result = await self.collector(page).collect_archive(PROFILE)
        self.assertFalse(result["coverage"]["all_history_scanned"])
        self.assertEqual(result["coverage"]["stop_reason"], "stalled")
        self.assertEqual(result["coverage"]["scrolls_attempted"], 8)

    async def test_verified_empty_public_account_is_complete(self):
        page = FakePage([account(0), posts([], more=0)])
        result = await self.collector(page).collect_archive(PROFILE)
        self.assertTrue(result["coverage"]["all_history_scanned"])
        self.assertEqual(result["works"], [])
        self.assertEqual(result["coverage"]["scrolls_attempted"], 0)

    async def test_empty_response_without_explicit_end_is_partial(self):
        page = FakePage([account(), posts([], more=None)])
        result = await self.collector(page).collect_archive(PROFILE, max_scrolls=1)
        self.assertFalse(result["coverage"]["all_history_scanned"])
        self.assertIsNone(result["coverage"]["has_more"])

    async def test_foreign_authors_cannot_be_added_or_claim_complete(self):
        page = FakePage([account(2), posts([work(1), work(2, "another")], more=0)])
        result = await self.collector(page).collect_archive(PROFILE)
        self.assertEqual([item["video_id"] for item in result["works"]], ["1"])
        self.assertFalse(result["coverage"]["all_history_scanned"])
        self.assertEqual(result["coverage"]["rejected_author_rows"], 1)
        self.assertEqual(result["coverage"]["stop_reason"], "author_mismatch")

    async def test_foreign_request_terminal_response_is_not_coverage(self):
        page = FakePage([account(), posts([work(1)])], [[posts([], more=0, requested="another")]])
        result = await self.collector(page).collect_archive(PROFILE, max_scrolls=1)
        self.assertFalse(result["coverage"]["end_observed"])
        self.assertEqual(result["coverage"]["pages_captured"], 1)

    async def test_captcha_preserves_partial_progress_without_retry(self):
        page = FakePage([account(), posts([work(1)])], ["captcha"])
        checkpoints = []

        async def capture(value):
            checkpoints.append(copy.deepcopy(value))

        with self.assertRaises(CollectorError) as error:
            await self.collector(page).collect_archive(PROFILE, on_progress=capture)
        self.assertEqual(error.exception.code, "captcha")
        self.assertEqual(checkpoints[-1]["coverage"]["stop_reason"], "captcha")
        self.assertEqual(len(checkpoints[-1]["works"]), 1)
        self.assertEqual(page.mouse.wheel.await_count, 1)
        self.assertFalse(checkpoints[-1]["coverage"]["all_history_scanned"])
        page.close.assert_awaited_once()

    async def test_missing_live_identity_is_explicit_failure_with_partial_checkpoint(self):
        row = work(1)
        row.pop("author")
        page = FakePage([posts([row], more=0)])
        checkpoints = []
        with self.assertRaises(CollectorError) as error:
            await self.collector(page).collect_archive(PROFILE, on_progress=checkpoints.append)
        self.assertEqual(error.exception.code, "identity_unverified")
        self.assertFalse(checkpoints[-1]["coverage"]["identity_verified"])
        self.assertFalse(checkpoints[-1]["coverage"]["all_history_scanned"])

    async def test_profile_total_is_not_taken_from_other_author_or_post_author(self):
        row = work(1)
        row["author"]["aweme_count"] = 999
        page = FakePage([account(222, author="another"), posts([row], more=0)])
        result = await self.collector(page).collect_archive(PROFILE)
        self.assertIsNone(result["coverage"]["advertised_public_works"])
        self.assertTrue(result["coverage"]["all_history_scanned"])

    async def test_terminal_with_missing_cursor_page_does_not_claim_full_history(self):
        page = FakePage([account(), posts([work(1)], cursor=100)],
                        [[posts([work(3)], more=0, request_cursor=50)]])
        result = await self.collector(page).collect_archive(PROFILE)
        self.assertTrue(result["coverage"]["end_observed"])
        self.assertFalse(result["coverage"]["pagination_complete"])
        self.assertFalse(result["coverage"]["all_history_scanned"])
        self.assertEqual(result["coverage"]["chain_status"], "disconnected")
        self.assertEqual(result["coverage"]["stop_reason"], "pagination_gap")

    async def test_exact_advertised_count_can_confirm_end_when_initial_cursor_missing(self):
        page = FakePage([account(1), posts([work(1)], more=0, request_cursor=50)])
        result = await self.collector(page).collect_archive(PROFILE)
        self.assertTrue(result["coverage"]["all_history_scanned"])
        self.assertEqual(result["coverage"]["chain_status"], "advertised_count_match")

    async def test_progress_never_claims_complete_before_access_check(self):
        page = FakePage([account(1), posts([work(1)], more=0)])
        page.challenge = True
        checkpoints = []
        with self.assertRaises(CollectorError):
            await self.collector(page).collect_archive(PROFILE, on_progress=checkpoints.append)
        self.assertTrue(checkpoints[-1]["coverage"]["end_observed"])
        self.assertTrue(all(not item["coverage"]["all_history_scanned"] for item in checkpoints))

    async def test_count_mismatch_remains_partial_even_with_connected_terminal_page(self):
        page = FakePage([account(3), posts([work(1)], more=0)])
        result = await self.collector(page).collect_archive(PROFILE)
        self.assertTrue(result["coverage"]["pagination_complete"])
        self.assertTrue(result["coverage"]["count_mismatch"])
        self.assertFalse(result["coverage"]["all_history_scanned"])
        self.assertEqual(result["coverage"]["stop_reason"], "count_mismatch")

    async def test_invalid_request_cursor_cannot_prove_terminal_coverage(self):
        page = FakePage([account(), posts([work(1)], more=0, request_cursor="invalid")])
        result = await self.collector(page).collect_archive(PROFILE, max_scrolls=1)
        self.assertFalse(result["coverage"]["end_observed"])
        self.assertFalse(result["coverage"]["all_history_scanned"])


if __name__ == "__main__":
    unittest.main()

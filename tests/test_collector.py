"""Focused contracts: identity, account isolation, counters and latest ordering."""
import unittest
from unittest.mock import AsyncMock

from creator_notes.collector import BrowserCollector, CollectorError, extract_profiles, extract_search_profiles, extract_videos, select_creator


def profile(sec_uid, nickname="\u6d4b\u8bd5\u535a\u4e3b", unique_id=""):
    return {
        "sec_uid": sec_uid, "nickname": nickname, "unique_id": unique_id,
        "short_id": "", "profile_url": f"https://www.douyin.com/user/{sec_uid}",
        "signature": "",
    }


class PublicDataTests(unittest.TestCase):
    def test_missing_counts_do_not_become_zero(self):
        result = extract_videos({"aweme_list": [{
            "aweme_id": "123", "create_time": 1720000000,
            "statistics": {"digg_count": 123456, "comment_count": 0, "collect_count": None, "collect_count_v2": 19},
        }]})[0]
        self.assertEqual(result["statistics"], {"like": 123456, "comment": 0, "collect": 19, "share": None})

    def test_invalid_boolean_is_not_numeric_metric(self):
        result = extract_videos({"aweme_id": "123", "create_time": 1720000000, "statistics": {"digg_count": True}})[0]
        self.assertIsNone(result["statistics"]["like"])

    def test_other_creators_are_excluded(self):
        result = extract_videos({"aweme_list": [
            {"aweme_id": "123", "create_time": 1720000000, "author": {"sec_uid": "target"}},
            {"aweme_id": "456", "create_time": 1720000000, "author": {"sec_uid": "other"}},
        ]}, "target")
        self.assertEqual([v["video_id"] for v in result], ["123"])

    def test_missing_author_needs_explicit_trusted_post_scope(self):
        payload = {"aweme_id": "123", "create_time": 1720000000}
        self.assertEqual(extract_videos(payload, "target"), [])
        self.assertEqual(len(extract_videos(payload, "target", allow_missing_author=True)), 1)

    def test_duplicate_names_require_selection(self):
        result = select_creator("\u6d4b\u8bd5\u535a\u4e3b", [profile("a"), profile("b")])
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(len(result["candidates"]), 2)

    def test_visible_id_takes_precedence_over_name(self):
        result = select_creator("@abc123", [profile("a", unique_id="abc123"), profile("b", nickname="abc123")])
        self.assertEqual(result["profile"]["sec_uid"], "a")
        self.assertEqual(result["matched_by"], "id")

    def test_fuzzy_single_result_is_not_silently_accepted(self):
        result = select_creator("\u6d4b\u8bd5", [profile("a", nickname="\u6d4b\u8bd5\u535a\u4e3b")])
        self.assertEqual(result["status"], "ambiguous")

    def test_profile_whitelist_does_not_return_cookie_or_private_fields(self):
        result = extract_profiles({"user_info": {
            "sec_uid": "a", "nickname": "\u6d4b\u8bd5\u535a\u4e3b", "cookie": "secret",
            "mobile": "secret", "signature": "\u516c\u5f00\u7b80\u4ecb",
        }})[0]
        self.assertNotIn("cookie", result)
        self.assertNotIn("mobile", result)
        self.assertEqual(result["signature"], "\u516c\u5f00\u7b80\u4ecb")

    def test_statistics_aweme_id_cannot_overwrite_its_work(self):
        result = extract_videos({"aweme_list": [{
            "aweme_id": "123", "create_time": 1720000000, "desc": "\u771f\u5b9e\u6807\u9898",
            "statistics": {"aweme_id": "123", "digg_count": 12345, "comment_count": 7},
        }]})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["create_time"], 1720000000)
        self.assertEqual(result[0]["title"], "\u771f\u5b9e\u6807\u9898")
        self.assertEqual(result[0]["statistics"]["like"], 12345)

    def test_failed_search_cannot_resolve_recommended_names(self):
        candidate = {"sec_uid": "target", "nickname": "\u6d4b\u8bd5\u535a\u4e3b"}
        self.assertEqual(extract_search_profiles({"status_code": 8, "user_list": [{"user_info": candidate}]}), [])
        self.assertEqual(extract_search_profiles({"status_code": 0, "recommend_users": [candidate]}), [])

    def test_only_explicit_user_search_rows_are_candidates(self):
        result = extract_search_profiles({
            "status_code": 0,
            "user_list": [{"user_info": {"sec_uid": "target", "nickname": "\u6d4b\u8bd5\u535a\u4e3b"}}],
            "recommend_users": [{"sec_uid": "unrelated", "nickname": "\u6d4b\u8bd5\u535a\u4e3b"}],
        })
        self.assertEqual([p["sec_uid"] for p in result], ["target"])


class LatestTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_metrics_are_filled_only_from_matching_video_detail(self):
        recent = extract_videos({
            "aweme_id": "222", "create_time": 1720000000,
            "statistics": {"digg_count": 3},
        })
        details = extract_videos({"aweme_list": [
            {"aweme_id": "111", "create_time": 1710000000,
             "statistics": {"comment_count": 999999, "share_count": 999999, "collect_count": 999999}},
            {"aweme_id": "222", "create_time": 1720000000,
             "statistics": {"digg_count": 5, "comment_count": 7, "collect_count": 8, "share_count": 9}},
        ]})
        c = BrowserCollector()
        c._read_page = AsyncMock(side_effect=[
            {"profiles": [profile("a")], "videos": recent, "coverage": {}},
            {"profiles": [profile("a")], "videos": details, "coverage": {}},
        ])
        result = await c.collect_latest(profile("a"))
        self.assertEqual(result["latest"]["statistics"], {"like": 5, "comment": 7, "collect": 8, "share": 9})
        self.assertTrue(result["coverage"]["detail_followup_obtained"])
        self.assertEqual(c._read_page.await_args_list[-1].kwargs["video_id"], "222")

    async def test_complete_post_counters_need_no_detail_request(self):
        recent = extract_videos({
            "aweme_id": "222", "create_time": 1720000000,
            "statistics": {"digg_count": 3, "comment_count": 0, "collect_count": 0, "share_count": 0},
        })
        c = BrowserCollector()
        c._read_page = AsyncMock(return_value={"profiles": [profile("a")], "videos": recent, "coverage": {}})
        await c.collect_latest(profile("a"))
        self.assertEqual(c._read_page.await_count, 1)

    async def test_pinned_old_high_like_does_not_displace_latest(self):
        items = extract_videos({"aweme_list": [
            {"aweme_id": "111", "create_time": 1700000000, "is_top": 1, "statistics": {"digg_count": 999999}},
            {"aweme_id": "222", "create_time": 1720000000, "statistics": {"digg_count": 3}},
            {"aweme_id": "333", "create_time": 1710000000},
        ]})
        c = BrowserCollector()
        c._read_page = AsyncMock(return_value={"profiles": [profile("a")], "videos": items, "coverage": {"all_history_scanned": False}})
        result = await c.collect_latest(profile("a"))
        self.assertEqual(result["latest"]["video_id"], "222")
        self.assertTrue(result["videos"][-1]["pinned"])
        self.assertFalse(result["coverage"]["all_history_scanned"])

    async def test_unknown_publish_time_does_not_claim_latest(self):
        items = extract_videos({"aweme_list": [
            {"aweme_id": "111", "create_time": 1700000000},
            {"aweme_id": "222", "desc": "\u7f3a\u5c11\u65f6\u95f4\u7684\u771f\u5b9e\u4f5c\u54c1"},
        ]})
        c = BrowserCollector()
        c._read_page = AsyncMock(return_value={"profiles": [], "videos": items, "coverage": {}})
        with self.assertRaises(CollectorError) as error:
            await c.collect_latest(profile("a"))
        self.assertEqual(error.exception.code, "no_publish_time")

    async def test_old_identity_is_not_a_fallback_for_unverified_live_page(self):
        c = BrowserCollector()
        c._read_page = AsyncMock(return_value={
            "profiles": [profile("other")],
            "videos": extract_videos({"aweme_id": "111", "create_time": 1700000000}),
            "coverage": {},
        })
        with self.assertRaises(CollectorError) as error:
            await c.collect_latest(profile("a"))
        self.assertEqual(error.exception.code, "identity_unverified")

    async def test_pinned_only_window_does_not_claim_latest(self):
        c = BrowserCollector()
        c._read_page = AsyncMock(return_value={
            "profiles": [profile("a")],
            "videos": extract_videos({"aweme_id": "111", "create_time": 1700000000, "is_top": 1}),
            "coverage": {},
        })
        with self.assertRaises(CollectorError) as error:
            await c.collect_latest(profile("a"))
        self.assertEqual(error.exception.code, "latest_unverified")


if __name__ == "__main__":
    unittest.main()

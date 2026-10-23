import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "taste_store.py"
spec = importlib.util.spec_from_file_location("taste_store", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class TasteStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = mod.Store(self.temp.name)
        self.store.initialize()
        self.now = datetime(2026, 10, 23, 4, 0, tzinfo=timezone.utc)

    def item(self, bvid="BV0000000001", **changes):
        return {"bvid": bvid, "title": "Synthetic candidate", "creator": "Creator",
                "duration_seconds": 1200, "url": f"https://www.bilibili.com/video/{bvid}?track=1",
                "checked_at": mod.stamp(self.now - timedelta(hours=1)), "evidence": "metadata",
                "availability": "page_available", "origin": "search", "topics": ["game"], **changes}

    def pool(self, **kwargs):
        return self.store.pool(self.now, 20, **kwargs)["items"]

    def test_ingest_deduplicates_normalizes_and_refreshes(self):
        first = self.item()
        refreshed = self.item(title="Updated")
        self.store.ingest([first, refreshed], self.now)
        items = self.pool()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Updated")
        self.assertEqual(items[0]["url"], "https://www.bilibili.com/video/BV0000000001")

    def test_recommended_opened_and_watched_remain_distinct(self):
        self.store.ingest([self.item()], self.now)
        self.store.record("BV0000000001", "opened", self.now)
        self.assertEqual(len(self.pool()), 1)
        self.store.record("BV0000000001", "recommended", self.now)
        self.assertEqual(self.pool(), [])
        self.assertEqual(len(self.pool(include_recent=True)), 1)
        self.store.record("BV0000000001", "watched", self.now)
        self.assertEqual(self.pool(include_recent=True), [])
        self.store.ingest([self.item(title="New metadata")], self.now)
        self.assertEqual(self.pool(include_recent=True), [])

    def test_stale_unknown_and_unknown_duration(self):
        self.store.ingest([self.item(checked_at=mod.stamp(self.now - timedelta(days=15))),
                           self.item("BV0000000002", availability="unknown"),
                           self.item("BV0000000003", duration_seconds=None)], self.now)
        items = self.pool()
        self.assertEqual([item["bvid"] for item in items], ["BV0000000003"])
        self.assertTrue(items[0]["duration_unknown"])

    def test_bad_url_time_and_missing_metadata_do_not_change_store(self):
        bad_changes = [dict(url="http://www.bilibili.com/video/BV0000000001"),
                       dict(url="https://www.bilibili.com.evil.test/video/BV0000000001"),
                       dict(url="https://name@www.bilibili.com/video/BV0000000001"),
                       dict(url="https://www.bilibili.com/video/BV0000000001"),
                       dict(checked_at="2026-10-23T03:00:00"),
                       dict(checked_at=mod.stamp(self.now + timedelta(seconds=1))),
                       dict(checked_at=None), dict(title=""), dict(creator=""),
                       dict(duration_seconds=-1), dict(duration_seconds=True)]
        for changes in bad_changes:
            with self.subTest(changes=changes), self.assertRaises(mod.StoreError):
                self.store.ingest([self.item(), self.item("BV0000000002", **changes)], self.now)
        self.assertEqual(self.pool(), [])

    def test_skip_is_temporary_or_same_session(self):
        self.store.ingest([self.item(checked_at=mod.stamp(self.now - timedelta(days=2)))], self.now)
        self.store.record("BV0000000001", "skip", self.now - timedelta(hours=25), session="lunch-a")
        self.assertEqual(self.pool(session="lunch-a"), [])
        self.assertEqual(len(self.pool(session="lunch-b")), 1)
        self.store.record("BV0000000001", "skip", self.now - timedelta(hours=1), session="lunch-a")
        self.assertEqual(self.pool(session="lunch-b", include_recent=True), [])

    def test_part_or_time_links_are_rejected_without_silent_first_part_fallback(self):
        for suffix in ("?p=2", "?t=20", "?start_progress=20", "#t=20", "?%70=2"):
            with self.subTest(suffix=suffix), self.assertRaises(mod.StoreError):
                self.store.ingest([self.item(url="https://www.bilibili.com/video/BV0000000001" + suffix)], self.now)
        self.assertEqual(self.pool(), [])
        self.store.ingest([self.item(url="https://www.bilibili.com/video/BV0000000001?spm_id_from=333.1387")], self.now)
        self.assertEqual(self.pool()[0]["url"], "https://www.bilibili.com/video/BV0000000001")

    def test_feedback_and_init_preserve_profile_and_corruption(self):
        self.store.ingest([self.item()], self.now)
        profile = {"schema_version": 1, "explicit_preferences": {"duration": [10, 30]}, "custom": "keep"}
        mod.atomic_json(self.store.path("profile"), profile)
        self.store.record("BV0000000001", "feedback", self.now, text="今天不想看这个")
        self.store.initialize()
        self.assertEqual(self.store.load("profile"), profile)
        self.assertEqual(len(self.pool()), 1)
        self.store.path("events").write_text("broken json", encoding="utf-8")
        with self.assertRaises(mod.StoreError):
            self.store.initialize()
        self.assertEqual(self.store.path("events").read_text(), "broken json")

    def test_watched_survives_event_log_rollover(self):
        self.store.ingest([self.item(), self.item("BV0000000002")], self.now)
        self.store.record("BV0000000001", "watched", self.now)
        # Exercise rollover without 500 repeated filesystem mutations.
        events = [{"bvid": "BV0000000002", "event": "opened", "at": mod.stamp(self.now)} for _ in range(500)]
        mod.atomic_json(self.store.path("events"), {"schema_version": 1, "events": events})
        self.store.record("BV0000000002", "opened", self.now)
        self.assertEqual(len(self.store.load("events")["events"]), 500)
        self.assertEqual([item["bvid"] for item in self.pool()], ["BV0000000002"])

    def test_blank_profile_has_defaults_without_personal_seed(self):
        profile = self.store.load("profile")
        self.assertEqual(profile["defaults"], {"recommendation_count": 5,
            "preferred_duration_minutes": [10, 30], "strict_duration": False})
        for field in ("explicit_preferences", "inferred_preferences", "preference_rules",
                      "session_rules", "feedback_observations", "learning_history"):
            self.assertFalse(profile[field])
        self.assertEqual(self.store.status(self.now)["learning_summary"]["feedback_video_count"], 0)

    def learn(self, target="game", stance="dislike", kind="topic", scope="long-term", **kwargs):
        return self.store.learn(kind, target, stance, scope, "User's actual words", self.now, **kwargs)

    def test_explicit_rules_filter_casefold_exact_and_provide_like_matches(self):
        self.store.ingest([self.item(topics=["GAME"]),
                           self.item("BV0000000002", topics=["gameplay"], creator="Other")], self.now)
        self.learn()
        self.assertEqual([i["bvid"] for i in self.pool(session="another-meal")], ["BV0000000002"])
        self.learn(target="other", stance="like", kind="creator")
        match = self.pool()[0]["preference_matches"]
        self.assertEqual([(m["kind"], m["stance"]) for m in match], [("creator", "like")])
        self.learn(target="GAME", stance="neutral")
        self.assertEqual(len(self.pool()), 2)
        self.assertEqual(len(self.store.load("profile")["preference_rules"]), 1)
        self.assertEqual(len(self.store.load("profile")["learning_history"]), 3)

    def test_creator_dislike_filters_only_that_creator(self):
        self.store.ingest([self.item(), self.item("BV0000000002", creator="Other")], self.now)
        self.learn(target="CREATOR", kind="creator")
        self.assertEqual([i["creator"] for i in self.pool()], ["Other"])

    def test_session_rule_is_same_meal_only_and_expires_after_24_hours(self):
        self.store.ingest([self.item()], self.now)
        self.learn(scope="session", session="meal-a")
        self.assertEqual(self.pool(session="meal-a"), [])
        self.assertEqual(len(self.pool(session="meal-b")), 1)
        self.assertEqual(len(self.pool()), 1)
        later = self.now + timedelta(hours=24)
        self.assertEqual(len(self.store.pool(later, 20, session="meal-a")["items"]), 1)
        self.assertEqual(self.store.taste(later, "meal-a")["session_rules"], [])

    def test_latest_session_rule_overrides_long_term_then_long_term_returns(self):
        self.store.ingest([self.item()], self.now)
        self.learn()
        self.learn(scope="session", session="meal-a", stance="like")
        self.assertEqual(len(self.pool(session="meal-a")), 1)
        self.assertEqual(self.pool(session="meal-b"), [])
        self.learn(scope="session", session="meal-a", stance="dislike")
        self.assertEqual(self.pool(session="meal-a"), [])
        self.learn(scope="session", session="meal-a", stance="neutral")
        self.assertEqual(len(self.pool(session="meal-a")), 1)
        self.assertEqual(len(self.store.load("profile")["session_rules"]), 1)
        self.assertEqual(self.store.pool(self.now + timedelta(hours=24), 20, session="meal-a")["items"], [])

    def test_learning_requires_words_and_session_and_does_not_change_bad_input(self):
        before = self.store.load("profile")
        for text, scope, session in (("", "long-term", None), ("words", "session", None),
                                      ("words", "session", "  ")):
            with self.subTest(text=text, scope=scope), self.assertRaises(mod.StoreError):
                self.store.learn("topic", "game", "like", scope, text, self.now, session)
        self.assertEqual(self.store.load("profile"), before)

    def test_single_video_dislike_is_correctable_without_category_ban_or_watched(self):
        self.store.ingest([self.item(), self.item("BV0000000002")], self.now)
        self.store.record("BV0000000001", "feedback", self.now, text="Do not want this one", reaction="dislike")
        self.assertEqual([i["bvid"] for i in self.pool()], ["BV0000000002"])
        self.assertEqual(self.store.taste(self.now)["explicit_rules"], [])
        self.assertNotIn("watched_at", self.store.load("candidates")["items"][0])
        self.store.ingest([self.item(title="Refreshed", taste_reaction="like")], self.now)
        self.assertEqual(self.store.load("candidates")["items"][0]["taste_reaction"], "dislike")
        for reaction in ("neutral", "like"):
            self.store.record("BV0000000001", "feedback", self.now, text="Correcting that", reaction=reaction)
            self.assertEqual(len(self.pool()), 2)
            self.assertEqual(len(self.store.load("profile")["feedback_observations"]), 1)

    def test_feedback_confidence_counts_different_videos_and_only_hints(self):
        self.store.ingest([self.item(), self.item("BV0000000002"),
                           self.item("BV0000000003")], self.now)
        for _ in range(2):
            self.store.record("BV0000000001", "feedback", self.now, text="Not my taste", reaction="dislike")
        summary = self.store.taste(self.now)["inferred"]["topics"][0]
        self.assertEqual((summary["dislike_count"], summary["distinct_video_count"], summary["confidence"]), (1, 1, "weak"))
        self.store.record("BV0000000002", "feedback", self.now, text="Also not my taste", reaction="dislike")
        summary = self.store.taste(self.now)["inferred"]["topics"][0]
        self.assertEqual((summary["dislike_count"], summary["distinct_video_count"], summary["confidence"]), (2, 2, "medium"))
        self.assertEqual(len(self.pool()), 1)
        self.assertEqual(self.pool()[0]["inferred_taste_hints"][0]["confidence"], "medium")
        self.assertEqual(self.store.taste(self.now)["inferred"]["role"], "ranking_hint_only")

    def test_feedback_from_different_meals_accumulates_persistent_inference(self):
        self.store.ingest([self.item(), self.item("BV0000000002"),
                           self.item("BV0000000003")], self.now)
        self.store.record("BV0000000001", "feedback", self.now, text="Liked it", reaction="like", session="meal-a")
        next_meal = self.now + timedelta(days=1)
        self.store.record("BV0000000002", "feedback", next_meal, text="Also liked it", reaction="like", session="meal-b")
        later = next_meal + timedelta(days=2)
        for session in (None, "meal-a", "meal-b", "meal-c"):
            with self.subTest(session=session):
                taste = self.store.taste(later, session)
                summary = taste["inferred"]["topics"][0]
                self.assertEqual((summary["like_count"], summary["distinct_video_count"], summary["confidence"]), (2, 2, "medium"))
                self.assertEqual(taste["active_feedback_video_count"], 2)
        observations = self.store.load("profile")["feedback_observations"]
        self.assertEqual([item["session"] for item in observations], ["meal-a", "meal-b"])
        self.assertEqual(self.store.taste(later)["explicit_rules"], [])

    def test_v1_custom_fields_explicit_preferences_and_watched_survive_learning(self):
        profile = {"schema_version": 1, "explicit_preferences": {"duration": [10, 30], "custom": True},
                   "inferred_preferences": [{"historical": True}], "custom": {"preserve": True}}
        mod.atomic_json(self.store.path("profile"), profile)
        self.store.ingest([self.item(custom="candidate field")], self.now)
        self.store.record("BV0000000001", "watched", self.now)
        self.learn(stance="like")
        self.store.record("BV0000000001", "feedback", self.now, text="Liked this", reaction="like")
        saved = self.store.load("profile")
        for key, value in profile.items():
            self.assertEqual(saved[key], value)
        self.store.ingest([self.item(title="Updated")], self.now)
        candidate = self.store.load("candidates")["items"][0]
        self.assertEqual(candidate["custom"], "candidate field")
        self.assertEqual(candidate["watched_at"], mod.stamp(self.now))
        self.assertEqual(candidate["taste_reaction"], "like")
        self.assertEqual(self.pool(), [])

    def test_learning_and_observation_history_are_bounded_and_keep_latest_words(self):
        profile = self.store.load("profile")
        profile["learning_history"] = [{"text": "older"}] * 100
        profile["feedback_observations"] = [{"bvid": f"BV{i:010d}", "creator": "Old", "topics": [],
            "reaction": "neutral", "session": None, "at": mod.stamp(self.now), "text": "old"} for i in range(300)]
        mod.atomic_json(self.store.path("profile"), profile)
        self.learn(stance="like")
        self.store.ingest([self.item()], self.now)
        self.store.record("BV0000000001", "feedback", self.now, text="Newest words", reaction="like")
        saved = self.store.load("profile")
        self.assertEqual(len(saved["learning_history"]), 100)
        self.assertEqual(saved["learning_history"][-1]["text"], "User's actual words")
        self.assertEqual(len(saved["feedback_observations"]), 300)
        self.assertEqual(saved["feedback_observations"][-1]["text"], "Newest words")


if __name__ == "__main__":
    unittest.main()

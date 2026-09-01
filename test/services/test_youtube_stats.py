import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.services import youtube_stats as stats


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


class ManifestTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = patch(
            "app.utils.utils.storage_dir", return_value=self._tmp.name
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _manifest(self, name, items):
        directory = os.path.join(self._tmp.name, name)
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "manifest.json"), "w", encoding="utf-8") as h:
            json.dump({"batch_id": name, "items": items}, h)


class TestCollectPublished(ManifestTestCase):
    def test_only_items_with_a_video_id_are_collected(self):
        self._manifest("b1", [
            {"subject": "Uploaded", "youtube": {"video_id": "a1", "url": "u"}},
            {"subject": "Not uploaded", "youtube": None},
            {"subject": "Upload failed", "youtube": {"error": "boom"}},
        ])

        rows = stats.collect_published_videos()

        self.assertEqual([r["subject"] for r in rows], ["Uploaded"])

    def test_corrupt_manifest_is_skipped_not_fatal(self):
        self._manifest("good", [{"subject": "A", "youtube": {"video_id": "a1"}}])
        bad = os.path.join(self._tmp.name, "bad")
        os.makedirs(bad, exist_ok=True)
        with open(os.path.join(bad, "manifest.json"), "w") as handle:
            handle.write("{oops")

        self.assertEqual(len(stats.collect_published_videos()), 1)

    def test_known_subjects_are_lowercased_for_dedupe(self):
        self._manifest("b1", [{"subject": "Why Fish Bite", "youtube": {"video_id": "x"}}])

        self.assertIn("why fish bite", stats.known_subjects())


class TestSignalGuard(unittest.TestCase):
    """样本太小时把随机波动当成结论，是这个功能最容易犯的错误。"""

    def test_small_sample_is_flagged_as_meaningless(self):
        rows = [{"published_at": _iso(30)} for _ in range(5)]

        ok, message = stats.has_meaningful_signal(rows)

        self.assertFalse(ok)
        self.assertIn("noise", message)

    def test_fresh_videos_are_flagged_even_with_enough_of_them(self):
        rows = [{"published_at": _iso(1)} for _ in range(stats.MIN_VIDEOS_FOR_SIGNAL)]

        ok, message = stats.has_meaningful_signal(rows)

        self.assertFalse(ok)
        self.assertIn("day", message)

    def test_enough_videos_and_enough_age_passes(self):
        rows = [
            {"published_at": _iso(stats.MIN_AGE_DAYS_FOR_SIGNAL + 5)}
            for _ in range(stats.MIN_VIDEOS_FOR_SIGNAL)
        ]

        ok, message = stats.has_meaningful_signal(rows)

        self.assertTrue(ok)
        self.assertEqual(message, "")


class TestFetchStats(unittest.TestCase):
    def _service(self, items):
        service = MagicMock()
        service.videos.return_value.list.return_value.execute.return_value = {
            "items": items
        }
        return service

    def test_statistics_are_parsed_into_integers(self):
        service = self._service([
            {
                "id": "v1",
                "snippet": {"title": "T", "publishedAt": _iso(10)},
                "statistics": {"viewCount": "1234", "likeCount": "56", "commentCount": "7"},
            }
        ])
        with patch.object(stats, "_service", return_value=({"HttpError": Exception}, service)):
            result = stats.fetch_video_stats(["v1"])

        self.assertEqual(result["v1"]["views"], 1234)
        self.assertEqual(result["v1"]["likes"], 56)
        self.assertEqual(result["v1"]["comments"], 7)

    def test_missing_counters_default_to_zero(self):
        service = self._service([
            {"id": "v1", "snippet": {"title": "T", "publishedAt": ""}, "statistics": {}}
        ])
        with patch.object(stats, "_service", return_value=({"HttpError": Exception}, service)):
            result = stats.fetch_video_stats(["v1"])

        self.assertEqual(result["v1"]["views"], 0)

    def test_ids_are_batched_fifty_at_a_time(self):
        service = self._service([])
        with patch.object(stats, "_service", return_value=({"HttpError": Exception}, service)):
            stats.fetch_video_stats([f"v{n}" for n in range(120)])

        # 120 个 id 应当分成 50 + 50 + 20 共三次请求。
        self.assertEqual(service.videos.return_value.list.call_count, 3)

    def test_empty_input_makes_no_request(self):
        with patch.object(stats, "_service") as service:
            self.assertEqual(stats.fetch_video_stats([]), {})
        service.assert_not_called()


class TestScopePreflight(unittest.TestCase):
    def test_missing_readonly_scope_gives_an_actionable_error(self):
        with patch("app.services.youtube._google_modules", return_value={}), patch(
            "app.services.youtube._load_credentials", return_value=MagicMock()
        ), patch(
            "app.services.youtube.missing_scopes",
            return_value=["https://www.googleapis.com/auth/youtube.readonly"],
        ):
            with self.assertRaises(stats.YouTubeStatsError) as caught:
                stats._service()

        message = str(caught.exception)
        # 原始 403 只说"权限不足"，用户无法据此知道该做什么。
        self.assertIn("youtube.readonly", message)
        self.assertIn("--youtube-auth", message)


class TestTopicParsing(unittest.TestCase):
    def test_plain_json_array(self):
        self.assertEqual(stats._parse_topic_list('["a","b"]'), ["a", "b"])

    def test_code_fenced_json(self):
        self.assertEqual(
            stats._parse_topic_list('```json\n["a","b"]\n```'), ["a", "b"]
        )

    def test_surrounding_prose_is_tolerated(self):
        self.assertEqual(
            stats._parse_topic_list('Sure! ["a","b"] hope that helps'), ["a", "b"]
        )

    def test_malformed_output_yields_nothing(self):
        self.assertEqual(stats._parse_topic_list("not json at all"), [])
        self.assertEqual(stats._parse_topic_list(""), [])


class TestFootageClaimFilter(unittest.TestCase):
    """无人值守时没人复核选题，承诺画面的标题必须被自动挡掉。"""

    def test_first_person_claims_are_rejected(self):
        for topic in (
            "I Tested Every Fishing Lure at Walmart",
            "I Left a GoPro in a Carp Swim Overnight",
            "We tried fishing in a storm",
            "My biggest catch ever",
        ):
            self.assertTrue(stats.promises_footage(topic), topic)

    def test_footage_promises_are_rejected(self):
        for topic in (
            "Caught on camera: shark steals a catch",
            "Canal fishing gone WRONG",
            "Watch what happens when the line snaps",
            "Here's what happened next",
        ):
            self.assertTrue(stats.promises_footage(topic), topic)

    def test_informational_topics_are_kept(self):
        for topic in (
            "The correct way to spool a baitcaster reel",
            "Why fish bite before a storm",
            "How to rig a live grasshopper without hurting it",
            "Small sea creatures you should never touch",
            "What makes a good travel fishing rod",
        ):
            self.assertFalse(stats.promises_footage(topic), topic)

    def test_empty_input_is_not_flagged(self):
        self.assertFalse(stats.promises_footage(""))


class TestNearDuplicateDetection(unittest.TestCase):
    """仅比较字符串相等挡不住改写，会导致重复制作同一个视频。"""

    def test_reworded_topic_is_caught(self):
        existing = {"why fish feed aggressively right before a storm"}

        self.assertTrue(
            stats.is_near_duplicate("Why bass feed aggressively right before a storm", existing)
        )

    def test_same_idea_different_phrasing_is_caught(self):
        existing = {"how to stop line twist on a spinning reel"}

        self.assertTrue(
            stats.is_near_duplicate("How to stop line twist on a spinning reel for good", existing)
        )

    def test_genuinely_different_topics_are_kept(self):
        existing = {"why fish bite before a storm"}

        for topic in (
            "The correct way to spool a baitcaster reel",
            "What largemouth bass eat in winter",
            "How to rig a live grasshopper",
        ):
            self.assertFalse(stats.is_near_duplicate(topic, existing), topic)

    def test_different_species_are_not_duplicates_of_each_other(self):
        # 同一句式换鱼种是不同的视频，必须放行，否则兜底池会枯竭。
        existing = {"how fishermen catch largemouth bass"}

        self.assertFalse(
            stats.is_near_duplicate("How fishermen catch northern pike", existing)
        )

    def test_empty_topic_is_not_flagged(self):
        self.assertFalse(stats.is_near_duplicate("", {"anything"}))


class TestFallbackTopics(unittest.TestCase):
    def test_full_matrix_is_available_when_nothing_exists(self):
        topics = stats.fallback_topics(500, existing=set())

        self.assertEqual(
            len(topics), len(stats.FALLBACK_SPECIES) * len(stats.FALLBACK_TEMPLATES)
        )
        self.assertEqual(len(topics), len(set(topics)))

    def test_species_are_covered_before_templates_repeat(self):
        topics = stats.fallback_topics(5, existing=set())

        # 先横向覆盖不同鱼种，而不是把同一条鱼写满每个句式。
        self.assertEqual(len({t.split("catch ")[-1] for t in topics}), 5)

    def test_existing_topics_are_not_regenerated(self):
        first = stats.fallback_topics(3, existing=set())
        again = stats.fallback_topics(3, existing={t.lower() for t in first})

        self.assertFalse(set(first) & set(again))

    def test_count_is_respected(self):
        self.assertEqual(len(stats.fallback_topics(7, existing=set())), 7)


class TestSuggestTopics(ManifestTestCase):
    def test_already_used_topics_are_filtered_out(self):
        self._manifest("b1", [
            {"subject": "Why fish bite", "youtube": {"video_id": "x"}}
        ])
        with patch(
            "app.services.llm._generate_response",
            return_value='["Why fish bite","A brand new topic"]',
        ):
            topics = stats.suggest_topics(["Why fish bite"], count=5)

        self.assertEqual(topics, ["A brand new topic"])

    def test_topics_promising_footage_are_discarded(self):
        with patch(
            "app.services.llm._generate_response",
            return_value='["I Left a GoPro Overnight","How to spool a baitcaster"]',
        ):
            topics = stats.suggest_topics(["x"], count=5)

        self.assertEqual(topics, ["How to spool a baitcaster"])

    def test_near_duplicate_suggestions_are_discarded(self):
        self._manifest("b1", [
            {"subject": "Why fish feed aggressively before a storm",
             "youtube": {"video_id": "x"}}
        ])
        with patch(
            "app.services.llm._generate_response",
            return_value='["Why bass feed aggressively before a storm","How to spool a reel"]',
        ):
            topics = stats.suggest_topics(["x"], count=5)

        self.assertEqual(topics, ["How to spool a reel"])

    def test_llm_error_string_yields_no_topics(self):
        with patch("app.services.llm._generate_response", return_value="Error: quota"):
            self.assertEqual(stats.suggest_topics(["x"]), [])

    def test_llm_exception_does_not_propagate(self):
        with patch("app.services.llm._generate_response", side_effect=RuntimeError("down")):
            self.assertEqual(stats.suggest_topics(["x"]), [])

    def test_no_inputs_makes_no_llm_call(self):
        with patch("app.services.llm._generate_response") as generate:
            self.assertEqual(stats.suggest_topics([], []), [])
        generate.assert_not_called()

    def test_result_is_capped_to_requested_count(self):
        # 选题必须彼此不同，否则会被判重逻辑正确地过滤掉。
        distinct = [
            f"How to fish for {species}"
            for species in stats.FALLBACK_SPECIES[:30]
        ]
        with patch(
            "app.services.llm._generate_response", return_value=json.dumps(distinct)
        ):
            self.assertEqual(len(stats.suggest_topics(["x"], count=4)), 4)


if __name__ == "__main__":
    unittest.main()

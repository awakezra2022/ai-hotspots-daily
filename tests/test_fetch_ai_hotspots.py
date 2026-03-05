import unittest
from datetime import datetime, timezone

from scripts.fetch_ai_hotspots import build_event_id, compute_final_score, parse_datetime


class FetchAiHotspotsTests(unittest.TestCase):
    def test_parse_datetime_supports_36kr_format(self) -> None:
        dt = parse_datetime("2026-03-05 08:24:53  +0800")
        self.assertIsNotNone(dt)
        assert dt is not None
        self.assertEqual(dt.tzinfo, timezone.utc)
        self.assertEqual(dt.hour, 0)
        self.assertEqual(dt.minute, 24)
        self.assertEqual(dt.second, 53)

    def test_build_event_id_clusters_qwen_org_change_titles(self) -> None:
        event_id_a, _ = build_event_id("Qwen 架构大地震：核心团队调整")
        event_id_b, _ = build_event_id("通义千问核心团队架构调整，阿里回应")
        self.assertEqual(event_id_a, event_id_b)

    def test_compute_final_score_is_reproducible(self) -> None:
        score = compute_final_score(
            recency_score=88.5,
            media_hotness_score=14,
            social_heat_score=18,
            verification_bonus=20,
        )
        self.assertEqual(score, 140.5)

    def test_compute_final_score_boundary(self) -> None:
        now = datetime.now(timezone.utc)
        old = now.replace(year=now.year - 1)
        # 仅确认不出现异常，且分值可按公式组合
        base = compute_final_score(0.0, 0, 0, 0)
        boosted = compute_final_score(100.0, 30, 30, 20)
        self.assertEqual(base, 0.0)
        self.assertEqual(boosted, 180.0)
        self.assertLess(old, now)


if __name__ == "__main__":
    unittest.main()

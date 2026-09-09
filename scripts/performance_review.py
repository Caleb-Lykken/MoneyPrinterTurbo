#!/usr/bin/env python
"""
按周复盘：哪些因素真正影响播放量。

方法上的两个要点：
  * **按年龄分组比较**。发布早的视频天然积累更多播放量，直接比较会把"发得早"
    误读成"发得好"，因此只在年龄相近的分组内比较各时段/选题的表现。
  * **看中位数而非最大值**。播放量呈幂律分布，少数爆款会让均值和最大值失真；
    早期就曾据此把"鱼饵颜色"误判为优势选题，实际中位数只有基线的 0.4 倍。
"""

from __future__ import annotations

import argparse
import collections
import os
import re
import statistics
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

from app.services import youtube_stats  # noqa: E402

_STOPWORDS = set(
    "the a an to for of in on and or how why what where when your you that this "
    "these is are do does it with not can from more than most best actually "
    "really about into out up down at his her".split()
)
MIN_GROUP = 6


def _load(min_age_days: float = 1.0) -> list[dict]:
    rows = []
    for row in youtube_stats.performance_rows():
        if row.get("views") is None or (row.get("age_days") or 0) < min_age_days:
            continue
        moment = datetime.fromisoformat(
            row["published_at"].replace("Z", "+00:00")
        ).astimezone()
        rows.append({**row, "hour": moment.hour, "day": moment.date()})
    return rows


def _table(title: str, groups: dict, baseline: float) -> None:
    usable = [
        (key, statistics.median(vals), len(vals))
        for key, vals in groups.items()
        if len(vals) >= MIN_GROUP
    ]
    if not usable:
        print(f"\n{title}\n  not enough data yet (need {MIN_GROUP}+ per group)")
        return
    usable.sort(key=lambda item: -item[1])
    print(f"\n{title}")
    print(f"  {'group':26}{'median':>8}{'n':>5}   vs baseline")
    for key, median, count in usable:
        print(f"  {str(key)[:26]:26}{median:>8.0f}{count:>5}   {median / baseline:>5.1f}x")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cohort-days",
        type=int,
        default=14,
        help="only compare videos published within this many days, so age differences "
        "do not masquerade as quality differences",
    )
    args = parser.parse_args()

    rows = _load()
    if not rows:
        print("no published videos with statistics yet")
        return 0

    trustworthy, note = youtube_stats.has_meaningful_signal(rows)
    newest = max(row["day"] for row in rows)
    cohort = [
        row for row in rows if (newest - row["day"]).days <= args.cohort_days
    ]
    baseline = statistics.median([row["views"] for row in cohort]) or 1

    views = sorted(row["views"] for row in rows)
    total = sum(views)
    top_decile = sum(sorted(views, reverse=True)[: max(1, len(views) // 10)])
    print(f"{len(rows)} videos   {total:,} views   median {statistics.median(views):.0f}")
    print(f"top 10% of videos carry {top_decile / total * 100:.0f}% of all views")
    if not trustworthy:
        print(f"\n! rankings below are not yet reliable: {note}")
    print(f"\ncohort for comparison: last {args.cohort_days} days, n={len(cohort)}, baseline median {baseline:.0f}")

    by_hour = collections.defaultdict(list)
    for row in cohort:
        by_hour[f"{row['hour']:02}:00"].append(row["views"])
    _table("BY UPLOAD HOUR", by_hour, baseline)

    by_day = collections.defaultdict(list)
    for row in cohort:
        by_day[row["day"].strftime("%a")].append(row["views"])
    _table("BY WEEKDAY", by_day, baseline)

    by_word = collections.defaultdict(list)
    for row in cohort:
        text = row.get("subject") or row.get("title", "")
        for word in {
            w for w in re.findall(r"[a-z]+", text.lower())
            if w not in _STOPWORDS and len(w) > 3
        }:
            by_word[word].append(row["views"])
    ranked = [
        (w, statistics.median(v), len(v)) for w, v in by_word.items() if len(v) >= MIN_GROUP
    ]
    ranked.sort(key=lambda item: -item[1])
    if ranked:
        print("\nTOPIC WORDS — best")
        for word, median, count in ranked[:8]:
            print(f"  {word:26}{median:>8.0f}{count:>5}   {median / baseline:>5.1f}x")
        print("TOPIC WORDS — worst")
        for word, median, count in ranked[-6:]:
            print(f"  {word:26}{median:>8.0f}{count:>5}   {median / baseline:>5.1f}x")

    print("\nWEEKLY TREND — views per video by publish week")
    by_week = collections.defaultdict(list)
    for row in rows:
        by_week[row["day"].isocalendar()[:2]].append(row["views"])
    for week in sorted(by_week):
        vals = by_week[week]
        print(
            f"  week {week[1]}  n={len(vals):3}  total={sum(vals):6,}  "
            f"median={statistics.median(vals):5.0f}  per video={sum(vals) / len(vals):6.1f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

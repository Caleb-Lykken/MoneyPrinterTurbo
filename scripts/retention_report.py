#!/usr/bin/env python
"""
留存报告：按视频读取 YouTube Analytics 的平均观看百分比与时长。

Shorts 推荐主要看观众是否看完，而不是点了多少次。播放量只能说明哪些视频被
推了，留存才能说明为什么。需要 ``yt-analytics.readonly`` 权限。
"""

from __future__ import annotations

import os
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

from app.services import youtube, youtube_stats  # noqa: E402


def fetch_retention(days: int = 28) -> list[dict]:
    if youtube.missing_scopes([youtube.ANALYTICS_SCOPE]):
        raise SystemExit(
            "retention needs the yt-analytics.readonly scope; re-run: "
            + youtube.auth_command()
        )
    modules = youtube._google_modules()
    credentials = youtube._load_credentials()
    analytics = modules["build"](
        "youtubeAnalytics", "v2", credentials=credentials, cache_discovery=False
    )
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    try:
        response = analytics.reports().query(
            ids="channel==MINE",
            startDate=start.isoformat(),
            endDate=end.isoformat(),
            metrics="views,averageViewPercentage,averageViewDuration,likes,subscribersGained",
            dimensions="video",
            sort="-views",
            maxResults=200,
        ).execute()
    except modules["HttpError"] as exc:
        body = getattr(exc, "content", b"")
        body = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
        # 授权范围与"项目是否启用该 API"是两回事：token 有 analytics 权限，
        # 但 Cloud 项目里没开 YouTube Analytics API 时同样返回 403。
        if "accessNotConfigured" in body or "has not been used in project" in body:
            match = re.search(r"https://console\.developers\.google\.com/apis/api/youtubeanalytics[^\s\"']+", body)
            link = match.group(0) if match else "https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com"
            raise SystemExit(
                "The YouTube Analytics API is not enabled in your Google Cloud project.\n"
                "This is separate from the YouTube Data API. Enable it here, wait a few\n"
                f"minutes, then re-run:\n  {link}"
            ) from exc
        raise
    headers = [h["name"] for h in response.get("columnHeaders", [])]
    rows = [dict(zip(headers, row)) for row in response.get("rows", [])]
    subjects = {r["video_id"]: r["subject"] for r in youtube_stats.collect_published_videos()}
    for row in rows:
        row["subject"] = subjects.get(row["video"], "")
    return rows


def main() -> int:
    rows = fetch_retention()
    if not rows:
        print("no analytics rows yet (data lags ~48h)")
        return 0
    pct = [r["averageViewPercentage"] for r in rows]
    print(f"{len(rows)} videos with analytics\n")
    print(f"avg view %  median {statistics.median(pct):.0f}%   p25 {sorted(pct)[len(pct)//4]:.0f}%   p75 {sorted(pct)[3*len(pct)//4]:.0f}%")
    print(f"subscribers gained (28d): {sum(int(r.get('subscribersGained',0)) for r in rows)}\n")
    print("HIGHEST RETENTION (what holds attention)")
    for r in sorted(rows, key=lambda r: -r["averageViewPercentage"])[:8]:
        print(f"  {r['averageViewPercentage']:5.0f}%  {int(r['views']):>5} views  {(r['subject'] or r['video'])[:50]}")
    print("\nLOWEST RETENTION (what loses them)")
    for r in sorted(rows, key=lambda r: r["averageViewPercentage"])[:6]:
        print(f"  {r['averageViewPercentage']:5.0f}%  {int(r['views']):>5} views  {(r['subject'] or r['video'])[:50]}")
    print("\nHIGH VIEWS, LOW RETENTION (clickbait risk — algorithm will stop pushing)")
    med = statistics.median(pct)
    for r in sorted(rows, key=lambda r: -r["views"])[:20]:
        if r["averageViewPercentage"] < med * 0.8:
            print(f"  {r['averageViewPercentage']:5.0f}%  {int(r['views']):>5} views  {(r['subject'] or r['video'])[:50]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
"""按天统计实际被 YouTube 接受的上传数量，用于观察真实的频道上限。"""

from __future__ import annotations

import collections
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

from app.services import youtube_stats  # noqa: E402

# Cloud Console 中 "Video Uploads per day" 的项目额度。真正拦住上传的是频道级
# 限制，它不在控制台里显示，因此这里用实际接受量来反推真实上限。
PROJECT_QUOTA = 100


def channel_uploads() -> list[dict]:
    """
    读取频道上传播放列表中的全部视频。

    只统计批次清单里的视频会漏掉手动上传的内容，而频道级上传额度对两者一视同仁，
    据此推算出的"每日上限"会明显偏低。
    """
    from app.services import youtube

    modules = youtube._google_modules()
    credentials = youtube._load_credentials()
    if credentials is None:
        return []
    service = modules["build"](
        "youtube", "v3", credentials=credentials, cache_discovery=False
    )
    channel = service.channels().list(part="contentDetails", mine=True).execute()
    items = channel.get("items") or []
    if not items:
        return []
    playlist = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

    video_ids, token = [], None
    while True:
        page = service.playlistItems().list(
            part="contentDetails", playlistId=playlist, maxResults=50, pageToken=token
        ).execute()
        video_ids += [
            entry["contentDetails"]["videoId"] for entry in page.get("items", [])
        ]
        token = page.get("nextPageToken")
        if not token:
            break

    stats = youtube_stats.fetch_video_stats(video_ids)
    known = {row["video_id"] for row in youtube_stats.collect_published_videos()}
    return [
        {**entry, "from_pipeline": video_id in known}
        for video_id, entry in stats.items()
    ]


def main() -> int:
    rows = [r for r in channel_uploads() if r.get("published_at")]
    if not rows:
        print("no published videos found")
        return 0

    by_day: collections.defaultdict = collections.defaultdict(lambda: [0, 0])
    by_hour: collections.Counter = collections.Counter()
    for row in rows:
        moment = datetime.fromisoformat(
            row["published_at"].replace("Z", "+00:00")
        ).astimezone()
        by_day[moment.date()][0 if row.get("from_pipeline") else 1] += 1
        by_hour[moment.hour] += 1

    manual_total = sum(counts[1] for counts in by_day.values())
    print(f"{len(rows)} videos on the channel ({manual_total} uploaded outside this tool)\n")
    print(f"  {'day':12} {'pipeline':>9} {'manual':>7} {'total':>6}")
    for day in sorted(by_day):
        pipeline, manual = by_day[day]
        total = pipeline + manual
        print(f"  {day}  {pipeline:>9} {manual:>7} {total:>6}  {'#' * min(total, 40)}")

    peak = max(sum(counts) for counts in by_day.values())
    print(f"\n  highest single day : {peak}")
    print(f"  project quota      : {PROJECT_QUOTA} (Cloud Console)")
    print(f"  headroom to quota  : {PROJECT_QUOTA - peak}")
    if peak < PROJECT_QUOTA:
        print(
            "  note: the channel-level cap stops uploads well before the project\n"
            "        quota is reached, and it is not shown in Cloud Console."
        )

    print("\nupload hour distribution (local):")
    for hour in sorted(by_hour):
        print(f"  {hour:02}:00  {by_hour[hour]:3}  {'#' * min(by_hour[hour], 60)}")
    spread = len(by_hour)
    print(f"\n  distinct hours used: {spread}/24")
    if spread <= 4:
        print(
            "  note: Shorts are cold-seeded to a small test audience, so uploads\n"
            "        clustered in a few hours compete with each other."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

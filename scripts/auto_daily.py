#!/usr/bin/env python
"""
每日自动化：先补发积压，再按同领域热门趋势生成新选题并渲染发布。

设计要点：
  * **先补发再生产**：上传受频道每日额度限制，积压视频必须优先发布，
    否则新渲染只会让积压和磁盘占用继续增长；
  * **锁文件**：渲染一批需要近一小时，必须避免上一轮尚未结束就再启动一轮；
  * **全部委托给 cli.py**：批量流程、清单续跑和幂等发布都已在其中实现，
    此处只负责编排，不重复实现任何逻辑。

用法：python scripts/auto_daily.py [--count N] [--keyword "fishing tips"] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

from app.services import youtube_stats  # noqa: E402
from app.utils import utils  # noqa: E402

LOCK_PATH = os.path.join(ROOT, "storage", "auto_daily.lock")
DEFAULT_KEYWORD = "fishing tips"

# 与批量界面保持一致的成片参数：美式音色、Shorts 安全字幕位置、粗体字体。
BATCH_FLAGS = [
    "--video-aspect", "9:16",
    "--random-voice", "en-US",
    "--subtitle-position", "custom",
    "--custom-position", "60",
    "--font-size", "80",
    "--stroke-width", "2.5",
    "--font-name", "BeVietnamPro-Bold.ttf",
    "--bgm-dir", os.path.expanduser("~/Desktop/Music"),
    "--publish",
    "--youtube-privacy", "public",
    "--delete-after-upload",
]


def log(message: str) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"{stamp}  {message}", flush=True)


def acquire_lock() -> bool:
    """用 O_EXCL 创建锁文件；已存在但进程已退出时视为陈旧锁并接管。"""
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        try:
            with open(LOCK_PATH, "r", encoding="utf-8") as handle:
                pid = int(handle.read().strip() or 0)
            os.kill(pid, 0)
        except (OSError, ValueError):
            log(f"stale lock from a dead process, taking over: {LOCK_PATH}")
            os.unlink(LOCK_PATH)
            return acquire_lock()
        log(f"another run is still active (pid {pid}); exiting")
        return False

    with os.fdopen(fd, "w") as handle:
        handle.write(str(os.getpid()))
    return True


def release_lock() -> None:
    try:
        os.unlink(LOCK_PATH)
    except OSError:
        pass


def backlog_size() -> int:
    """统计已渲染但尚未发布的视频数量。"""
    root = utils.storage_dir("batches", create=True)
    total = 0
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name, "manifest.json")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                manifest = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        for item in manifest.get("items", []):
            if item.get("videos") and not (item.get("youtube") or {}).get("video_id"):
                total += 1
    return total


def pending_manifests() -> list[str]:
    """返回仍有"已渲染但未发布"条目的清单路径。"""
    root = utils.storage_dir("batches", create=True)
    pending = []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name, "manifest.json")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                manifest = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        for item in manifest.get("items", []):
            if item.get("videos") and not (item.get("youtube") or {}).get("video_id"):
                pending.append(path)
                break
    return pending


def run_cli(args: list[str], dry_run: bool) -> int:
    command = [sys.executable, os.path.join(ROOT, "cli.py"), *args]
    log("run: " + " ".join(command[2:]))
    if dry_run:
        return 0
    return subprocess.run(command, cwd=ROOT).returncode


def flush_backlog(dry_run: bool, publish_limit: int | None = None) -> int:
    """
    发布已渲染的积压视频，返回本轮发布的数量上限消耗情况。

    ``publish_limit`` 用于分批发布：Shorts 的冷启动人群很小，同一时刻集中上传
    会让这些视频互相争夺同一批测试观众，因此按批次、间隔数小时发布更有利。
    """
    manifests = pending_manifests()
    if not manifests:
        log("no backlog to publish")
        return 0

    remaining = publish_limit
    log(f"backlog in {len(manifests)} manifest(s); publishing"
        + (f" up to {publish_limit}" if publish_limit else " all"))
    for path in manifests:
        if remaining is not None and remaining <= 0:
            break
        args = ["--batch-manifest", path, "--publish"]
        if remaining is not None:
            args += ["--publish-limit", str(remaining)]
        before = backlog_size()
        run_cli(args, dry_run)
        if remaining is not None and not dry_run:
            remaining -= max(0, before - backlog_size())
    return (publish_limit - remaining) if publish_limit and remaining is not None else 0


def build_topics(count: int, keyword: str) -> list[str]:
    """
    以同领域近期热门视频为参考生成新选题。

    自有视频的播放数据在发布初期主要反映推荐系统的曝光波动，样本不足时
    不作为选题依据；外部热门视频是更可靠的输入。
    """
    reference: list[str] = []
    try:
        rows = youtube_stats.search_niche(keyword, days=30, max_results=25)
        reference = [row["title"] for row in rows[:10]]
        log(f"niche research: {len(rows)} videos for {keyword!r}")
    except Exception as exc:
        log(f"niche research unavailable ({exc}); falling back to own history")

    winners: list[str] = []
    try:
        rows = youtube_stats.performance_rows()
        trustworthy, note = youtube_stats.has_meaningful_signal(rows)
        if trustworthy:
            winners = [
                r.get("subject") or r.get("title", "")
                for r in rows[:5] if r.get("views") is not None
            ]
        else:
            log(f"own stats not used as a signal yet: {note}")
    except Exception as exc:
        log(f"performance data unavailable ({exc})")

    topics = youtube_stats.suggest_topics(winners, reference, count=count)
    log(f"model produced {len(topics)} usable topics")

    # 模型的新想法会随着已做选题增多而枯竭；此时按鱼种套用固定句式兜底，
    # 保证日更不中断，同时仍然经过判重。
    if len(topics) < count:
        seen = youtube_stats.known_subjects() | {t.strip().lower() for t in topics}
        filler = youtube_stats.fallback_topics(count - len(topics), seen)
        if filler:
            log(f"topped up with {len(filler)} species-based topics")
        topics.extend(filler)

    log(f"generated {len(topics)} new topics")
    return topics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument(
        "--max-backlog",
        type=int,
        default=25,
        help=(
            "skip generating new topics while this many rendered videos are still "
            "waiting to upload. Uploads are capped by YouTube per channel, so "
            "rendering faster than that only grows the backlog and disk usage."
        ),
    )
    parser.add_argument("--keyword", default=DEFAULT_KEYWORD)
    parser.add_argument(
        "--mode",
        choices=["both", "render", "publish"],
        default="both",
        help=(
            "'render' generates and renders without uploading; 'publish' only "
            "uploads existing backlog. Splitting them lets rendering run in one "
            "batch while uploads are spread across the day"
        ),
    )
    parser.add_argument(
        "--publish-limit",
        type=int,
        default=None,
        help="upload at most this many videos in a publish run",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--until",
        default=None,
        metavar="YYYY-MM-DD",
        help=(
            "stop running after this date (inclusive). Unattended automation must "
            "expire on its own rather than depend on someone remembering to "
            "remove the schedule."
        ),
    )
    args = parser.parse_args()

    if args.until:
        try:
            deadline = datetime.strptime(args.until, "%Y-%m-%d").date()
        except ValueError:
            log(f"invalid --until value {args.until!r}; expected YYYY-MM-DD")
            return 2
        today = datetime.now().date()
        if today > deadline:
            log(f"past --until {deadline}; this schedule has expired, doing nothing")
            return 0

    if not acquire_lock():
        return 0
    try:
        log(
            f"=== auto_daily start (mode={args.mode}, count={args.count}, "
            f"keyword={args.keyword!r}) ==="
        )

        if args.mode in ("both", "publish"):
            flush_backlog(args.dry_run, args.publish_limit)
        if args.mode == "publish":
            log("=== auto_daily done (publish only) ===")
            return 0

        # 上传速度由 YouTube 的频道额度决定，渲染更快只会让积压和磁盘无限增长。
        # 积压超过阈值时本轮只发布、不生产，使产量自动收敛到真实可发布的速度。
        backlog = backlog_size()
        if backlog >= args.max_backlog:
            log(
                f"backlog is {backlog} (limit {args.max_backlog}); "
                "skipping new topics this run and letting uploads catch up"
            )
            return 0
        log(f"backlog is {backlog}/{args.max_backlog}; generating new topics")

        topics = build_topics(args.count, args.keyword)
        if not topics:
            log("no new topics generated; nothing further to do")
            return 0

        batch_dir = os.path.join(utils.storage_dir("batches", create=True), utils.get_uuid())
        os.makedirs(batch_dir, exist_ok=True)
        topics_path = os.path.join(batch_dir, "topics.txt")
        with open(topics_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(topics) + "\n")
        for topic in topics:
            log(f"  topic: {topic}")

        flags = list(BATCH_FLAGS)
        if args.mode == "render":
            # 渲染阶段不发布：成片留到分批发布任务里按节奏上传。
            for flag in ("--publish", "--delete-after-upload"):
                if flag in flags:
                    flags.remove(flag)
            privacy_index = flags.index("--youtube-privacy")
            del flags[privacy_index : privacy_index + 2]
            flags.append("--no-publish")

        run_cli(
            [
                "--subjects-file", topics_path,
                "--batch-manifest", os.path.join(batch_dir, "manifest.json"),
                *flags,
            ],
            args.dry_run,
        )
        log("=== auto_daily done ===")
        return 0
    finally:
        release_lock()


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
"""
每日健康检查与改进建议（凌晨运行，在渲染任务之后）。

存在的理由：2026-09-24 起渲染连续三晚失败，日志照常滚动、定时任务照常触发，
却没有任何地方报警，直到有人手动查看才发现已经三天没有产出。因此本脚本优先
检查"产出是否还在发生"，其次才给出优化建议。

任何检查失败都不会让脚本崩溃：监控程序自身必须比被监控的流程更难失败。
"""

from __future__ import annotations

import collections
import os
import statistics
import subprocess
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

LOG = os.path.join(ROOT, "storage", "auto_daily.log")
AGENTS = ("com.moneyprinterturbo.render", "com.moneyprinterturbo.publish")
# 每天的发布时段数量；积压低于这个数就无法填满当天的窗口。
EXPECTED_SLOTS = 20

problems: list[str] = []
notes: list[str] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def _tail(path: str, limit: int = 4000) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.readlines()[-limit:]
    except OSError:
        return []


def check_agents() -> None:
    try:
        listed = subprocess.run(
            ["launchctl", "list"], capture_output=True, text=True, timeout=20
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        problems.append(f"could not query launchctl: {exc}")
        return
    for label in AGENTS:
        if label not in listed:
            problems.append(f"scheduled job {label} is NOT loaded")


def check_production() -> int:
    """确认渲染仍在产出，并返回当前积压数量。"""
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "auto_daily", os.path.join(ROOT, "scripts", "auto_daily.py")
        )
        module = importlib.util.module_from_spec(spec)
        sys.argv = ["daily_health"]
        spec.loader.exec_module(module)
        backlog = module.backlog_size()
    except Exception as exc:
        problems.append(f"could not read the backlog: {exc}")
        return -1

    if backlog == 0:
        problems.append(
            "backlog is EMPTY — today's publish slots will have nothing to post"
        )
    elif backlog < EXPECTED_SLOTS:
        notes.append(
            f"backlog {backlog} is below the {EXPECTED_SLOTS} daily slots; "
            "some slots will idle"
        )
    return backlog


def check_recent_render() -> None:
    lines = _tail(LOG)
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")
    rendered = [
        line for line in lines
        if "mode=render" in line and (today in line or yesterday in line)
    ]
    if not rendered:
        problems.append("no render run found in the last 24h")
    # 回溯最近一次渲染之后是否出现异常堆栈
    for index in range(len(lines) - 1, -1, -1):
        if "mode=render" in lines[index]:
            tail = "".join(lines[index:])
            if "Traceback" in tail:
                first = next(
                    (ln.strip() for ln in tail.splitlines()
                     if ln.strip().startswith(("OSError", "ValueError", "RuntimeError",
                                               "KeyError", "TypeError", "Exception"))),
                    "see storage/auto_daily.log",
                )
                problems.append(f"last render run raised: {first}")
            break


def check_publishing() -> None:
    from app.services import youtube_stats

    try:
        rows = [r for r in youtube_stats.performance_rows() if r.get("published_at")]
    except Exception as exc:
        problems.append(f"could not read channel statistics: {exc}")
        return

    by_day: collections.Counter = collections.Counter()
    for row in rows:
        moment = datetime.fromisoformat(
            row["published_at"].replace("Z", "+00:00")
        ).astimezone()
        by_day[moment.date()] += 1

    today = datetime.now().date()
    recent = [(today - timedelta(days=n), by_day.get(today - timedelta(days=n), 0))
              for n in range(1, 4)]
    say("uploads, last 3 days:")
    for day, count in recent:
        say(f"  {day}  {count:3}  {'#' * min(count, 40)}")
    if all(count == 0 for _, count in recent):
        problems.append("NOTHING has published in the last 3 days")
    elif recent[0][1] == 0:
        notes.append("nothing published yesterday")


def check_disk() -> None:
    try:
        used = subprocess.run(
            ["du", "-sg", os.path.join(ROOT, "storage")],
            capture_output=True, text=True, timeout=120,
        ).stdout.split()[0]
        free = subprocess.run(
            ["df", "-g", "/"], capture_output=True, text=True, timeout=20
        ).stdout.splitlines()[1].split()[3]
    except (OSError, subprocess.SubprocessError, IndexError, ValueError) as exc:
        notes.append(f"could not measure disk: {exc}")
        return
    say(f"\ndisk: storage {used} GB used, {free} GB free")
    try:
        if int(free) < 15:
            problems.append(f"only {free} GB free on disk")
    except ValueError:
        pass


def prune_cache(max_age_days: int = 7) -> None:
    """
    清理素材缓存。缓存用于跨视频复用素材，但不会自动回收：一次观察到它涨到
    40 GB。磁盘写满同样会让整条流水线静默停摆，因此放在每日检查里一并处理。
    """
    try:
        from app.services import cache_manager

        result = cache_manager.clean_video_cache(max_age_days=max_age_days)
    except Exception as exc:
        notes.append(f"could not prune the material cache: {exc}")
        return
    if result.deleted_count:
        say(
            f"pruned material cache: removed {result.deleted_count} files, "
            f"{result.deleted_size / 1024 ** 3:.1f} GB freed"
            + (f", {result.failed_count} failed" if result.failed_count else "")
        )


def opportunities() -> None:
    """按时段找出明显拖后腿的发布位，给出可执行的调整建议。"""
    from app.services import youtube_stats

    try:
        rows = []
        for row in youtube_stats.performance_rows():
            if row.get("views") is None or (row.get("age_days") or 0) < 2:
                continue
            moment = datetime.fromisoformat(
                row["published_at"].replace("Z", "+00:00")
            ).astimezone()
            if (datetime.now().astimezone() - moment).days <= 14:
                rows.append({**row, "hour": moment.hour, "day": moment.date()})
    except Exception as exc:
        notes.append(f"could not analyse performance: {exc}")
        return
    if len(rows) < 20:
        return

    baseline = statistics.median(r["views"] for r in rows) or 1
    say(f"\nlast 14 days: {len(rows)} videos, median {baseline:.0f} views")

    weeks: collections.defaultdict = collections.defaultdict(list)
    for row in rows:
        weeks[row["day"].isocalendar()[1]].append(row["views"])
    ordered = sorted(weeks)
    if len(ordered) >= 2:
        prev, curr = ordered[-2], ordered[-1]
        a = sum(weeks[prev]) / len(weeks[prev])
        b = sum(weeks[curr]) / len(weeks[curr])
        say(f"views/video: week {prev} {a:.0f} -> week {curr} {b:.0f}")
        if b < a * 0.7:
            notes.append(
                f"views per video fell {(1 - b / a) * 100:.0f}% week over week"
            )

    by_hour: collections.defaultdict = collections.defaultdict(list)
    for row in rows:
        by_hour[row["hour"]].append(row["views"])
    weak = [
        (hour, statistics.median(vals), len(vals))
        for hour, vals in by_hour.items()
        if len(vals) >= 8 and statistics.median(vals) < baseline * 0.5
    ]
    for hour, median, count in sorted(weak, key=lambda item: item[1]):
        notes.append(
            f"{hour:02}:00 slot is weak: median {median:.0f} vs {baseline:.0f} "
            f"baseline over {count} videos — consider dropping it"
        )


def ab_status() -> None:
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "retention_report", os.path.join(ROOT, "scripts", "retention_report.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        say("")
        module._length_ab(module.fetch_retention())
    except SystemExit as exc:
        notes.append(f"retention unavailable: {exc}")
    except Exception as exc:
        notes.append(f"retention unavailable: {exc}")


def notify(title: str, message: str) -> None:
    """失败时发系统通知：静默失败三天正是这个脚本要解决的问题。"""
    safe = message.replace('"', "'")[:200]
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{safe}" with title "{title}"'],
            capture_output=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def main() -> int:
    say(f"===== daily health check {datetime.now():%Y-%m-%d %H:%M} =====")
    check_agents()
    backlog = check_production()
    check_recent_render()
    check_publishing()
    say(f"\nbacklog: {backlog} videos ready to publish")
    prune_cache()
    check_disk()
    opportunities()
    ab_status()

    if problems:
        say("\n!! PROBLEMS")
        for item in problems:
            say(f"  - {item}")
        notify("MoneyPrinterTurbo: needs attention", problems[0])
    else:
        say("\nno problems found")
    if notes:
        say("\nsuggestions")
        for item in notes:
            say(f"  - {item}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())

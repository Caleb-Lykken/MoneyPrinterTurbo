"""
YouTube 数据读取：自有视频表现、同领域热门视频，以及基于表现的选题生成。

与 ``app/services/youtube.py`` 的上传职责分开：这里只做读取和分析，需要
``youtube.readonly`` 权限。

配额说明（默认额度）：
  * ``videos.list``  每次 1 单位，可一次查询 50 个视频，日额度 10000 单位；
  * ``search.list``  每天 100 次调用，因此同领域调研属于低频操作。

关于数据量的重要前提：新发布视频的播放量在最初几天由推荐系统的曝光波动
主导，样本很小时"表现好"和"运气好"无法区分。本模块提供
``has_meaningful_signal`` 用于在界面上明确提示这一点，而不是让用户把噪声
当成结论。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone

from loguru import logger

from app.utils import utils

# 低于这些门槛时，排名基本反映随机波动而非选题质量。
MIN_VIDEOS_FOR_SIGNAL = 20
MIN_AGE_DAYS_FOR_SIGNAL = 14

_MAX_IDS_PER_REQUEST = 50


class YouTubeStatsError(Exception):
    """读取 YouTube 数据失败。"""


def _service():
    from app.services import youtube

    modules = youtube._google_modules()
    credentials = youtube._load_credentials()
    if credentials is None:
        raise YouTubeStatsError(
            f"YouTube account is not authorized. Run: {youtube.auth_command()}"
        )

    # 早于 API 调用给出准确原因：仅有上传权限的旧 token 调用读取接口会返回
    # 一个含义模糊的 403，用户很难据此知道要重新授权。
    if youtube.missing_scopes([youtube.READONLY_SCOPE]):
        raise YouTubeStatsError(
            "reading statistics needs the youtube.readonly scope, which the "
            "stored token does not have. Grant it by re-running: "
            f"{youtube.auth_command()}"
        )
    return modules, modules["build"](
        "youtube", "v3", credentials=credentials, cache_discovery=False
    )


def _chunked(values: list, size: int):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def collect_published_videos() -> list[dict]:
    """
    扫描所有批次清单，汇总已经发布的视频。

    清单是发布结果的唯一可靠来源：本地成片可能已经在上传后被删除，
    但 video_id 与主题始终保留在清单里。
    """
    root = utils.storage_dir("batches", create=True)
    published: list[dict] = []
    for name in sorted(os.listdir(root)):
        manifest_path = os.path.join(root, name, "manifest.json")
        if not os.path.isfile(manifest_path):
            continue
        try:
            with open(manifest_path, "r", encoding="utf-8") as handle:
                manifest = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue

        for item in manifest.get("items", []):
            video_id = (item.get("youtube") or {}).get("video_id")
            if not video_id:
                continue
            published.append(
                {
                    "video_id": video_id,
                    "subject": item.get("subject", ""),
                    "batch_id": manifest.get("batch_id"),
                    "url": (item.get("youtube") or {}).get("url"),
                }
            )
    return published


def fetch_video_stats(video_ids: list[str]) -> dict[str, dict]:
    """批量读取视频统计数据，返回 ``{video_id: {...}}``。"""
    if not video_ids:
        return {}

    modules, service = _service()
    stats: dict[str, dict] = {}
    for chunk in _chunked(list(dict.fromkeys(video_ids)), _MAX_IDS_PER_REQUEST):
        try:
            response = service.videos().list(
                part="snippet,statistics,contentDetails", id=",".join(chunk)
            ).execute()
        except modules["HttpError"] as exc:
            raise YouTubeStatsError(f"failed to read video statistics: {exc}") from exc

        for entry in response.get("items", []):
            statistics = entry.get("statistics", {})
            snippet = entry.get("snippet", {})
            stats[entry["id"]] = {
                "video_id": entry["id"],
                "title": snippet.get("title", ""),
                "published_at": snippet.get("publishedAt", ""),
                "views": int(statistics.get("viewCount", 0) or 0),
                "likes": int(statistics.get("likeCount", 0) or 0),
                "comments": int(statistics.get("commentCount", 0) or 0),
            }
    return stats


def video_age_days(published_at: str) -> float | None:
    if not published_at:
        return None
    try:
        published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - published).total_seconds() / 86400


def has_meaningful_signal(rows: list[dict]) -> tuple[bool, str]:
    """
    判断当前数据量是否足以支撑"哪个选题更好"的结论。

    返回 ``(是否可信, 说明)``。界面据此提示用户，而不是直接隐藏数据。
    """
    if len(rows) < MIN_VIDEOS_FOR_SIGNAL:
        return False, (
            f"Only {len(rows)} published video(s). Differences in view count at "
            f"this sample size are mostly noise — around {MIN_VIDEOS_FOR_SIGNAL} "
            "videos are needed before rankings mean much."
        )

    ages = [video_age_days(row.get("published_at", "")) or 0 for row in rows]
    oldest = max(ages) if ages else 0
    if oldest < MIN_AGE_DAYS_FOR_SIGNAL:
        return False, (
            f"The oldest video is {oldest:.0f} day(s) old. Early view counts are "
            "dominated by how YouTube happens to test each video, so wait about "
            f"{MIN_AGE_DAYS_FOR_SIGNAL} days before drawing conclusions."
        )
    return True, ""


def performance_rows() -> list[dict]:
    """把自有视频与其统计数据合并成可直接展示的行，按播放量降序。"""
    published = collect_published_videos()
    stats = fetch_video_stats([row["video_id"] for row in published])

    rows = []
    for row in published:
        entry = stats.get(row["video_id"])
        if not entry:
            # 视频可能已被删除，或统计数据尚未生成。
            rows.append({**row, "views": None, "likes": None, "comments": None})
            continue
        age = video_age_days(entry["published_at"])
        rows.append(
            {
                **row,
                **entry,
                "age_days": age,
                # 按天归一化，避免老视频仅因为发布更早而排在前面。
                "views_per_day": (entry["views"] / age) if age and age >= 1 else None,
            }
        )
    rows.sort(key=lambda item: (item.get("views") or -1), reverse=True)
    return rows


def search_niche(query: str, days: int = 30, max_results: int = 25) -> list[dict]:
    """
    查找同领域近期播放量最高的视频。

    search.list 每天仅有 100 次调用额度，因此这是低频调研入口，不适合轮询。
    """
    if not query.strip():
        raise YouTubeStatsError("a search keyword is required")

    modules, service = _service()
    published_after = (
        datetime.now(timezone.utc) - timedelta(days=max(1, days))
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        response = service.search().list(
            part="snippet",
            q=query.strip(),
            type="video",
            order="viewCount",
            publishedAfter=published_after,
            maxResults=min(50, max(1, max_results)),
        ).execute()
    except modules["HttpError"] as exc:
        raise YouTubeStatsError(f"niche search failed: {exc}") from exc

    ids = [
        entry["id"]["videoId"]
        for entry in response.get("items", [])
        if entry.get("id", {}).get("videoId")
    ]
    stats = fetch_video_stats(ids)

    rows = []
    for video_id in ids:
        entry = stats.get(video_id)
        if not entry:
            continue
        age = video_age_days(entry["published_at"])
        rows.append(
            {
                **entry,
                "url": f"https://youtu.be/{video_id}",
                "age_days": age,
                "views_per_day": (entry["views"] / age) if age and age >= 1 else None,
            }
        )
    rows.sort(key=lambda item: item["views"], reverse=True)
    return rows


# 判重时忽略的高频虚词，避免"how/the/for"之类词汇拉高相似度。
_DEDUPE_STOPWORDS = frozenset({
    "the", "a", "an", "to", "for", "of", "in", "on", "and", "or", "how", "why",
    "what", "where", "when", "your", "you", "that", "this", "these", "is", "are",
    "do", "does", "it", "with", "not", "can", "from", "more", "than", "most",
    "best", "actually", "really", "about", "into", "out", "up", "down", "at",
})
# 经验阈值：0.5 能挡住"why fish feed before a storm"与"why bass feed before a
# storm"这类改写，同时保留同一主题下真正不同的角度。
_DEDUPE_SIMILARITY = 0.5


def _significant_tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if w not in _DEDUPE_STOPWORDS and len(w) > 2}


def is_near_duplicate(topic: str, existing: set[str]) -> bool:
    """
    判断选题是否与已有选题实质重复。

    仅比较字符串相等无法拦截改写：同一个想法换一个主语就会被重复制作，
    既浪费额度，也会污染后续"哪个选题更好"的判断。
    """
    tokens = _significant_tokens(topic)
    if not tokens:
        return False
    for other in existing:
        other_tokens = _significant_tokens(other)
        if not other_tokens:
            continue
        overlap = len(tokens & other_tokens) / len(tokens | other_tokens)
        if overlap >= _DEDUPE_SIMILARITY:
            return True
    return False


# 选题耗尽时的兜底：按鱼种套用固定句式，组合数量足以支撑长期日更。
FALLBACK_SPECIES = (
    "largemouth bass", "smallmouth bass", "striped bass", "rainbow trout",
    "brown trout", "brook trout", "steelhead", "channel catfish", "flathead catfish",
    "blue catfish", "walleye", "northern pike", "muskie", "yellow perch", "crappie",
    "bluegill", "carp", "grass carp", "chinook salmon", "coho salmon", "sockeye salmon",
    "lake trout", "sturgeon", "gar", "bowfin", "redfish", "snook", "tarpon",
    "bonefish", "permit", "flounder", "halibut", "cod", "haddock", "mackerel",
    "bluefin tuna", "yellowfin tuna", "mahi mahi", "red snapper", "grouper",
    "sheepshead", "black drum", "speckled trout", "barracuda", "amberjack",
    "wahoo", "sailfish", "swordfish", "shad", "white bass", "peacock bass",
    "arapaima", "snakehead", "tilapia", "burbot", "whitefish", "grayling",
    "pompano", "cobia", "triggerfish",
)

FALLBACK_TEMPLATES = (
    "How fishermen catch {species}",
    "How to fish for {species}: gear, bait and timing",
    "Where {species} hide and how to find them",
    "What {species} eat and how to match it",
    "The best time of year to fish for {species}",
)


def fallback_topics(count: int, existing: set[str] | None = None) -> list[str]:
    """
    按鱼种套用固定句式生成选题，用于模型给不出新想法时兜底。

    组合按鱼种优先展开，保证先覆盖不同鱼种，而不是把同一条鱼写满五遍。
    """
    # 空集合是 falsy：写成 `existing or known_subjects()` 会让显式传入的空基准
    # 被悄悄替换成全部历史选题，判重范围与调用方意图不符。
    baseline = set(existing if existing is not None else known_subjects())
    # 相似度判重只针对已有选题。同一句式换鱼种后重合度天然很高
    # （largemouth bass 与 smallmouth bass 达 0.6），若把已生成的兜底选题也纳入
    # 比较，会把绝大多数鱼种误判为重复，兜底池随即枯竭。
    generated_keys: set[str] = set()
    generated: list[str] = []
    for template in FALLBACK_TEMPLATES:
        for species in FALLBACK_SPECIES:
            if len(generated) >= count:
                return generated
            topic = template.format(species=species)
            key = topic.strip().lower()
            if key in baseline or key in generated_keys:
                continue
            if is_near_duplicate(topic, baseline):
                continue
            generated_keys.add(key)
            generated.append(topic)
    return generated


def known_subjects() -> set[str]:
    """已经做过的选题，用于去重，避免重复生成同一个视频。"""
    root = utils.storage_dir("batches", create=True)
    subjects: set[str] = set()
    for name in sorted(os.listdir(root)):
        manifest_path = os.path.join(root, name, "manifest.json")
        if not os.path.isfile(manifest_path):
            continue
        try:
            with open(manifest_path, "r", encoding="utf-8") as handle:
                manifest = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        for item in manifest.get("items", []):
            subject = (item.get("subject") or "").strip().lower()
            if subject:
                subjects.add(subject)
    return subjects


def suggest_topics(
    winners: list[str],
    reference_titles: list[str] | None = None,
    count: int = 10,
) -> list[str]:
    """
    以表现最好的选题为参考，生成新的选题。

    去重按已做过的选题进行，避免反复产出同一个视频。生成失败时返回空列表，
    由调用方决定如何提示，不抛出异常打断界面。
    """
    from app.services import llm

    if not winners and not reference_titles:
        return []

    winner_block = "\n".join(f"- {item}" for item in winners[:10])
    reference_block = "\n".join(f"- {item}" for item in (reference_titles or [])[:10])

    prompt = f"""
# Role: YouTube Shorts topic strategist

## Goal
Propose {count} new short-video topics that follow the same patterns as the
topics below, which performed well.

## Topics that performed well
{winner_block or "(none provided)"}

## Popular videos in this niche, for reference
{reference_block or "(none provided)"}

## Constraints
1. Respond ONLY with a single valid minified JSON array of strings.
2. Exactly {count} items.
3. Each item is a concrete video topic, at most 80 characters, in English.
4. Do not repeat any topic listed above, and do not restate the same idea twice.
5. Prefer specific, curiosity-driven angles over generic category names.
6. CRITICAL: the finished videos use generic stock footage and an AI narrator.
   Never propose a topic that promises specific recorded events or first-person
   experience, such as "I tested...", "I left a camera...", "Watch what happened
   when...", or "caught on camera". Every topic must be explainable with general
   footage. Write informational or explanatory angles instead.

## Output example
["Why bass strike lures at dawn","The knot that never slips on braid"]

## Rejected examples, for contrast
"I Tested Every Lure at Walmart" (first-person claim), "I Left a GoPro Overnight
- Here's What Showed Up" (promises footage), "Caught on Camera: Shark Steals
Catch" (promises footage).
""".strip()

    try:
        response = llm._generate_response(prompt)
    except Exception as exc:
        logger.warning(f"topic suggestion failed: {exc}")
        return []

    if not response or "Error: " in str(response):
        logger.warning(f"topic suggestion returned an error: {response}")
        return []

    topics = _parse_topic_list(response)
    seen = known_subjects()
    unique = []
    for topic in topics:
        cleaned = topic.strip()
        if cleaned.lower() in seen:
            continue
        if promises_footage(cleaned):
            logger.warning(f"discarding topic that promises footage: {cleaned!r}")
            continue
        if is_near_duplicate(cleaned, seen):
            logger.warning(f"discarding near-duplicate topic: {cleaned!r}")
            continue
        seen.add(cleaned.lower())
        unique.append(cleaned)
    return unique[:count]


# 成片使用通用素材和 AI 解说，无法兑现"我拍到了什么"这类承诺。
# 提示词已作约束，这里再做一次确定性过滤，避免无人值守时把这类选题放进队列。
_FOOTAGE_CLAIM_PATTERNS = (
    "here's what happened",
    "heres what happened",
    "here's what showed up",
    "heres what showed up",
    "caught on camera",
    "watch what happens",
    "watch me",
    "i tested",
    "i tried",
    "i left",
    "i fished",
    "i caught",
    "i spent",
    "i bought",
    "we tested",
    "we tried",
    "gopro",
    "went wrong",
    "gone wrong",
)


def promises_footage(topic: str) -> bool:
    """判断选题是否承诺了实际不存在的画面或第一人称经历。"""
    text = (topic or "").strip().lower()
    if not text:
        return False
    if text.startswith(("i ", "i'm ", "my ", "we ")):
        return True
    return any(pattern in text for pattern in _FOOTAGE_CLAIM_PATTERNS)


def _parse_topic_list(response: str) -> list[str]:
    """从模型输出中提取选题数组，容忍代码块包裹和多余文本。"""
    text = str(response).strip()
    if "```" in text:
        parts = [part for part in text.split("```") if part.strip()]
        for part in parts:
            cleaned = part.strip()
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()
            if cleaned.startswith("["):
                text = cleaned
                break

    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []

    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []

    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]

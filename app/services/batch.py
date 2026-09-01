"""
批量生成：一次输入多个主题，先统一生成文案，再逐条渲染并发布。

分两个阶段执行：

  1. 文案阶段：为每个主题调用一次 LLM，把结果写入批次清单（manifest）。
  2. 渲染阶段：逐个主题复用完整流水线渲染成片，成功后按需上传 YouTube。

清单在每次状态变化后原子落盘，因此中断（Ctrl-C、崩溃、断电）后重新指定
``--batch-manifest`` 即可续跑：已完成的主题会被跳过，失败的主题会重试。

单个主题失败只影响它自己，批次会继续处理后续主题，并在结束时汇总结果。
"""

from __future__ import annotations

import filecmp
import hashlib
import json
import os
import random
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

# 主题为空、行首为 # 的注释行都会被忽略，方便直接维护一个主题清单文件。
_COMMENT_PREFIX = "#"
_SLUG_INVALID_RE = re.compile(r"[^\w]+", re.UNICODE)
_MAX_SLUG_LENGTH = 60

# 必须与 cli._PIPELINE_STAGES 以及 task._run_pipeline 的阶段顺序保持一致，
# test_batch.py 中有用例校验两者不会漂移。
_STAGE_ORDER = ("script", "terms", "audio", "subtitle", "materials", "video")

STATUS_PENDING = "pending"
STATUS_SCRIPT_FAILED = "script_failed"
STATUS_RENDERING = "rendering"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

_RETRYABLE_STATUSES = (STATUS_PENDING, STATUS_SCRIPT_FAILED, STATUS_RENDERING, STATUS_FAILED)


class BatchInputError(Exception):
    """批次输入或清单本身不可用，属于调用方可修复的错误。"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_subjects(path: str) -> list[str]:
    """读取主题清单文件，忽略空行与注释行。"""
    try:
        with open(path, "r", encoding="utf-8") as subjects_file:
            raw_lines = subjects_file.readlines()
    except OSError as exc:
        raise BatchInputError(f"failed to read subjects file: {exc}") from exc

    subjects = []
    for line in raw_lines:
        subject = line.strip()
        if not subject or subject.startswith(_COMMENT_PREFIX):
            continue
        subjects.append(subject)

    if not subjects:
        raise BatchInputError(f"subjects file contains no topics: {path}")
    return subjects


def prepare_bgm_tracks(bgm_dir: str) -> list[str]:
    """
    把用户目录中的背景音乐复制进受管 BGM 目录，返回可直接引用的文件名。

    渲染时 ``video.get_bgm_file`` 只接受 ``storage/bgm`` 和 ``resource/songs``
    内的文件，因此外部目录必须先复制进来 —— 这与 ``prepare_cli_files`` 处理
    本地素材的做法一致。内容相同的文件不会重复复制。
    """
    from app.services import bgm as bgm_service

    directory = os.path.abspath(os.path.expanduser(bgm_dir.strip()))
    if not os.path.isdir(directory):
        raise BatchInputError(f"background music directory does not exist: {bgm_dir}")

    managed_dir = bgm_service.uploaded_bgm_dir(create=True)
    tracks: list[str] = []
    for name in sorted(os.listdir(directory), key=str.lower):
        source = os.path.join(directory, name)
        if not os.path.isfile(source):
            continue
        if Path(name).suffix.lower() not in bgm_service.SUPPORTED_BGM_EXTENSIONS:
            continue

        try:
            safe_name = bgm_service.sanitize_upload_filename(name)
        except bgm_service.BgmUploadError as exc:
            logger.warning(f"skip background music file {name!r}: {exc}")
            continue

        target = os.path.join(managed_dir, safe_name)
        if os.path.isfile(target) and filecmp.cmp(source, target, shallow=False):
            # 已经复制过同一份文件，直接复用，避免每次运行都产生新副本。
            tracks.append(safe_name)
            continue

        if os.path.isfile(target):
            # 同名但内容不同：加内容指纹后缀，不覆盖既有曲目。
            digest = hashlib.sha1(
                open(source, "rb").read()  # noqa: SIM115 - 指纹只需一次性读取
            ).hexdigest()[:8]
            stem, extension = os.path.splitext(safe_name)
            safe_name = f"{stem}-{digest}{extension}"
            target = os.path.join(managed_dir, safe_name)

        if not os.path.isfile(target):
            shutil.copy2(source, target)
            logger.info(
                f"copied background music into managed storage: {source} -> {target}"
            )
        tracks.append(safe_name)

    if not tracks:
        raise BatchInputError(
            f"no supported background music files in {directory} "
            f"(supported: {', '.join(bgm_service.SUPPORTED_BGM_EXTENSIONS)})"
        )
    return tracks


def _bgm_track_for(manifest: dict, item: dict) -> str | None:
    """
    为该主题选定背景音乐，按顺序轮换。

    选中的曲目写回清单：续跑时同一个主题必须拿到同一首曲子，否则重跑会
    产出与之前不一致的成片。
    """
    recorded = item.get("bgm_file")
    if recorded:
        return recorded

    tracks = manifest.get("bgm_tracks") or []
    if not tracks:
        return None
    return tracks[(int(item.get("index", 1)) - 1) % len(tracks)]


# Ana 是儿童音色，与大多数解说类内容不搭，随机池中默认排除。
_EXCLUDED_VOICES = ("en-US-AnaNeural",)


def prepare_voice_pool(locale: str) -> list[str]:
    """
    返回某个语言区域中可直接使用的配音音色。

    排除两类：需要 Azure V2 密钥的音色（未配置密钥时会直接失败），以及明显
    不适合解说的儿童音色。
    """
    from app.services import voice as voice_service

    normalized = (locale or "").strip()
    if not normalized:
        raise BatchInputError("a voice locale is required")

    available = voice_service.get_all_azure_voices(filter_locals=[normalized])
    usable = [
        name
        for name in available
        # V2 音色需要 azure_speech_key，未配置时整条流水线会在配音阶段失败。
        if not voice_service.is_azure_v2_voice(name)
        and not any(name.startswith(excluded) for excluded in _EXCLUDED_VOICES)
    ]
    if not usable:
        raise BatchInputError(
            f"no usable voices found for locale {normalized!r}"
        )
    return usable


def _voice_for(manifest: dict, item: dict) -> str | None:
    """
    为该主题随机选定配音音色。

    选中的音色写回清单：续跑时同一个主题必须使用同一个音色，否则重跑会得到
    与之前不一致的成片。
    """
    recorded = item.get("voice_name")
    if recorded:
        return recorded

    pool = manifest.get("voice_pool") or []
    if not pool:
        return None
    return random.choice(pool)


def _slug_for_subject(subject: str, index: int) -> str:
    """
    生成可读的输出文件名前缀。

    序号前缀同时解决了两个问题：不同主题清理后可能得到相同的名字，以及
    Windows 保留设备名（CON、NUL 等）不能作为文件名。
    """
    slug = _SLUG_INVALID_RE.sub("-", subject or "").strip("-")
    slug = slug[:_MAX_SLUG_LENGTH].strip("-")
    return f"{index:02d}-{slug}" if slug else f"{index:02d}-video"


def _manifest_path(args) -> str:
    from app.utils import utils

    if args.batch_manifest:
        return os.path.abspath(os.path.expanduser(args.batch_manifest))

    batch_id = utils.get_uuid()
    batch_dir = os.path.join(utils.storage_dir("batches", create=True), batch_id)
    # 目录留给 _save_manifest 首次写入时创建：发布预检等早期失败不应留下空目录。
    return os.path.join(batch_dir, "manifest.json")


def _load_manifest(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as manifest_file:
            manifest = json.load(manifest_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise BatchInputError(f"failed to read batch manifest {path}: {exc}") from exc

    if not isinstance(manifest, dict) or not isinstance(manifest.get("items"), list):
        raise BatchInputError(f"batch manifest is not a valid batch file: {path}")
    return manifest


def _save_manifest(path: str, manifest: dict) -> None:
    """原子写入清单，保证任何时刻中断都能留下可续跑的文件。"""
    from app.services import task_artifacts

    manifest["updated_at"] = _utc_now()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    task_artifacts.write_json_atomic(Path(path), manifest)


def _new_manifest(
    subjects: list[str],
    base_params,
    stop_at: str,
    bgm_tracks: list[str] | None = None,
    voice_pool: list[str] | None = None,
) -> dict:
    from app.utils import utils

    # 主题和文案逐条存放在 items 里，公共参数只保存一份，避免清单冗余。
    # VideoParams 的部分枚举字段默认保存的是取值字符串而非枚举成员，
    # 序列化告警属于既有行为且不影响回读，这里关闭以免污染批次日志。
    params_payload = base_params.model_dump(mode="json", warnings=False)
    params_payload["video_subject"] = ""
    params_payload["video_script"] = ""

    return {
        "batch_id": utils.get_uuid(),
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "stop_at": stop_at,
        "bgm_tracks": list(bgm_tracks or []),
        "voice_pool": list(voice_pool or []),
        "base_params": params_payload,
        "items": [
            {
                "index": index,
                "subject": subject,
                "script": "",
                "task_id": None,
                "status": STATUS_PENDING,
                "videos": [],
                "failed_stage": None,
                "error": None,
                "completed_stage": None,
                "bgm_file": None,
                "voice_name": None,
                "youtube": None,
                "local_cleanup": None,
            }
            for index, subject in enumerate(subjects, start=1)
        ],
    }


def _script_error(script) -> str | None:
    """
    识别文案生成失败。

    ``llm.generate_script`` 用返回值内的 ``"Error: "`` 传递错误，与
    ``task._run_pipeline`` 的判断方式保持一致。
    """
    if not script or not str(script).strip():
        return "failed to generate video script"
    if isinstance(script, str) and "Error: " in script:
        return script.removeprefix("Error: ").strip() or "failed to generate video script"
    return None


def _resolve_publishing(args) -> tuple[bool, str]:
    """确定本次批次是否发布，以及使用的隐私级别。"""
    from app.services import youtube

    # --publish / --no-publish 显式覆盖配置；未指定时按配置决定。
    publish_flag = getattr(args, "publish", None)
    if publish_flag is False:
        return False, ""

    should_publish = True if publish_flag else youtube.is_enabled()
    if not should_publish:
        return False, ""

    privacy = youtube.resolve_privacy_status(
        getattr(args, "youtube_privacy", None) or youtube.configured_privacy_status()
    )
    return True, privacy


def _collect_output(
    video_path: str, output_dir: str, name: str, prefer_copy: bool = False
) -> str | None:
    """
    把成片汇总到易读的输出目录。

    默认优先硬链接以避免复制大文件；跨设备或文件系统不支持时回退为复制。
    开启上传后删除时必须真正复制：硬链接与原文件共用 inode，删除任务目录
    并不会释放空间，而且这份副本本来就是要留下来的。
    """
    try:
        os.makedirs(output_dir, exist_ok=True)
        extension = os.path.splitext(video_path)[1] or ".mp4"
        target = os.path.join(output_dir, f"{name}{extension}")
        if os.path.exists(target):
            os.remove(target)
        if prefer_copy:
            shutil.copy2(video_path, target)
        else:
            try:
                os.link(video_path, target)
            except (OSError, NotImplementedError, AttributeError):
                shutil.copy2(video_path, target)
        return target
    except OSError as exc:
        logger.warning(f"failed to collect output for {video_path}: {exc}")
        return None


def _publish_item(item: dict, params, privacy_status: str) -> bool:
    """
    上传该主题的成片，返回是否因为配额耗尽需要停止后续上传。

    发布失败不会推翻已经渲染成功的结果：视频文件仍然保留，错误记录在清单里，
    续跑时可以只重试发布。
    """
    from app.config import config
    from app.services import youtube

    # 已经有 video_id 说明上次运行已经发布过。续跑时必须跳过，否则会在频道上
    # 产生重复视频 —— 这是整个批次流程里最不可逆的副作用。
    existing = item.get("youtube") or {}
    if existing.get("video_id"):
        logger.info(
            f"[{item['index']}] already published, skipping upload: {existing.get('url')}"
        )
        return False

    video_paths = item.get("videos") or []
    if not video_paths:
        return False

    # video_count > 1 时同一份文案会渲染出多个版本，全部上传只会在频道上制造
    # 近似重复的内容，因此只发布第一个，并明确记录被跳过的数量。
    if len(video_paths) > 1:
        logger.info(
            f"[{item['index']}] uploading the first of {len(video_paths)} rendered "
            "videos; the remaining variants are kept on disk only"
        )

    try:
        metadata = youtube.build_metadata(
            video_subject=item.get("subject", ""),
            video_script=item.get("script", ""),
            language=getattr(params, "video_language", "") or "",
            extra_tags=list(config.app.get("youtube_default_tags", []) or []),
        )
        result = youtube.upload_video(
            video_paths[0],
            title=metadata["title"],
            description=metadata["description"],
            tags=metadata["tags"],
            privacy_status=privacy_status,
            category_id=config.app.get("youtube_category_id", None),
            made_for_kids=bool(config.app.get("youtube_made_for_kids", False)),
        )
    except youtube.YouTubeError as exc:
        logger.error(f"[{item['index']}] YouTube upload failed: {exc}")
        item["youtube"] = {"error": str(exc)}
        return bool(exc.quota_exceeded)
    except Exception as exc:
        logger.exception(f"[{item['index']}] unexpected YouTube upload error: {exc}")
        item["youtube"] = {"error": str(exc)}
        return False

    item["youtube"] = result
    logger.info(f"[{item['index']}] published -> {result['url']}")
    return False


def _cleanup_local_files(item: dict) -> dict | None:
    """
    上传成功后删除该主题的本地任务目录，返回可写入清单的清理记录。

    只有确认拿到 video_id 才会执行：没有远端副本就删除本地成片是不可逆的
    数据丢失。删除前用 file_security 校验路径确实位于 storage/tasks 之内，
    避免清单被手工编辑或损坏后把删除操作引向任意目录。
    """
    from app.utils import file_security, utils

    video_id = (item.get("youtube") or {}).get("video_id")
    if not video_id:
        # 调用方已经做过判断，这里是第二道防线。
        logger.warning(
            f"[{item['index']}] refusing to delete local files without an upload id"
        )
        return None

    task_id = item.get("task_id")
    if not task_id:
        return None

    tasks_root = utils.task_dir()
    try:
        target = file_security.resolve_path_within_directory(
            tasks_root, task_id, require_file=False
        )
    except ValueError as exc:
        logger.warning(f"[{item['index']}] skip cleanup of unsafe task path: {exc}")
        return None

    if not os.path.isdir(target):
        return None

    freed = 0
    for directory, _, names in os.walk(target):
        for name in names:
            try:
                freed += os.path.getsize(os.path.join(directory, name))
            except OSError:
                continue

    try:
        shutil.rmtree(target)
    except OSError as exc:
        logger.warning(f"[{item['index']}] failed to delete {target}: {exc}")
        return None

    logger.info(
        f"[{item['index']}] deleted local files after upload: {target} "
        f"({freed / 1024 / 1024:.0f} MB freed)"
    )
    return {"deleted": True, "freed_bytes": freed, "path": target}


def _generate_scripts(manifest: dict, manifest_path: str, base_params) -> None:
    """阶段一：为每个尚无文案的主题生成脚本，逐条落盘。"""
    from app.services import llm

    items = manifest["items"]
    for item in items:
        if item.get("script") or item.get("status") == STATUS_DONE:
            continue

        subject = item["subject"]
        logger.info(f"[{item['index']}/{len(items)}] generating script: {subject}")
        try:
            script = llm.generate_script(
                video_subject=subject,
                language=base_params.video_language,
                paragraph_number=base_params.paragraph_number,
                video_script_prompt=base_params.video_script_prompt,
                custom_system_prompt=base_params.custom_system_prompt,
            )
        except Exception as exc:
            logger.exception(f"[{item['index']}] script generation crashed: {exc}")
            script = None

        error = _script_error(script)
        if error:
            item["status"] = STATUS_SCRIPT_FAILED
            item["failed_stage"] = "script"
            item["error"] = error
            logger.error(f"[{item['index']}] script failed: {error}")
        else:
            item["script"] = script.strip()
            item["status"] = STATUS_PENDING
            item["failed_stage"] = None
            item["error"] = None

        _save_manifest(manifest_path, manifest)


def _render_items(
    manifest: dict,
    manifest_path: str,
    base_params,
    stop_at: str,
    should_publish: bool,
    privacy_status: str,
    output_dir: str | None,
    delete_after_upload: bool = False,
    publish_limit: int | None = None,
) -> None:
    """阶段二：逐个主题渲染成片，成功后按需发布。"""
    from app.models import const
    from app.services import task as tm
    from app.utils import utils

    items = manifest["items"]
    quota_exhausted = False
    published_this_run = 0

    def publish_budget_spent() -> bool:
        """
        达到本轮发布上限后停止上传。

        Shorts 会先把每个视频投放给一个很小的冷启动人群；同一时间集中发布会让
        它们互相争夺同一批测试观众。分批发布可以让每个视频各自获得曝光机会。
        """
        return publish_limit is not None and published_this_run >= publish_limit

    for item in items:
        satisfied = _is_satisfied(item, stop_at)
        if satisfied and not _needs_publish(item, should_publish):
            continue
        if not item.get("script"):
            continue

        subject = item["subject"]

        # 已渲染完成、只差发布的主题不需要重新消耗渲染时间。
        if satisfied:
            if should_publish and not quota_exhausted and not publish_budget_spent():
                quota_exhausted = _publish_item(item, base_params, privacy_status)
                if (item.get("youtube") or {}).get("video_id"):
                    published_this_run += 1
                _maybe_cleanup(item, delete_after_upload)
                _save_manifest(manifest_path, manifest)
            continue

        logger.info(f"[{item['index']}/{len(items)}] rendering: {subject}")
        item_params = base_params.model_copy(deep=True)
        item_params.video_subject = subject
        item_params.video_script = item["script"]

        track = _bgm_track_for(manifest, item)
        if track:
            item["bgm_file"] = track
            item_params.bgm_type = "custom"
            item_params.bgm_file = track
            logger.info(f"[{item['index']}] background music: {track}")

        chosen_voice = _voice_for(manifest, item)
        if chosen_voice:
            item["voice_name"] = chosen_voice
            item_params.voice_name = chosen_voice
            logger.info(f"[{item['index']}] voice: {chosen_voice}")

        task_id = utils.get_uuid()
        item["task_id"] = task_id
        item["status"] = STATUS_RENDERING
        _save_manifest(manifest_path, manifest)

        try:
            result = tm.start(task_id=task_id, params=item_params, stop_at=stop_at)
        except Exception as exc:
            # tm.start 已经把预期异常转成失败结果，这里只兜住未预期异常，
            # 保证单个主题的崩溃不会中断整个批次。
            logger.exception(f"[{item['index']}] render crashed: {exc}")
            result = None

        if not result or result.get("state") == const.TASK_STATE_FAILED:
            item["status"] = STATUS_FAILED
            item["failed_stage"] = (result or {}).get("failed_stage", "unknown")
            item["error"] = (result or {}).get("error", "empty task result")
            # 配音失败多半是该音色在 TTS 服务上超时。若保留已记录的音色，
            # 续跑会用同一个音色反复失败，因此清空以便重试时重新抽取。
            if item["failed_stage"] == "audio" and manifest.get("voice_pool"):
                logger.info(
                    f"[{item['index']}] clearing voice {item.get('voice_name')} "
                    "so a retry draws a different one"
                )
                item["voice_name"] = None
            logger.error(
                f"[{item['index']}] failed at stage "
                f"{item['failed_stage']}: {item['error']}"
            )
            _save_manifest(manifest_path, manifest)
            continue

        videos = [str(path) for path in (result.get("videos") or [])]
        item["videos"] = videos
        item["status"] = STATUS_DONE
        item["failed_stage"] = None
        item["error"] = None
        item["completed_stage"] = stop_at
        for path in videos:
            logger.success(f"[{item['index']}] done -> {path}")

        if output_dir and videos:
            for ordinal, path in enumerate(videos, start=1):
                name = _slug_for_subject(subject, item["index"])
                if len(videos) > 1:
                    name = f"{name}-{ordinal}"
                collected = _collect_output(
                    path, output_dir, name, prefer_copy=delete_after_upload
                )
                if collected:
                    logger.info(f"[{item['index']}] collected -> {collected}")

        _save_manifest(manifest_path, manifest)

        if should_publish and videos:
            if quota_exhausted:
                logger.warning(
                    f"[{item['index']}] skipping upload: daily YouTube quota exhausted"
                )
                item["youtube"] = {"error": "skipped: daily upload quota exhausted"}
            elif publish_budget_spent():
                logger.info(
                    f"[{item['index']}] holding upload: reached this run's limit of "
                    f"{publish_limit}"
                )
            else:
                quota_exhausted = _publish_item(item, base_params, privacy_status)
                if (item.get("youtube") or {}).get("video_id"):
                    published_this_run += 1
                _maybe_cleanup(item, delete_after_upload)
            _save_manifest(manifest_path, manifest)


def _stage_index(stage: str) -> int:
    try:
        return _STAGE_ORDER.index(stage)
    except ValueError:
        # 未知阶段按最完整处理，避免误判成"还需要继续跑"而重复消耗额度。
        return len(_STAGE_ORDER) - 1


def _is_satisfied(item: dict, stop_at: str) -> bool:
    """
    判断该主题是否已经跑到了本次请求的阶段。

    只看 ``status == done`` 是不够的：``--stop-at terms`` 之类的中间阶段同样会把
    主题标记为完成，如果据此跳过，后续想补跑完整成片时会一个主题都不执行。
    """
    if item.get("status") != STATUS_DONE:
        return False

    completed = item.get("completed_stage")
    if not completed:
        # 旧清单没有记录阶段，有成片即视为跑完了完整流程。
        return bool(item.get("videos"))
    return _stage_index(completed) >= _stage_index(stop_at)


def _maybe_cleanup(item: dict, delete_after_upload: bool) -> None:
    """仅在开启开关且本次确实拿到 video_id 时清理本地文件。"""
    if not delete_after_upload:
        return
    if not (item.get("youtube") or {}).get("video_id"):
        return
    if (item.get("local_cleanup") or {}).get("deleted"):
        return

    record = _cleanup_local_files(item)
    if record:
        item["local_cleanup"] = record
        # 本地文件已删除，清单里的路径不再指向真实文件，保留原路径仅作追溯。
        item["videos_deleted"] = list(item.get("videos") or [])
        item["videos"] = []


def _needs_publish(item: dict, should_publish: bool) -> bool:
    """已完成渲染但尚未成功发布的主题，在开启发布时仍需处理。"""
    if not should_publish:
        return False
    if not item.get("videos"):
        return False
    return not (item.get("youtube") or {}).get("video_id")


def _summarize(manifest: dict, manifest_path: str, should_publish: bool) -> tuple[dict, int]:
    items = manifest["items"]
    succeeded = [item for item in items if item.get("status") == STATUS_DONE]
    failed = [
        item
        for item in items
        if item.get("status") in (STATUS_FAILED, STATUS_SCRIPT_FAILED, STATUS_RENDERING)
    ]
    publish_failed = [
        item
        for item in items
        if should_publish and (item.get("youtube") or {}).get("error")
    ]

    summary = {
        "batch_id": manifest.get("batch_id"),
        "manifest": manifest_path,
        "succeeded": len(succeeded),
        "failed": len(failed),
        "scripts": len([item for item in items if item.get("script")]),
        "published": len(
            [item for item in items if (item.get("youtube") or {}).get("video_id")]
        ),
        "publish_failed": len(publish_failed),
        "freed_bytes": sum(
            (item.get("local_cleanup") or {}).get("freed_bytes", 0) for item in items
        ),
        "items": [
            {
                "index": item.get("index"),
                "subject": item.get("subject"),
                "task_id": item.get("task_id"),
                "status": item.get("status"),
                "videos": item.get("videos", []),
                "error": item.get("error"),
                "youtube": item.get("youtube"),
            }
            for item in items
        ],
    }
    exit_code = 1 if (failed or publish_failed) else 0
    return summary, exit_code


def run_batch(args, base_params=None) -> int:
    """批量入口：返回进程退出码。"""
    from app.models.schema import VideoParams
    from app.services import youtube

    try:
        manifest_path = _manifest_path(args)
        resuming = bool(args.batch_manifest) and os.path.isfile(manifest_path)

        if resuming:
            if args.subjects or args.subjects_file:
                raise BatchInputError(
                    "--subjects/--subjects-file cannot be combined with an existing "
                    f"batch manifest: {manifest_path}"
                )
            manifest = _load_manifest(manifest_path)
            # 续跑必须可复现，因此视频参数以清单为准，命令行参数只影响
            # stop_at、发布和输出目录这类运行时选项。
            logger.warning(
                "resuming from an existing manifest; video parameters come from the "
                "manifest and CLI parameter flags are ignored"
            )
            base_params = VideoParams(**manifest["base_params"])
        else:
            if base_params is None:
                raise BatchInputError("batch mode requires video parameters")
            subjects = (
                load_subjects(args.subjects_file)
                if args.subjects_file
                else [subject.strip() for subject in args.subjects if subject.strip()]
            )
            if not subjects:
                raise BatchInputError("no topics were provided")
            bgm_tracks = (
                prepare_bgm_tracks(args.bgm_dir) if args.bgm_dir else None
            )
            voice_pool = (
                prepare_voice_pool(args.random_voice) if args.random_voice else None
            )
            manifest = _new_manifest(
                subjects, base_params, args.stop_at, bgm_tracks, voice_pool
            )
    except BatchInputError as exc:
        logger.error(str(exc))
        return 2

    stop_at = args.stop_at
    manifest["stop_at"] = stop_at
    should_publish, privacy_status = _resolve_publishing(args)

    from app.config import config

    delete_after_upload = bool(
        args.delete_after_upload
        if args.delete_after_upload is not None
        else config.app.get("youtube_delete_after_upload", False)
    )

    # 发布授权必须在生成任何内容之前确认：批次可能跑几个小时，不能等到
    # 全部渲染完成才发现无法上传。
    if should_publish and stop_at == "video":
        authorized, message = youtube.ensure_authorized()
        if not authorized:
            logger.error(f"YouTube publishing is enabled but not usable: {message}")
            return 2
        if privacy_status == "public":
            logger.info(
                "requesting public uploads; unaudited API projects have their "
                "uploads forced to private by YouTube"
            )
    elif should_publish:
        # 只跑到中间阶段时没有成片可发布，直接关闭发布避免误导性的日志。
        should_publish = False

    if delete_after_upload and not should_publish:
        # 没有远端副本却删除本地成片，等于直接丢数据。
        logger.warning(
            "ignoring --delete-after-upload because publishing is not enabled; "
            "local videos are kept"
        )
        delete_after_upload = False
    if delete_after_upload:
        logger.info(
            "local task files will be deleted after each verified upload"
        )

    _save_manifest(manifest_path, manifest)
    logger.info(f"batch manifest: {manifest_path}")

    _generate_scripts(manifest, manifest_path, base_params)

    if stop_at != "script":
        _render_items(
            manifest,
            manifest_path,
            base_params,
            stop_at,
            should_publish,
            privacy_status,
            args.batch_output_dir,
            delete_after_upload,
            args.publish_limit,
        )

    summary, exit_code = _summarize(manifest, manifest_path, should_publish)
    _save_manifest(manifest_path, manifest)
    print(json.dumps(summary, ensure_ascii=False))
    return exit_code

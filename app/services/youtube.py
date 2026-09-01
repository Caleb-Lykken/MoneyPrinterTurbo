"""
YouTube Data API v3 直接上传集成 —— 可选、按需启用。

与 ``app/services/upload_post.py`` 的第三方中转发布不同，本模块使用用户自己的
Google Cloud 项目和 OAuth 凭据，直接调用 ``videos.insert`` 上传成片。

启用步骤：
  1. 在 Google Cloud 控制台创建项目，启用 YouTube Data API v3；
  2. 创建 "Desktop app" 类型的 OAuth 客户端，把 JSON 下载到
     ``storage/youtube_client_secret.json``；
  3. 安装可选依赖：``uv sync --extra youtube``；
  4. 执行一次 ``cli.py --youtube-auth`` 完成浏览器授权（具体命令见 auth_command）；
  5. 在 config.toml 的 [app] 中设置 ``youtube_upload_enabled = true``。

重要限制：**未通过 Google API 审核的项目**，通过 ``videos.insert`` 上传的视频会被
强制设置为 ``private``。此时请求里的 ``privacyStatus`` 会被接受但随后被覆盖，代码
无法绕过。``upload_video`` 因此会比对返回值与请求值，不一致时给出明确警告，而不是
汇报一次实际上没有公开的“成功”。

配额：默认额度为每天 100 次 ``videos.insert`` 调用。
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
from typing import Any

from loguru import logger

from app.config import config
from app.utils import utils

# 上传所需的最小权限。
UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
# 读取自有视频统计与同领域调研所需的只读权限。
READONLY_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"

# 新增权限不会让已有 token 失效：旧 token 仍可继续上传，只是读取统计会被拒绝。
# 因此 ensure_authorized 只校验上传能力，读取能力由 missing_scopes 单独判断，
# 避免为了新功能而中断正在运行的发布流程。
SCOPES = [UPLOAD_SCOPE, READONLY_SCOPE]

DEFAULT_CLIENT_SECRETS_FILENAME = "youtube_client_secret.json"
DEFAULT_TOKEN_FILENAME = "youtube_token.json"

# YouTube 对元数据的硬性上限，超出会被接口直接拒绝。
MAX_TITLE_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 5000
MAX_TAGS_TOTAL_LENGTH = 450

VALID_PRIVACY_STATUSES = ("public", "unlisted", "private")
DEFAULT_CATEGORY_ID = "22"  # People & Blogs

_UPLOAD_CHUNK_SIZE = 4 * 1024 * 1024
_RETRIABLE_STATUS_CODES = (500, 502, 503, 504)
_MAX_UPLOAD_ATTEMPTS = 5

# 触发这些原因说明当天的上传额度已经耗尽，继续重试没有意义。
_QUOTA_ERROR_REASONS = ("quotaExceeded", "uploadLimitExceeded", "dailyLimitExceeded")

_MISSING_DEPENDENCY_HINT = (
    "YouTube upload requires the optional Google client libraries. "
    "Install them with: uv sync --extra youtube"
)

_AUDIT_HINT = (
    "Videos uploaded by an API project that has not passed Google's audit are "
    "forced to private. Request an audit in the Google Cloud console, or publish "
    "the video manually from YouTube Studio."
)


class YouTubeError(Exception):
    """YouTube 集成的统一错误类型。"""

    def __init__(self, message: str, *, quota_exceeded: bool = False):
        super().__init__(message)
        self.quota_exceeded = quota_exceeded


def auth_command() -> str:
    """
    返回当前环境下真正可执行的授权命令。

    项目文档统一写作 ``python cli.py``，但实际环境里解释器可能位于虚拟环境、
    uv 缓存或 git worktree 之外的目录。错误提示里给出可直接复制执行的命令，
    比给出一条在用户机器上跑不通的通用写法更有帮助。
    """
    return f"{sys.executable} {os.path.join(utils.root_dir(), 'cli.py')} --youtube-auth"


def _client_secrets_file() -> str:
    configured = str(config.app.get("youtube_client_secrets_file", "") or "").strip()
    if configured:
        return os.path.expanduser(configured)
    return os.path.join(utils.storage_dir(), DEFAULT_CLIENT_SECRETS_FILENAME)


def _token_file() -> str:
    configured = str(config.app.get("youtube_token_file", "") or "").strip()
    if configured:
        return os.path.expanduser(configured)
    return os.path.join(utils.storage_dir(), DEFAULT_TOKEN_FILENAME)


def is_enabled() -> bool:
    """仅当显式开启且 OAuth 客户端文件存在时才认为集成可用。"""
    if not config.app.get("youtube_upload_enabled", False):
        return False
    return os.path.isfile(_client_secrets_file())


def resolve_privacy_status(value: str | None) -> str:
    """把配置或命令行传入的隐私级别规整为接口接受的取值。"""
    normalized = str(value or "").strip().lower()
    if normalized in VALID_PRIVACY_STATUSES:
        return normalized
    if normalized:
        logger.warning(
            f"unknown YouTube privacy status {value!r}; falling back to private"
        )
    return "private"


def configured_privacy_status() -> str:
    return resolve_privacy_status(config.app.get("youtube_privacy_status", "public"))


def _google_modules():
    """延迟导入可选依赖，缺失时给出可执行的安装提示而不是 ImportError 堆栈。"""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload
    except ImportError as exc:
        raise YouTubeError(f"{_MISSING_DEPENDENCY_HINT} ({exc})") from exc

    return {
        "Request": Request,
        "Credentials": Credentials,
        "InstalledAppFlow": InstalledAppFlow,
        "build": build,
        "HttpError": HttpError,
        "MediaFileUpload": MediaFileUpload,
    }


def _save_credentials(credentials) -> None:
    """以 0600 权限写入 token，避免长期有效的上传凭据被同机其他用户读取。"""
    token_path = _token_file()
    os.makedirs(os.path.dirname(token_path), exist_ok=True)
    descriptor = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as token_file:
        token_file.write(credentials.to_json())
    # 已存在的文件不会被 os.open 的 mode 参数收紧，这里显式对齐权限。
    os.chmod(token_path, 0o600)
    logger.info(f"stored YouTube credentials: {token_path}")


def granted_scopes() -> list[str]:
    """读取本地 token 实际持有的权限；无 token 时返回空列表。"""
    token_path = _token_file()
    if not os.path.isfile(token_path):
        return []
    try:
        with open(token_path, "r", encoding="utf-8") as token_file:
            return list(json.load(token_file).get("scopes") or [])
    except (OSError, json.JSONDecodeError):
        return []


def missing_scopes(required: list[str] | None = None) -> list[str]:
    """返回 token 尚未授予的权限，供调用方给出准确的重新授权提示。"""
    granted = set(granted_scopes())
    return [scope for scope in (required or SCOPES) if scope not in granted]


def _load_credentials(*, allow_refresh: bool = True):
    """
    读取本地缓存的凭据，必要时静默刷新。

    这里绝不触发交互式授权：批量任务在后台运行，弹出浏览器只会让流程卡死。
    需要用户参与的场景由 ``run_auth_flow`` 单独处理。
    """
    modules = _google_modules()
    token_path = _token_file()
    if not os.path.isfile(token_path):
        return None

    try:
        # 按 token 实际持有的权限构造凭据。若按 SCOPES 构造，旧的仅上传 token
        # 会被认为缺少权限，正在运行的发布流程会因为新增只读权限而中断。
        credentials = modules["Credentials"].from_authorized_user_file(
            token_path, granted_scopes() or SCOPES
        )
    except (ValueError, json.JSONDecodeError) as exc:
        raise YouTubeError(
            f"stored YouTube credentials are unreadable ({exc}); "
            f"re-authorize with: {auth_command()}"
        ) from exc

    if credentials.valid:
        return credentials

    if allow_refresh and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(modules["Request"]())
        except Exception as exc:
            raise YouTubeError(
                f"failed to refresh YouTube credentials ({exc}); "
                f"re-authorize with: {auth_command()}"
            ) from exc
        _save_credentials(credentials)
        return credentials

    return None


def ensure_authorized() -> tuple[bool, str]:
    """
    发布前置检查：确认存在可用（或可静默刷新）的凭据。

    批量任务在渲染任何视频之前调用它，避免跑完整条流水线才发现无法发布。
    """
    if UPLOAD_SCOPE in missing_scopes([UPLOAD_SCOPE]) and granted_scopes():
        return False, (
            "the stored YouTube token cannot upload videos. Re-authorize with: "
            f"{auth_command()}"
        )

    if not os.path.isfile(_client_secrets_file()):
        return False, (
            "YouTube OAuth client secrets not found: "
            f"{_client_secrets_file()}. Create a Desktop OAuth client in the "
            "Google Cloud console and save the JSON there."
        )

    try:
        credentials = _load_credentials()
    except YouTubeError as exc:
        return False, str(exc)

    if credentials is None:
        return False, (
            f"YouTube account is not authorized yet. Run: {auth_command()}"
        )
    return True, ""


def run_auth_flow() -> int:
    """执行一次性的浏览器授权，成功返回 0。"""
    secrets_path = _client_secrets_file()
    if not os.path.isfile(secrets_path):
        logger.error(
            f"YouTube OAuth client secrets not found: {secrets_path}. "
            "Create a Desktop OAuth client in the Google Cloud console "
            "(APIs & Services > Credentials) and save the downloaded JSON there."
        )
        return 2

    try:
        modules = _google_modules()
        flow = modules["InstalledAppFlow"].from_client_secrets_file(
            secrets_path, SCOPES
        )
        # port=0 让系统分配空闲端口，避免固定端口被占用导致授权失败。
        credentials = flow.run_local_server(port=0)
        _save_credentials(credentials)
    except YouTubeError as exc:
        logger.error(str(exc))
        return 1
    except Exception as exc:
        logger.exception(f"YouTube authorization failed: {exc}")
        return 1

    logger.success("YouTube authorization complete.")
    return 0


def _normalize_tags(hashtags: list[str] | None) -> list[str]:
    """
    把 ``#tag`` 形式的话题词转换成 YouTube tags 字段可用的纯文本。

    接口对 tags 的总长度有限制，超出会整体拒绝，因此这里按累计长度截断。
    """
    tags: list[str] = []
    total_length = 0
    for raw in hashtags or []:
        tag = str(raw or "").strip().lstrip("#").strip()
        # 尖括号会被接口拒绝；逗号会被理解成标签分隔符。
        tag = tag.replace("<", "").replace(">", "").replace(",", " ").strip()
        if not tag or tag in tags:
            continue
        # 逗号分隔后每个标签还会额外占用一个字符。
        projected = total_length + len(tag) + 1
        if projected > MAX_TAGS_TOTAL_LENGTH:
            break
        tags.append(tag)
        total_length = projected
    return tags


def build_metadata(
    video_subject: str,
    video_script: str = "",
    language: str = "",
    extra_tags: list[str] | None = None,
) -> dict:
    """
    生成上传所需的标题、描述和标签。

    直接复用 ``llm.generate_social_metadata``：它已经为 youtube_shorts 平台做了
    长度约束，并在 LLM 不可用时降级为启发式结果，因此这里不需要再处理异常分支。
    """
    from app.services import llm

    metadata = llm.generate_social_metadata(
        video_subject=video_subject,
        video_script=video_script,
        language=language or "",
        platform="youtube_shorts",
    )

    title = str(metadata.get("title") or video_subject or "").strip()
    if not title:
        title = "Untitled"
    # 尖括号是 YouTube 明确拒绝的字符，出现时整个请求会失败。
    title = title.replace("<", "").replace(">", "")[:MAX_TITLE_LENGTH]

    hashtags = list(metadata.get("hashtags") or [])
    description_parts = [str(metadata.get("caption") or "").strip()]
    hashtag_line = " ".join(tag for tag in hashtags if tag)
    if hashtag_line:
        description_parts.append(hashtag_line)
    description = "\n\n".join(part for part in description_parts if part)

    # 竖屏且时长足够短的视频会被自动识别为 Shorts，#Shorts 只是额外的明确信号。
    if "#shorts" not in description.lower():
        description = f"{description}\n\n#Shorts".strip()
    description = description.replace("<", "").replace(">", "")[:MAX_DESCRIPTION_LENGTH]

    tags = _normalize_tags(hashtags + list(extra_tags or []))
    return {"title": title, "description": description, "tags": tags}


def _http_error_reasons(exc: Exception) -> str:
    """把 HttpError 的响应体转成可检索的文本，用于识别配额类错误。"""
    content = getattr(exc, "content", b"") or b""
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")
    return f"{content} {exc}"


def _is_quota_error(exc: Exception) -> bool:
    """
    识别配额/上传上限类错误。

    判断依据是响应体中的 reason，而不是状态码：频道每日上传上限返回的是
    HTTP 400 且 reason 为 ``uploadLimitExceeded``，按状态码过滤会把它当成
    普通失败，导致后续每个视频都白白重试一次上传。
    """
    reasons = _http_error_reasons(exc)
    return any(reason in reasons for reason in _QUOTA_ERROR_REASONS)


def upload_video(
    video_path: str,
    *,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    privacy_status: str = "private",
    category_id: str | None = None,
    made_for_kids: bool = False,
) -> dict:
    """
    以断点续传方式上传单个视频，返回发布结果。

    成功时返回 ``{"video_id", "url", "privacy_status", "requested_privacy_status"}``。
    失败抛出 ``YouTubeError``；配额耗尽时 ``quota_exceeded`` 为 True，调用方据此
    停止后续上传尝试。
    """
    if not os.path.isfile(video_path):
        raise YouTubeError(f"video file does not exist: {video_path}")

    modules = _google_modules()
    credentials = _load_credentials()
    if credentials is None:
        raise YouTubeError(
            f"YouTube account is not authorized. Run: {auth_command()}"
        )

    requested_privacy = resolve_privacy_status(privacy_status)
    body = {
        "snippet": {
            "title": (title or "Untitled")[:MAX_TITLE_LENGTH],
            "description": (description or "")[:MAX_DESCRIPTION_LENGTH],
            "tags": list(tags or []),
            "categoryId": str(category_id or DEFAULT_CATEGORY_ID),
        },
        "status": {
            "privacyStatus": requested_privacy,
            "selfDeclaredMadeForKids": bool(made_for_kids),
            # 本项目生成的画面和配音均由模型合成，必须按平台要求声明。
            "containsSyntheticMedia": True,
        },
    }

    service = modules["build"](
        "youtube", "v3", credentials=credentials, cache_discovery=False
    )
    media = modules["MediaFileUpload"](
        video_path,
        chunksize=_UPLOAD_CHUNK_SIZE,
        resumable=True,
        mimetype="video/*",
    )
    request = service.videos().insert(
        part="snippet,status", body=body, media_body=media
    )

    logger.info(f"uploading to YouTube: {os.path.basename(video_path)}")
    response = None
    attempt = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                logger.debug(f"YouTube upload progress: {int(status.progress() * 100)}%")
        except modules["HttpError"] as exc:
            if _is_quota_error(exc):
                raise YouTubeError(
                    f"YouTube upload quota exhausted: {exc}", quota_exceeded=True
                ) from exc
            status_code = getattr(getattr(exc, "resp", None), "status", None)
            if status_code not in _RETRIABLE_STATUS_CODES:
                raise YouTubeError(f"YouTube upload failed: {exc}") from exc
            attempt += 1
            if attempt >= _MAX_UPLOAD_ATTEMPTS:
                raise YouTubeError(
                    f"YouTube upload failed after {attempt} attempts: {exc}"
                ) from exc
            # 指数退避加随机抖动，避免多次重试打在同一时间点。
            delay = min(2**attempt, 60) + random.random()
            logger.warning(
                f"retriable YouTube upload error (attempt {attempt}): {exc}; "
                f"retrying in {delay:.1f}s"
            )
            time.sleep(delay)
        except (OSError, TimeoutError) as exc:
            attempt += 1
            if attempt >= _MAX_UPLOAD_ATTEMPTS:
                raise YouTubeError(
                    f"YouTube upload failed after {attempt} attempts: {exc}"
                ) from exc
            delay = min(2**attempt, 60) + random.random()
            logger.warning(
                f"transport error during YouTube upload (attempt {attempt}): {exc}; "
                f"retrying in {delay:.1f}s"
            )
            time.sleep(delay)

    return _build_upload_result(response, requested_privacy)


def _build_upload_result(response: Any, requested_privacy: str) -> dict:
    """
    汇总上传结果，并识别未审核项目导致的隐私级别降级。

    未通过审核时接口会接受 public 请求但返回 private。若不做比对，调用方会拿到
    一个“成功”的结果，却在频道上看不到公开视频。
    """
    if not isinstance(response, dict):
        raise YouTubeError(f"YouTube returned an unexpected response: {response!r}")

    video_id = str(response.get("id") or "").strip()
    if not video_id:
        raise YouTubeError(f"YouTube response is missing a video id: {response!r}")

    actual_privacy = str(
        (response.get("status") or {}).get("privacyStatus") or ""
    ).strip()
    result = {
        "video_id": video_id,
        "url": f"https://youtu.be/{video_id}",
        "privacy_status": actual_privacy or requested_privacy,
        "requested_privacy_status": requested_privacy,
    }

    if actual_privacy and actual_privacy != requested_privacy:
        logger.warning(
            f"YouTube stored video {video_id} as {actual_privacy!r} although "
            f"{requested_privacy!r} was requested. {_AUDIT_HINT}"
        )
    else:
        logger.success(f"uploaded to YouTube: {result['url']} ({result['privacy_status']})")

    return result

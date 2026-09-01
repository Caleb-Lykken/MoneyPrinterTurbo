"""
批量生成的启动与监控界面。

与主 WebUI 相互独立：批量任务作为**子进程**运行，因此刷新页面、关闭标签页
甚至重启本界面都不会中断正在进行的渲染。界面本身只做两件事：

  * 启动：把主题写入 topics.txt，然后拉起 ``cli.py``；
  * 监控：轮询批次清单（manifest）和运行日志。

清单由批量服务原子写入，所以任何时刻读到的都是完整状态，不需要额外加锁。
"""

import json
import os
import re
import signal
import subprocess
import sys
from datetime import datetime

import streamlit as st

root_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if root_dir in sys.path:
    sys.path.remove(root_dir)
sys.path.insert(0, root_dir)

from app.services import batch  # noqa: E402
from app.services import youtube_stats  # noqa: E402
from app.utils import utils  # noqa: E402

st.set_page_config(
    page_title="MoneyPrinterTurbo Batch",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)

REFRESH_SECONDS = "2s"

_STATUS_BADGE = {
    batch.STATUS_PENDING: ("⏳", "queued"),
    batch.STATUS_SCRIPT_FAILED: ("❌", "script failed"),
    batch.STATUS_RENDERING: ("🎬", "rendering"),
    batch.STATUS_DONE: ("✅", "done"),
    batch.STATUS_FAILED: ("❌", "failed"),
}


# --------------------------------------------------------------------------
# 批次发现与进程管理
# --------------------------------------------------------------------------
def batches_root() -> str:
    return utils.storage_dir("batches", create=True)


def batch_paths(batch_dir: str) -> dict:
    return {
        "dir": batch_dir,
        "manifest": os.path.join(batch_dir, "manifest.json"),
        "log": os.path.join(batch_dir, "run.log"),
        "pid": os.path.join(batch_dir, "run.pid"),
        "topics": os.path.join(batch_dir, "topics.txt"),
    }


def read_pid(batch_dir: str) -> int | None:
    try:
        with open(batch_paths(batch_dir)["pid"], "r", encoding="utf-8") as handle:
            return int(handle.read().strip())
    except (OSError, ValueError):
        return None


def _process_state(pid: int) -> str:
    """读取进程状态码；取不到时返回空字符串。"""
    try:
        result = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


def is_running(batch_dir: str) -> bool:
    """
    判断批量进程是否仍在运行。

    仅用 ``os.kill(pid, 0)`` 是不够的：子进程结束后若没有被父进程回收会变成
    僵尸进程，此时信号 0 依然成功，界面就会一直显示"运行中"，既不会停止自动
    刷新，也永远不显示成片播放器。因此这里先尝试非阻塞回收，再排除僵尸状态。
    """
    pid = read_pid(batch_dir)
    if not pid:
        return False

    try:
        reaped, _ = os.waitpid(pid, os.WNOHANG)
        if reaped == pid:
            # 刚刚回收，说明进程已经结束。
            return False
    except ChildProcessError:
        # 界面重启过，该进程已不是当前进程的子进程，交给下面的状态检查。
        pass
    except OSError:
        pass

    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False

    return not _process_state(pid).startswith("Z")


def discover_batches() -> list[dict]:
    root = batches_root()
    found = []
    for name in os.listdir(root):
        batch_dir = os.path.join(root, name)
        paths = batch_paths(batch_dir)
        has_manifest = os.path.isfile(paths["manifest"])
        # 清单要等应用完成启动后才写出，这段时间里批次已经在跑了。
        # 只要存在 pid 文件就纳入列表，否则用户点完"开始"会有一分多钟看不到任何反馈。
        if not has_manifest and not os.path.isfile(paths["pid"]):
            continue
        found.append(
            {
                "id": name,
                "dir": batch_dir,
                "mtime": os.path.getmtime(
                    paths["manifest"] if has_manifest else paths["pid"]
                ),
                "running": is_running(batch_dir),
                "starting": not has_manifest,
            }
        )
    # 运行中的批次永远排在最前，其余按最近更新排序。
    found.sort(key=lambda item: (not item["running"], -item["mtime"]))
    return found


def read_manifest(batch_dir: str) -> dict | None:
    path = batch_paths(batch_dir)["manifest"]
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        # 清单是原子写入的，读到半个文件属于极端情况，下一次轮询即可恢复。
        return None


def tail_log(batch_dir: str, lines: int = 40) -> str:
    path = batch_paths(batch_dir)["log"]
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            content = handle.readlines()
    except OSError:
        return ""

    plain = [re.sub(r"\x1b\[[0-9;]*m", "", line.rstrip()) for line in content[-lines:]]
    return "\n".join(line for line in plain if line.strip())


def launch_batch(topics: list[str], options: dict) -> str:
    """把批量任务作为独立子进程拉起，返回批次目录。"""
    batch_dir = os.path.join(batches_root(), utils.get_uuid())
    os.makedirs(batch_dir, exist_ok=True)
    paths = batch_paths(batch_dir)

    with open(paths["topics"], "w", encoding="utf-8") as handle:
        handle.write("\n".join(topics) + "\n")

    command = [
        sys.executable,
        os.path.join(root_dir, "cli.py"),
        "--subjects-file", paths["topics"],
        "--batch-manifest", paths["manifest"],
        "--stop-at", options["stop_at"],
        "--video-aspect", options["aspect"],
        "--video-count", str(options["video_count"]),
    ]
    if options.get("bgm_dir"):
        command += ["--bgm-dir", options["bgm_dir"]]
    if options.get("random_voice"):
        command += ["--random-voice", options["random_voice"]]
    if options.get("subtitle_position"):
        command += ["--subtitle-position", options["subtitle_position"]]
        if options["subtitle_position"] == "custom":
            command += ["--custom-position", str(options["custom_position"])]
    if options.get("font_size"):
        command += ["--font-size", str(options["font_size"])]
    if options.get("stroke_width"):
        command += ["--stroke-width", str(options["stroke_width"])]
    if options.get("font_name"):
        command += ["--font-name", options["font_name"]]
    if options.get("output_dir"):
        command += ["--batch-output-dir", options["output_dir"]]
    if options.get("publish"):
        command += ["--publish", "--youtube-privacy", options["privacy"]]
        if options.get("delete_after_upload"):
            command += ["--delete-after-upload"]
    else:
        command += ["--no-publish"]
    if options.get("language"):
        command += ["--video-language", options["language"]]

    log_handle = open(paths["log"], "w", encoding="utf-8")
    process = subprocess.Popen(
        command,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        cwd=root_dir,
        # 独立会话：Streamlit 重跑、页面刷新或本界面退出都不会波及渲染进程。
        start_new_session=True,
    )
    with open(paths["pid"], "w", encoding="utf-8") as handle:
        handle.write(str(process.pid))
    return batch_dir


def cancel_batch(batch_dir: str) -> bool:
    pid = read_pid(batch_dir)
    if not pid:
        return False
    try:
        # 子进程运行在自己的会话里，按进程组结束可以一并回收 ffmpeg 子进程。
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (OSError, ProcessLookupError):
        return False
    return True


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------
def render_launch_form() -> None:
    st.subheader("Start a new batch")

    topics_raw = st.text_area(
        "Topics — one per line",
        height=160,
        placeholder="How AI changes daily life\nWhy sleep matters\nThe history of coffee",
        key="batch_topics",
    )

    left, middle, right = st.columns(3)
    with left:
        aspect = st.selectbox("Aspect ratio", ["9:16", "16:9", "1:1"], index=0)
        stop_at = st.selectbox(
            "Run until",
            ["video", "script", "terms", "audio", "subtitle", "materials"],
            index=0,
            help="'script' writes every script without rendering, so you can review them first.",
        )
        video_count = st.number_input("Videos per topic", 1, 5, 1)
    with middle:
        bgm_dir = st.text_input(
            "Background music folder (optional)",
            value="",
            placeholder="/Users/you/Desktop/Music",
            help="Tracks rotate, one per topic. Leave empty to use the built-in songs.",
        )
        # 上游默认音色是中文，用英文脚本生成时会得到浓重口音的解说。
        # 这里默认随机选用美式英语音色，避免重复踩到这个坑。
        voice_locale = st.selectbox(
            "Voice",
            ["en-US", "en-GB", "en-AU", "en-CA", "(project default)"],
            index=0,
            help=(
                "A random voice from this locale is picked for each topic. "
                "The project default is a Chinese voice, which reads English "
                "with a heavy accent."
            ),
        )
        language = st.text_input(
            "Script language (optional)", value="", placeholder="en-US / zh-CN"
        )
        output_dir = st.text_input(
            "Collect finished videos in (optional)", value="", placeholder="./out"
        )
    with right:
        publish = st.checkbox("Upload to YouTube", value=False)
        privacy = st.selectbox(
            "Privacy", ["public", "unlisted", "private"], index=0, disabled=not publish
        )
        delete_after_upload = st.checkbox(
            "Delete local files after upload",
            value=False,
            disabled=not publish,
            help=(
                "Removes storage/tasks/<id>/ once a video id comes back — about "
                "65 MB per video. Only ever runs after a confirmed upload."
            ),
        )
        if publish and delete_after_upload:
            st.caption("🗑️ Local copies are removed once YouTube confirms the upload.")

    st.markdown("**Subtitles**")
    sub_left, sub_middle, sub_right = st.columns(3)
    # Shorts 会在画面底部覆盖标题、频道名和操作按钮，默认的 bottom 位置
    # 正好被挡住，因此这里默认把字幕抬到画面偏上的安全区。
    placement = sub_left.selectbox(
        "Placement",
        ["Shorts-safe (above the UI)", "bottom", "center", "top"],
        index=0,
        help=(
            "YouTube Shorts covers roughly the bottom 20% of the screen with the "
            "title, channel name and buttons. 'bottom' puts subtitles right "
            "underneath that overlay."
        ),
    )
    height_pct = sub_middle.slider(
        "Height from top (%)",
        30, 80, 60,
        disabled=not placement.startswith("Shorts-safe"),
        help="Lower value = higher on screen. 60% clears the Shorts overlay.",
    )
    font_size = sub_right.number_input("Font size", 20, 160, 80)
    # 项目默认字体是中文字体，其拉丁字形偏细，在杂乱画面上不够醒目。
    font_name = st.selectbox(
        "Font",
        [
            "BeVietnamPro-Bold.ttf",
            "BeVietnamPro-Medium.ttf",
            "Charm-Bold.ttf",
            "UTM Kabel KT.ttf",
            "STHeitiMedium.ttc",
        ],
        index=0,
        help=(
            "Bold Latin fonts read far better on busy footage. STHeiti is the "
            "project default and is designed for Chinese text."
        ),
    )

    topics = [line.strip() for line in topics_raw.splitlines() if line.strip()]
    topics = [line for line in topics if not line.startswith("#")]

    if st.button(
        f"▶ Start batch ({len(topics)} topic{'s' if len(topics) != 1 else ''})",
        type="primary",
        disabled=not topics,
        use_container_width=True,
    ):
        if bgm_dir and not os.path.isdir(os.path.expanduser(bgm_dir)):
            st.error(f"Background music folder does not exist: {bgm_dir}")
            return
        batch_dir = launch_batch(topics, {
            "aspect": aspect,
            "stop_at": stop_at,
            "video_count": int(video_count),
            "bgm_dir": os.path.expanduser(bgm_dir) if bgm_dir else "",
            "language": language.strip(),
            "output_dir": os.path.expanduser(output_dir) if output_dir else "",
            "publish": publish,
            "privacy": privacy,
            "delete_after_upload": delete_after_upload,
            "random_voice": (
                "" if voice_locale == "(project default)" else voice_locale
            ),
            "subtitle_position": (
                "custom" if placement.startswith("Shorts-safe") else placement
            ),
            "custom_position": float(height_pct),
            "font_size": int(font_size),
            # 字号变大后，加粗描边能在杂乱画面上保持可读性。
            "stroke_width": 2.5,
            "font_name": font_name,
        })
        st.session_state["selected_batch"] = batch_dir
        st.rerun()


def render_item(item: dict, live: bool) -> None:
    icon, label = _STATUS_BADGE.get(item.get("status"), ("•", item.get("status", "?")))
    subject = item.get("subject", "")

    with st.container(border=True):
        header, meta = st.columns([4, 2])
        with header:
            st.markdown(f"**{icon} {item.get('index')}. {subject}**")
            st.caption(label + (f" · {item['completed_stage']}" if item.get("completed_stage") else ""))
        with meta:
            if item.get("bgm_file"):
                st.caption(f"🎵 {item['bgm_file']}")
            if item.get("voice_name"):
                st.caption(f"🗣️ {item['voice_name']}")
            youtube = item.get("youtube") or {}
            if youtube.get("url"):
                privacy = youtube.get("privacy_status", "")
                requested = youtube.get("requested_privacy_status", "")
                st.markdown(f"📺 [{youtube['url']}]({youtube['url']})")
                if requested and privacy and privacy != requested:
                    st.warning(
                        f"stored as {privacy}, {requested} was requested "
                        "(API project not audited yet)",
                        icon="⚠️",
                    )
                else:
                    st.caption(f"privacy: {privacy}")
            elif youtube.get("error"):
                st.error(youtube["error"], icon="📺")

        if item.get("error"):
            st.error(f"{item.get('failed_stage') or 'error'}: {item['error']}")

        if item.get("script"):
            with st.expander("Script", expanded=False):
                st.write(item["script"])

        cleanup = item.get("local_cleanup") or {}
        if cleanup.get("deleted"):
            freed = cleanup.get("freed_bytes", 0) / 1024 / 1024
            st.caption(f"🗑️ local files removed after upload ({freed:.0f} MB freed)")

        for video in item.get("videos") or []:
            if not os.path.isfile(video):
                st.caption(f"missing file: {video}")
                continue
            if live:
                # 自动刷新会让播放器每 2 秒重新加载，运行期间只显示路径。
                st.caption(f"✅ {video}")
            else:
                st.video(video)


def render_monitor_body(batch_dir: str, live: bool) -> None:
    manifest = read_manifest(batch_dir)
    if manifest is None:
        if is_running(batch_dir):
            st.info(
                "⏳ Starting up — loading the video pipeline. "
                "The first topic begins in a minute or so."
            )
            with st.expander("Log", expanded=True):
                st.code(tail_log(batch_dir) or "(no output yet)", language="log")
        else:
            st.warning("This batch has no manifest and is not running.")
        return

    items = manifest.get("items", [])
    running = is_running(batch_dir)
    done = [i for i in items if i.get("status") == batch.STATUS_DONE]
    failed = [
        i for i in items
        if i.get("status") in (batch.STATUS_FAILED, batch.STATUS_SCRIPT_FAILED)
    ]
    published = [i for i in items if (i.get("youtube") or {}).get("video_id")]
    scripts = [i for i in items if i.get("script")]

    status_line = "🟢 running" if running else "⚪ not running"
    st.markdown(f"### {status_line} · `{os.path.basename(batch_dir)}`")

    columns = st.columns(5)
    columns[0].metric("Topics", len(items))
    columns[1].metric("Scripts", len(scripts))
    columns[2].metric("Rendered", len(done))
    columns[3].metric("Failed", len(failed))
    columns[4].metric("Published", len(published))

    total = len(items) or 1
    st.progress(len(done) / total, text=f"{len(done)} of {len(items)} rendered")

    if running and st.button("■ Stop this batch", key=f"cancel-{batch_dir}"):
        if cancel_batch(batch_dir):
            st.warning("Stop signal sent. The current topic finishes its ffmpeg step first.")
        else:
            st.error("Could not signal the batch process.")

    if live:
        st.caption(
            "Auto-refreshing every 2s. Turn it off in the sidebar to play the "
            "finished videos inline."
        )

    for item in items:
        render_item(item, live)

    with st.expander("Log", expanded=not done):
        st.code(tail_log(batch_dir) or "(no output yet)", language="log")


@st.fragment(run_every=REFRESH_SECONDS)
def render_monitor_live(batch_dir: str) -> None:
    render_monitor_body(batch_dir, live=True)


def render_monitor(batch_dir: str, live: bool) -> None:
    """
    只有正在运行且开启自动刷新时才使用 fragment 轮询。

    批次结束后停止刷新，视频播放器才不会被反复重建。
    """
    if live:
        render_monitor_live(batch_dir)
    else:
        render_monitor_body(batch_dir, live=False)


def render_sidebar() -> str | None:
    st.sidebar.title("🎬 Batches")
    if st.sidebar.button("🔄 Refresh list", use_container_width=True):
        st.rerun()

    found = discover_batches()
    if not found:
        st.sidebar.info("No batches yet.")
        return None

    def label(entry: dict) -> str:
        stamp = datetime.fromtimestamp(entry["mtime"]).strftime("%m-%d %H:%M")
        if entry.get("starting") and entry["running"]:
            icon = "⏳"
        else:
            icon = "🟢" if entry["running"] else "⚪"
        return f"{icon} {stamp} · {entry['id'][:8]}"

    directories = [entry["dir"] for entry in found]
    selected = st.session_state.get("selected_batch")
    index = directories.index(selected) if selected in directories else 0

    chosen = st.sidebar.radio(
        "Select a batch",
        directories,
        index=index,
        format_func=lambda directory: label(
            next(entry for entry in found if entry["dir"] == directory)
        ),
        label_visibility="collapsed",
    )
    st.session_state["selected_batch"] = chosen
    st.sidebar.caption(f"`{chosen}`")
    return chosen


def _stats_error(exc: Exception) -> None:
    message = str(exc)
    st.error(message)
    if "insufficient" in message.lower() or "not authorized" in message.lower():
        st.info(
            "Reading statistics needs the youtube.readonly scope. Re-run the "
            "authorization command once to grant it."
        )


def render_performance() -> None:
    st.subheader("How your videos are doing")
    st.caption(
        "Statistics are fetched on demand — each refresh spends a little API quota."
    )

    if st.button("🔄 Refresh statistics", type="primary"):
        try:
            st.session_state["performance_rows"] = youtube_stats.performance_rows()
        except Exception as exc:
            _stats_error(exc)
            return

    rows = st.session_state.get("performance_rows")
    if rows is None:
        st.info("Press refresh to load statistics for your published videos.")
        return
    if not rows:
        st.info("No published videos found yet.")
        return

    trustworthy, caveat = youtube_stats.has_meaningful_signal(rows)
    if not trustworthy:
        # 明确告知数据不足，避免把随机波动当成选题结论。
        st.warning(f"⚠️ Not enough data to rank topics yet. {caveat}", icon="⚠️")

    counted = [row for row in rows if row.get("views") is not None]
    columns = st.columns(3)
    columns[0].metric("Published videos", len(rows))
    columns[1].metric("Total views", sum(row["views"] for row in counted))
    columns[2].metric("Total likes", sum(row["likes"] for row in counted))

    st.dataframe(
        [
            {
                "Topic": row.get("subject") or row.get("title", ""),
                "Views": row.get("views"),
                "Likes": row.get("likes"),
                "Comments": row.get("comments"),
                "Age (days)": (
                    round(row["age_days"], 1) if row.get("age_days") else None
                ),
                "Views/day": (
                    round(row["views_per_day"], 1) if row.get("views_per_day") else None
                ),
                "URL": row.get("url"),
            }
            for row in rows
        ],
        use_container_width=True,
        hide_index=True,
    )


def render_research() -> None:
    st.subheader("What is working in your niche")
    st.caption(
        "Searches YouTube for the most-viewed recent videos matching a keyword. "
        "Limited to 100 searches per day by the API quota."
    )

    left, middle, right = st.columns([3, 1, 1])
    keyword = left.text_input("Keyword", value="fishing tips", key="research_keyword")
    days = middle.number_input("Last N days", 1, 365, 30)
    limit = right.number_input("Results", 5, 50, 25)

    if st.button("🔎 Search", type="primary"):
        try:
            st.session_state["research_rows"] = youtube_stats.search_niche(
                keyword, days=int(days), max_results=int(limit)
            )
        except Exception as exc:
            _stats_error(exc)
            return

    rows = st.session_state.get("research_rows")
    if not rows:
        if rows is not None:
            st.info("No videos found for that keyword and window.")
        return

    st.dataframe(
        [
            {
                "Title": row["title"],
                "Views": row["views"],
                "Likes": row["likes"],
                "Age (days)": round(row["age_days"], 1) if row.get("age_days") else None,
                "Views/day": (
                    round(row["views_per_day"], 1) if row.get("views_per_day") else None
                ),
                "URL": row["url"],
            }
            for row in rows
        ],
        use_container_width=True,
        hide_index=True,
    )


def render_grow() -> None:
    st.subheader("Make more of what works")

    rows = st.session_state.get("performance_rows") or []
    research = st.session_state.get("research_rows") or []
    if not rows:
        st.info(
            "Load your statistics on the Performance tab first — new topics are "
            "built from your best performers."
        )
        return

    trustworthy, caveat = youtube_stats.has_meaningful_signal(rows)
    if not trustworthy:
        st.warning(
            f"⚠️ Your best performers are not yet statistically meaningful. {caveat} "
            "Topics generated now mostly reflect chance, not what actually works.",
            icon="⚠️",
        )

    ranked = [row for row in rows if row.get("views") is not None]
    winners = [
        row.get("subject") or row.get("title", "")
        for row in ranked[: min(5, len(ranked))]
    ]
    st.write("**Top performers used as the pattern:**")
    for name in winners:
        st.write(f"- {name}")

    left, right = st.columns(2)
    count = left.number_input("How many new topics", 1, 25, 10)
    privacy = right.selectbox("Privacy for the new batch", ["public", "unlisted", "private"])
    bgm_dir = st.text_input("Background music folder (optional)", value="")
    delete_after = st.checkbox("Delete local files after upload", value=True)

    if st.button("🚀 Generate topics and start a batch", type="primary"):
        with st.spinner("Asking the model for new topics…"):
            topics = youtube_stats.suggest_topics(
                winners,
                [row["title"] for row in research[:10]],
                count=int(count),
            )
        if not topics:
            st.error(
                "No new topics were generated. The model may be unavailable, or "
                "every suggestion duplicated a topic you have already made."
            )
            return

        batch_dir = launch_batch(topics, {
            "aspect": "9:16",
            "stop_at": "video",
            "video_count": 1,
            "bgm_dir": os.path.expanduser(bgm_dir) if bgm_dir else "",
            "language": "",
            "output_dir": "",
            "publish": True,
            "privacy": privacy,
            "delete_after_upload": delete_after,
            "random_voice": "en-US",
            "subtitle_position": "custom",
            "custom_position": 60.0,
            "font_size": 80,
            "stroke_width": 2.5,
            "font_name": "BeVietnamPro-Bold.ttf",
        })
        st.session_state["selected_batch"] = batch_dir
        st.success(f"Started a batch with {len(topics)} new topics.")
        for topic in topics:
            st.write(f"- {topic}")


def render_batches_tab() -> None:
    selected = st.session_state.get("selected_batch")
    running_now = selected and is_running(selected)
    with st.expander("▶ New batch", expanded=not running_now):
        render_launch_form()

    if selected:
        auto_refresh = st.sidebar.toggle("Auto-refresh while running", value=True)
        render_monitor(selected, live=bool(running_now and auto_refresh))
    else:
        st.info("Start a batch above to see it here.")


def main() -> None:
    st.title("MoneyPrinterTurbo — Batch")
    render_sidebar()

    batches_tab, performance_tab, research_tab, grow_tab = st.tabs(
        ["🎬 Batches", "📊 Performance", "🔎 Research", "🚀 Grow"]
    )
    with batches_tab:
        render_batches_tab()
    with performance_tab:
        render_performance()
    with research_tab:
        render_research()
    with grow_tab:
        render_grow()


# Streamlit 以 __main__ 执行脚本，因此该保护不影响界面运行，
# 同时让上面的纯函数可以被测试直接导入。
if __name__ == "__main__":
    main()

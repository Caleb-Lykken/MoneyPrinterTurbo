import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Batch.py 是 Streamlit 脚本，导入即执行页面配置；此处只测试其中的纯函数。
import webui.Batch as batch_ui  # noqa: E402


class TestBatchUiHelpers(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.batch_dir = os.path.join(self._tmp.name, "a-batch")
        os.makedirs(self.batch_dir, exist_ok=True)

    def test_paths_are_derived_from_the_batch_directory(self):
        paths = batch_ui.batch_paths(self.batch_dir)

        self.assertEqual(paths["manifest"], os.path.join(self.batch_dir, "manifest.json"))
        self.assertEqual(paths["log"], os.path.join(self.batch_dir, "run.log"))
        self.assertEqual(paths["pid"], os.path.join(self.batch_dir, "run.pid"))

    def test_missing_pid_file_means_not_running(self):
        self.assertFalse(batch_ui.is_running(self.batch_dir))

    def test_dead_pid_is_reported_as_not_running(self):
        with open(os.path.join(self.batch_dir, "run.pid"), "w") as handle:
            handle.write("999999")

        self.assertFalse(batch_ui.is_running(self.batch_dir))

    def test_own_pid_is_reported_as_running(self):
        with open(os.path.join(self.batch_dir, "run.pid"), "w") as handle:
            handle.write(str(os.getpid()))

        self.assertTrue(batch_ui.is_running(self.batch_dir))

    def test_finished_child_is_not_reported_as_running(self):
        """
        回归用例：子进程结束后若未被回收会变成僵尸，
        此时 os.kill(pid, 0) 仍然成功，界面会永远显示"运行中"。
        """
        import subprocess as sp

        child = sp.Popen(["true"])
        child.wait_called = False
        # 故意不调用 wait()，复现界面重跑之间无人回收子进程的情形。
        deadline = time.time() + 5
        while time.time() < deadline and batch_ui._process_state(child.pid) == "":
            time.sleep(0.05)
        while time.time() < deadline:
            if not batch_ui._process_state(child.pid).startswith("R"):
                break
            time.sleep(0.05)

        with open(os.path.join(self.batch_dir, "run.pid"), "w") as handle:
            handle.write(str(child.pid))

        self.assertFalse(batch_ui.is_running(self.batch_dir))

    def test_corrupt_manifest_reads_as_none_instead_of_raising(self):
        with open(os.path.join(self.batch_dir, "manifest.json"), "w") as handle:
            handle.write("{ broken")

        self.assertIsNone(batch_ui.read_manifest(self.batch_dir))

    def test_manifest_is_parsed(self):
        with open(os.path.join(self.batch_dir, "manifest.json"), "w") as handle:
            json.dump({"items": [{"index": 1}]}, handle)

        self.assertEqual(batch_ui.read_manifest(self.batch_dir)["items"][0]["index"], 1)

    def test_log_tail_strips_ansi_colour_codes(self):
        with open(os.path.join(self.batch_dir, "run.log"), "w") as handle:
            handle.write("\x1b[32mgreen line\x1b[0m\nplain line\n")

        tail = batch_ui.tail_log(self.batch_dir)

        self.assertIn("green line", tail)
        self.assertNotIn("\x1b[", tail)

    def test_missing_log_returns_empty_string(self):
        self.assertEqual(batch_ui.tail_log(self.batch_dir), "")


class TestLaunchCommand(unittest.TestCase):
    """启动命令拼错会让整个批次跑偏，这里逐项固定关键参数。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = patch.object(batch_ui, "batches_root", return_value=self._tmp.name)
        root.start()
        self.addCleanup(root.stop)

    def _launch(self, **overrides):
        options = {
            "aspect": "9:16",
            "stop_at": "video",
            "video_count": 1,
            "bgm_dir": "",
            "language": "",
            "output_dir": "",
            "publish": False,
            "privacy": "public",
            "delete_after_upload": False,
            "subtitle_position": "",
            "custom_position": 60.0,
            "font_size": 0,
            "stroke_width": 0,
            "font_name": "",
        }
        options.update(overrides)
        with patch("subprocess.Popen") as popen:
            popen.return_value.pid = 4242
            batch_dir = batch_ui.launch_batch(["Topic one", "Topic two"], options)
        return batch_dir, popen.call_args.args[0]

    def test_topics_are_written_for_the_cli_to_read(self):
        batch_dir, _ = self._launch()

        with open(os.path.join(batch_dir, "topics.txt"), encoding="utf-8") as handle:
            self.assertEqual(handle.read().split("\n")[:2], ["Topic one", "Topic two"])

    def test_pid_is_recorded_so_the_ui_can_reattach(self):
        batch_dir, _ = self._launch()

        with open(os.path.join(batch_dir, "run.pid"), encoding="utf-8") as handle:
            self.assertEqual(handle.read().strip(), "4242")

    def test_publishing_off_passes_no_publish(self):
        _, command = self._launch(publish=False)

        self.assertIn("--no-publish", command)
        self.assertNotIn("--publish", command)

    def test_publishing_on_passes_privacy(self):
        _, command = self._launch(publish=True, privacy="unlisted")

        self.assertIn("--publish", command)
        self.assertEqual(command[command.index("--youtube-privacy") + 1], "unlisted")
        self.assertNotIn("--no-publish", command)

    def test_delete_after_upload_is_passed_only_with_publishing(self):
        _, command = self._launch(publish=True, delete_after_upload=True)
        self.assertIn("--delete-after-upload", command)

        _, command = self._launch(publish=False, delete_after_upload=True)
        # 没有上传就删除本地成片等于丢数据，即使勾选也不能传下去。
        self.assertNotIn("--delete-after-upload", command)
        self.assertIn("--no-publish", command)

    def test_shorts_safe_subtitles_are_passed_through(self):
        _, command = self._launch(
            subtitle_position="custom", custom_position=60.0, font_size=80,
            stroke_width=2.5,
        )

        self.assertEqual(command[command.index("--subtitle-position") + 1], "custom")
        self.assertEqual(command[command.index("--custom-position") + 1], "60.0")
        self.assertEqual(command[command.index("--font-size") + 1], "80")
        self.assertEqual(command[command.index("--stroke-width") + 1], "2.5")

    def test_custom_position_is_omitted_for_preset_placements(self):
        _, command = self._launch(subtitle_position="bottom", font_size=80)

        # --custom-position 只有在 custom 模式下才被 CLI 接受。
        self.assertNotIn("--custom-position", command)
        self.assertEqual(command[command.index("--subtitle-position") + 1], "bottom")

    def test_font_name_is_passed_through(self):
        _, command = self._launch(font_name="BeVietnamPro-Bold.ttf")

        self.assertEqual(
            command[command.index("--font-name") + 1], "BeVietnamPro-Bold.ttf"
        )

    def test_optional_flags_are_omitted_when_empty(self):
        _, command = self._launch()

        for flag in ("--bgm-dir", "--batch-output-dir", "--video-language"):
            self.assertNotIn(flag, command)

    def test_optional_flags_are_passed_when_set(self):
        _, command = self._launch(
            bgm_dir="/music", output_dir="/out", language="en-US", stop_at="script"
        )

        self.assertEqual(command[command.index("--bgm-dir") + 1], "/music")
        self.assertEqual(command[command.index("--batch-output-dir") + 1], "/out")
        self.assertEqual(command[command.index("--video-language") + 1], "en-US")
        self.assertEqual(command[command.index("--stop-at") + 1], "script")

    def test_batch_runs_in_its_own_session(self):
        with patch("subprocess.Popen") as popen:
            popen.return_value.pid = 1
            batch_ui.launch_batch(["Topic"], {
                "aspect": "9:16", "stop_at": "video", "video_count": 1,
                "bgm_dir": "", "language": "", "output_dir": "",
                "publish": False, "privacy": "public",
            })

        # 独立会话是刷新页面不会杀死渲染的前提。
        self.assertTrue(popen.call_args.kwargs["start_new_session"])


class TestDiscovery(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = patch.object(batch_ui, "batches_root", return_value=self._tmp.name)
        root.start()
        self.addCleanup(root.stop)

    def _make(self, name, manifest=True, pid=None):
        directory = os.path.join(self._tmp.name, name)
        os.makedirs(directory, exist_ok=True)
        if manifest:
            with open(os.path.join(directory, "manifest.json"), "w") as handle:
                json.dump({"items": []}, handle)
        if pid is not None:
            with open(os.path.join(directory, "run.pid"), "w") as handle:
                handle.write(str(pid))
        return directory

    def test_empty_directories_are_ignored(self):
        self._make("empty", manifest=False)

        self.assertEqual(batch_ui.discover_batches(), [])

    def test_a_starting_batch_appears_before_its_manifest_exists(self):
        self._make("starting", manifest=False, pid=os.getpid())

        found = batch_ui.discover_batches()

        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]["starting"])
        self.assertTrue(found[0]["running"])

    def test_running_batches_sort_first(self):
        self._make("finished", manifest=True)
        self._make("live", manifest=True, pid=os.getpid())

        found = batch_ui.discover_batches()

        self.assertEqual(found[0]["id"], "live")
        self.assertTrue(found[0]["running"])


if __name__ == "__main__":
    unittest.main()

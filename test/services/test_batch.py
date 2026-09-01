import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import cli
from app.models import const
from app.services import batch


def _failed(stage="materials", error="boom"):
    return {"state": const.TASK_STATE_FAILED, "failed_stage": stage, "error": error}


def _ok(videos):
    return {"state": const.TASK_STATE_COMPLETE, "videos": list(videos)}


class TestLoadSubjects(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "topics.txt")

    def _write(self, content):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return self.path

    def test_skips_blank_lines_and_comments(self):
        path = self._write(
            "# a comment\n"
            "  How AI changes daily life  \n"
            "\n"
            "   \n"
            "# another comment\n"
            "人工智能如何改变日常生活\n"
        )

        self.assertEqual(
            batch.load_subjects(path),
            ["How AI changes daily life", "人工智能如何改变日常生活"],
        )

    def test_empty_file_is_rejected(self):
        path = self._write("\n# only comments\n\n")

        with self.assertRaises(batch.BatchInputError):
            batch.load_subjects(path)

    def test_missing_file_is_rejected(self):
        with self.assertRaises(batch.BatchInputError):
            batch.load_subjects(os.path.join(self._tmp.name, "nope.txt"))


class TestSlug(unittest.TestCase):
    def test_index_prefix_disambiguates_identical_subjects(self):
        self.assertEqual(
            batch._slug_for_subject("Why sleep matters!", 1), "01-Why-sleep-matters"
        )
        self.assertEqual(
            batch._slug_for_subject("Why sleep? Matters...", 2), "02-Why-sleep-Matters"
        )

    def test_windows_reserved_name_is_never_bare(self):
        self.assertEqual(batch._slug_for_subject("CON", 3), "03-CON")

    def test_subject_without_usable_characters_still_yields_a_name(self):
        self.assertEqual(batch._slug_for_subject("!!! ???", 4), "04-video")


class BatchRunTestCase(unittest.TestCase):
    """批量运行的公共脚手架：所有外部调用都被替换，不产生真实请求。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.manifest_path = os.path.join(self._tmp.name, "manifest.json")
        self.topics_path = os.path.join(self._tmp.name, "topics.txt")
        with open(self.topics_path, "w", encoding="utf-8") as handle:
            handle.write("Topic one\nTopic two\nTopic three\n")

        # 默认关闭发布，避免用例意外走到 YouTube 分支。
        enabled = patch("app.services.youtube.is_enabled", return_value=False)
        enabled.start()
        self.addCleanup(enabled.stop)

    def _argv(self, *extra):
        return [
            "--subjects-file",
            self.topics_path,
            "--batch-manifest",
            self.manifest_path,
            *extra,
        ]

    def _resume_argv(self, *extra):
        """续跑时只指定清单，主题来源必须省略。"""
        return ["--batch-manifest", self.manifest_path, *extra]

    def _run(self, *extra, scripts=None, start=None):
        return self._invoke(self._argv(*extra), scripts=scripts, start=start)

    def _resume(self, *extra, scripts=None, start=None):
        return self._invoke(self._resume_argv(*extra), scripts=scripts, start=start)

    def _invoke(self, argv, scripts=None, start=None):
        scripts = scripts if scripts is not None else (lambda **kw: "a script")
        start = start if start is not None else (lambda **kw: _ok(["/videos/final-1.mp4"]))
        with patch("app.services.llm.generate_script", side_effect=scripts) as script_mock, patch(
            "app.services.task.start", side_effect=start
        ) as start_mock, patch("builtins.print") as print_mock:
            code = cli.run_cli(argv)
        self.script_mock = script_mock
        self.start_mock = start_mock
        self.summary = (
            json.loads(print_mock.call_args.args[0]) if print_mock.call_args else None
        )
        return code

    def _manifest(self):
        with open(self.manifest_path, "r", encoding="utf-8") as handle:
            return json.load(handle)


class TestScriptPhase(BatchRunTestCase):
    def test_one_script_per_topic_is_persisted(self):
        code = self._run(
            "--stop-at",
            "script",
            scripts=lambda **kw: f"script for {kw['video_subject']}",
        )

        self.assertEqual(code, 0)
        items = self._manifest()["items"]
        self.assertEqual([item["subject"] for item in items], ["Topic one", "Topic two", "Topic three"])
        self.assertEqual(
            [item["script"] for item in items],
            ["script for Topic one", "script for Topic two", "script for Topic three"],
        )
        self.assertEqual(self.summary["scripts"], 3)

    def test_stop_at_script_never_renders(self):
        self._run("--stop-at", "script")

        self.start_mock.assert_not_called()

    def test_one_failed_script_does_not_stop_the_batch(self):
        def scripts(**kwargs):
            if kwargs["video_subject"] == "Topic two":
                return "Error: quota exceeded"
            return "a script"

        code = self._run("--stop-at", "script", scripts=scripts)

        items = self._manifest()["items"]
        self.assertEqual(items[1]["status"], batch.STATUS_SCRIPT_FAILED)
        self.assertEqual(items[1]["error"], "quota exceeded")
        self.assertTrue(items[0]["script"] and items[2]["script"])
        self.assertEqual(code, 1)

    def test_crashing_llm_is_contained_to_one_topic(self):
        def scripts(**kwargs):
            if kwargs["video_subject"] == "Topic one":
                raise RuntimeError("network down")
            return "a script"

        self._run("--stop-at", "script", scripts=scripts)

        items = self._manifest()["items"]
        self.assertEqual(items[0]["status"], batch.STATUS_SCRIPT_FAILED)
        self.assertTrue(items[1]["script"] and items[2]["script"])


class TestRenderPhase(BatchRunTestCase):
    def test_renders_every_topic_with_its_own_script(self):
        code = self._run(scripts=lambda **kw: f"script for {kw['video_subject']}")

        self.assertEqual(code, 0)
        self.assertEqual(self.start_mock.call_count, 3)
        subjects = [call.kwargs["params"].video_subject for call in self.start_mock.call_args_list]
        scripts = [call.kwargs["params"].video_script for call in self.start_mock.call_args_list]
        self.assertEqual(subjects, ["Topic one", "Topic two", "Topic three"])
        self.assertEqual(scripts, [f"script for {name}" for name in subjects])

    def test_each_topic_gets_an_independent_params_object(self):
        self._run()

        params = [call.kwargs["params"] for call in self.start_mock.call_args_list]
        self.assertEqual(len({id(item) for item in params}), 3)
        # 深拷贝失败时改动一个任务的参数会污染其他任务。
        params[0].video_subject = "mutated"
        self.assertEqual(params[1].video_subject, "Topic two")

    def test_each_topic_gets_a_unique_task_id(self):
        self._run()

        task_ids = [call.kwargs["task_id"] for call in self.start_mock.call_args_list]
        self.assertEqual(len(set(task_ids)), 3)

    def test_failed_render_is_recorded_and_batch_continues(self):
        def start(**kwargs):
            if kwargs["params"].video_subject == "Topic two":
                return _failed(stage="materials", error="no footage found")
            return _ok(["/videos/final-1.mp4"])

        code = self._run(start=start)

        self.assertEqual(code, 1)
        items = self._manifest()["items"]
        self.assertEqual(items[1]["status"], batch.STATUS_FAILED)
        self.assertEqual(items[1]["failed_stage"], "materials")
        self.assertEqual(items[1]["error"], "no footage found")
        self.assertEqual(items[0]["status"], batch.STATUS_DONE)
        self.assertEqual(items[2]["status"], batch.STATUS_DONE)
        self.assertEqual(self.summary["succeeded"], 2)
        self.assertEqual(self.summary["failed"], 1)

    def test_unexpected_render_exception_is_contained(self):
        def start(**kwargs):
            if kwargs["params"].video_subject == "Topic one":
                raise RuntimeError("ffmpeg exploded")
            return _ok(["/videos/final-1.mp4"])

        code = self._run(start=start)

        self.assertEqual(code, 1)
        items = self._manifest()["items"]
        self.assertEqual(items[0]["status"], batch.STATUS_FAILED)
        self.assertEqual(self.start_mock.call_count, 3)

    def test_output_dir_collects_videos_with_readable_names(self):
        source_dir = os.path.join(self._tmp.name, "tasks")
        os.makedirs(source_dir, exist_ok=True)
        rendered = os.path.join(source_dir, "final-1.mp4")
        with open(rendered, "wb") as handle:
            handle.write(b"video-bytes")
        output_dir = os.path.join(self._tmp.name, "out")

        self._run("--batch-output-dir", output_dir, start=lambda **kw: _ok([rendered]))

        self.assertEqual(
            sorted(os.listdir(output_dir)),
            ["01-Topic-one.mp4", "02-Topic-two.mp4", "03-Topic-three.mp4"],
        )


class TestResume(BatchRunTestCase):
    def test_finished_topics_are_not_rendered_again(self):
        self._run()
        self.assertEqual(self.start_mock.call_count, 3)

        code = self._resume()

        self.assertEqual(code, 0)
        self.start_mock.assert_not_called()
        self.script_mock.assert_not_called()

    def test_failed_topics_are_retried_on_resume(self):
        calls = {"n": 0}

        def start(**kwargs):
            calls["n"] += 1
            if kwargs["params"].video_subject == "Topic two" and calls["n"] <= 3:
                return _failed()
            return _ok(["/videos/final-1.mp4"])

        self._run(start=start)
        code = self._resume(start=start)

        self.assertEqual(code, 0)
        self.assertEqual(self.start_mock.call_count, 1)
        self.assertEqual(
            self.start_mock.call_args.kwargs["params"].video_subject, "Topic two"
        )

    def test_resume_reuses_manifest_parameters(self):
        self._run("--stop-at", "script", "--video-aspect", "16:9", "--font-size", "44")

        code = self._resume()

        self.assertEqual(code, 0)
        params = self.start_mock.call_args.kwargs["params"]
        self.assertEqual(params.video_aspect, "16:9")
        self.assertEqual(params.font_size, 44)

    def test_subjects_cannot_be_added_to_an_existing_manifest(self):
        self._run("--stop-at", "script")

        with patch("builtins.print"):
            code = cli.run_cli(
                [
                    "--subjects",
                    "A new topic",
                    "--batch-manifest",
                    self.manifest_path,
                ]
            )
        self.assertEqual(code, 2)

    def test_corrupt_manifest_is_reported_as_an_input_error(self):
        with open(self.manifest_path, "w", encoding="utf-8") as handle:
            handle.write("{not json")

        with patch("builtins.print"):
            code = cli.run_cli(["--batch-manifest", self.manifest_path])

        self.assertEqual(code, 2)


class TestPartialStageResume(BatchRunTestCase):
    """中间阶段停止后，续跑必须能继续把流程推进到完整成片。"""

    def test_intermediate_stage_does_not_block_a_later_full_render(self):
        # --stop-at terms 会把主题标记为完成，但并没有产出成片。
        self._run("--stop-at", "terms", start=lambda **kw: {"script": "s", "terms": ["t"]})
        self.assertEqual(self.start_mock.call_count, 3)

        code = self._resume()

        self.assertEqual(code, 0)
        self.assertEqual(self.start_mock.call_count, 3)
        self.assertEqual(
            [call.kwargs["stop_at"] for call in self.start_mock.call_args_list],
            ["video"] * 3,
        )

    def test_completed_stage_is_recorded(self):
        self._run("--stop-at", "terms", start=lambda **kw: {"script": "s", "terms": ["t"]})

        items = self._manifest()["items"]
        self.assertEqual([item["completed_stage"] for item in items], ["terms"] * 3)

    def test_a_later_run_at_an_earlier_stage_is_skipped(self):
        self._run()

        code = self._resume("--stop-at", "terms")

        self.assertEqual(code, 0)
        # 已经跑完完整成片的主题不需要为更早的阶段重跑。
        self.start_mock.assert_not_called()

    def test_legacy_manifest_without_completed_stage_still_resumes(self):
        self._run()

        manifest = self._manifest()
        for item in manifest["items"]:
            del item["completed_stage"]
        with open(self.manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle)

        code = self._resume()

        self.assertEqual(code, 0)
        # 旧清单没有阶段字段，但有成片，应当按已完成处理。
        self.start_mock.assert_not_called()


class TestBgmRotation(BatchRunTestCase):
    def setUp(self):
        super().setUp()
        self.music_dir = os.path.join(self._tmp.name, "music")
        os.makedirs(self.music_dir, exist_ok=True)
        for index in range(1, 3):
            with open(os.path.join(self.music_dir, f"track-{index}.m4a"), "wb") as handle:
                handle.write(f"audio-{index}".encode())
        # 非音频文件必须被忽略，否则会被当成曲目交给渲染层。
        with open(os.path.join(self.music_dir, "notes.txt"), "w") as handle:
            handle.write("not audio")

        managed = os.path.join(self._tmp.name, "managed-bgm")
        os.makedirs(managed, exist_ok=True)
        patcher = patch("app.services.bgm.uploaded_bgm_dir", return_value=managed)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.managed = managed

    def test_tracks_are_copied_and_unsupported_files_ignored(self):
        tracks = batch.prepare_bgm_tracks(self.music_dir)

        self.assertEqual(tracks, ["track-1.m4a", "track-2.m4a"])
        self.assertEqual(sorted(os.listdir(self.managed)), ["track-1.m4a", "track-2.m4a"])

    def test_identical_files_are_not_copied_twice(self):
        batch.prepare_bgm_tracks(self.music_dir)
        before = {
            name: os.stat(os.path.join(self.managed, name)).st_mtime_ns
            for name in os.listdir(self.managed)
        }

        batch.prepare_bgm_tracks(self.music_dir)

        after = {
            name: os.stat(os.path.join(self.managed, name)).st_mtime_ns
            for name in os.listdir(self.managed)
        }
        self.assertEqual(before, after)

    def test_directory_without_audio_is_rejected(self):
        empty = os.path.join(self._tmp.name, "empty")
        os.makedirs(empty, exist_ok=True)

        with self.assertRaises(batch.BatchInputError):
            batch.prepare_bgm_tracks(empty)

    def test_tracks_rotate_across_topics(self):
        self._run("--bgm-dir", self.music_dir)

        params = [call.kwargs["params"] for call in self.start_mock.call_args_list]
        # 3 个主题、2 首曲目，第 3 个主题回到第 1 首。
        self.assertEqual(
            [item.bgm_file for item in params],
            ["track-1.m4a", "track-2.m4a", "track-1.m4a"],
        )
        self.assertTrue(all(item.bgm_type == "custom" for item in params))

    def test_assigned_track_is_recorded_for_reproducible_resume(self):
        self._run("--bgm-dir", self.music_dir)

        items = self._manifest()["items"]
        self.assertEqual(
            [item["bgm_file"] for item in items],
            ["track-1.m4a", "track-2.m4a", "track-1.m4a"],
        )

    def test_resume_reuses_the_recorded_track(self):
        def start(**kwargs):
            if kwargs["params"].video_subject == "Topic two":
                return _failed()
            return _ok(["/videos/final-1.mp4"])

        self._run("--bgm-dir", self.music_dir, start=start)
        self._resume()

        self.assertEqual(
            self.start_mock.call_args.kwargs["params"].bgm_file, "track-2.m4a"
        )

    def test_without_bgm_dir_params_are_untouched(self):
        self._run()

        params = self.start_mock.call_args.kwargs["params"]
        self.assertEqual(params.bgm_type, "random")
        self.assertEqual(params.bgm_file, "")


class TestBgmDirValidation(unittest.TestCase):
    def test_bgm_dir_requires_batch_mode(self):
        with self.assertRaises(SystemExit):
            cli.parse_args(["--video-subject", "x", "--bgm-dir", "/tmp/music"])

    def test_bgm_dir_conflicts_with_bgm_file(self):
        with self.assertRaises(SystemExit):
            cli.parse_args(
                ["--subjects", "a", "--bgm-dir", "/tmp/music", "--bgm-file", "x.mp3"]
            )

    def test_bgm_dir_conflicts_with_other_bgm_types(self):
        with self.assertRaises(SystemExit):
            cli.parse_args(
                ["--subjects", "a", "--bgm-dir", "/tmp/music", "--bgm-type", "sonilo"]
            )


class TestDeleteAfterUpload(unittest.TestCase):
    """删除本地成片不可逆，因此每一条前置条件都必须有用例守住。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tasks_root = os.path.join(self._tmp.name, "tasks")
        os.makedirs(self.tasks_root, exist_ok=True)
        patcher = patch("app.utils.utils.task_dir", return_value=self.tasks_root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _task(self, task_id="task-1", size=1024):
        directory = os.path.join(self.tasks_root, task_id)
        os.makedirs(directory, exist_ok=True)
        for name in ("final-1.mp4", "combined-1.mp4"):
            with open(os.path.join(directory, name), "wb") as handle:
                handle.write(b"x" * size)
        return directory

    def _item(self, task_id="task-1", video_id="vid"):
        return {
            "index": 1,
            "task_id": task_id,
            "videos": [os.path.join(self.tasks_root, task_id, "final-1.mp4")],
            "youtube": {"video_id": video_id} if video_id else None,
        }

    def test_files_are_deleted_after_a_verified_upload(self):
        directory = self._task()
        item = self._item()

        batch._maybe_cleanup(item, delete_after_upload=True)

        self.assertFalse(os.path.exists(directory))
        self.assertTrue(item["local_cleanup"]["deleted"])
        self.assertEqual(item["local_cleanup"]["freed_bytes"], 2048)
        # 路径不再有效，但保留原值便于追溯。
        self.assertEqual(item["videos"], [])
        self.assertEqual(len(item["videos_deleted"]), 1)

    def test_nothing_is_deleted_without_the_flag(self):
        directory = self._task()

        batch._maybe_cleanup(self._item(), delete_after_upload=False)

        self.assertTrue(os.path.isdir(directory))

    def test_nothing_is_deleted_without_an_upload_id(self):
        directory = self._task()
        item = self._item(video_id=None)

        batch._maybe_cleanup(item, delete_after_upload=True)

        # 没有远端副本就删除本地文件等于直接丢数据。
        self.assertTrue(os.path.isdir(directory))
        self.assertIsNone(item.get("local_cleanup"))

    def test_failed_upload_keeps_local_files(self):
        directory = self._task()
        item = self._item(video_id=None)
        item["youtube"] = {"error": "upload failed"}

        batch._maybe_cleanup(item, delete_after_upload=True)

        self.assertTrue(os.path.isdir(directory))

    def test_cleanup_is_not_repeated(self):
        self._task()
        item = self._item()
        batch._maybe_cleanup(item, delete_after_upload=True)
        first = item["local_cleanup"]

        batch._maybe_cleanup(item, delete_after_upload=True)

        self.assertIs(item["local_cleanup"], first)

    def test_path_outside_the_tasks_directory_is_refused(self):
        outside = os.path.join(self._tmp.name, "not-a-task")
        os.makedirs(outside, exist_ok=True)
        with open(os.path.join(outside, "keep.txt"), "w") as handle:
            handle.write("important")
        item = self._item(task_id="../not-a-task")

        batch._maybe_cleanup(item, delete_after_upload=True)

        # 清单被手工编辑或损坏时，删除操作绝不能逃出 storage/tasks。
        self.assertTrue(os.path.isfile(os.path.join(outside, "keep.txt")))

    def test_missing_task_directory_is_not_an_error(self):
        item = self._item(task_id="never-existed")

        batch._maybe_cleanup(item, delete_after_upload=True)

        self.assertIsNone(item.get("local_cleanup"))


class TestDeleteAfterUploadWiring(BatchRunTestCase):
    def test_flag_is_ignored_when_publishing_is_off(self):
        with patch.object(batch, "_maybe_cleanup") as cleanup:
            self._run("--no-publish")

        # --no-publish 时不会有任何上传，也就不该发生删除。
        cleanup.assert_not_called()

    def test_flag_conflicts_with_no_publish(self):
        with self.assertRaises(SystemExit):
            cli.parse_args(
                ["--subjects", "a", "--delete-after-upload", "--no-publish"]
            )

    def test_collected_copies_are_real_copies_when_deleting(self):
        source_dir = os.path.join(self._tmp.name, "src")
        os.makedirs(source_dir, exist_ok=True)
        source = os.path.join(source_dir, "final-1.mp4")
        with open(source, "wb") as handle:
            handle.write(b"video")
        output_dir = os.path.join(self._tmp.name, "out")

        collected = batch._collect_output(source, output_dir, "01-x", prefer_copy=True)

        # 硬链接与原文件共用 inode，删除任务目录不会释放空间。
        self.assertNotEqual(os.stat(source).st_ino, os.stat(collected).st_ino)

    def test_collected_copies_hardlink_by_default(self):
        source_dir = os.path.join(self._tmp.name, "src2")
        os.makedirs(source_dir, exist_ok=True)
        source = os.path.join(source_dir, "final-1.mp4")
        with open(source, "wb") as handle:
            handle.write(b"video")
        output_dir = os.path.join(self._tmp.name, "out2")

        collected = batch._collect_output(source, output_dir, "01-x")

        self.assertEqual(os.stat(source).st_ino, os.stat(collected).st_ino)


class TestVoicePool(unittest.TestCase):
    def test_v2_voices_are_excluded(self):
        with patch(
            "app.services.voice.get_all_azure_voices",
            return_value=[
                "en-US-AriaNeural-Female",
                "en-US-AndrewMultilingualNeural-V2-Male",
            ],
        ):
            pool = batch.prepare_voice_pool("en-US")

        # V2 音色需要 Azure 密钥，未配置时会让整条流水线在配音阶段失败。
        self.assertEqual(pool, ["en-US-AriaNeural-Female"])

    def test_child_voice_is_excluded(self):
        with patch(
            "app.services.voice.get_all_azure_voices",
            return_value=["en-US-AnaNeural-Female", "en-US-GuyNeural-Male"],
        ):
            pool = batch.prepare_voice_pool("en-US")

        self.assertEqual(pool, ["en-US-GuyNeural-Male"])

    def test_locale_without_usable_voices_is_rejected(self):
        with patch("app.services.voice.get_all_azure_voices", return_value=[]):
            with self.assertRaises(batch.BatchInputError):
                batch.prepare_voice_pool("xx-XX")

    def test_empty_locale_is_rejected(self):
        with self.assertRaises(batch.BatchInputError):
            batch.prepare_voice_pool("")


class TestRandomVoice(BatchRunTestCase):
    def setUp(self):
        super().setUp()
        patcher = patch(
            "app.services.voice.get_all_azure_voices",
            return_value=["en-US-AriaNeural-Female", "en-US-GuyNeural-Male"],
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_every_topic_gets_a_us_voice(self):
        self._run("--random-voice", "en-US")

        voices = [
            call.kwargs["params"].voice_name
            for call in self.start_mock.call_args_list
        ]
        self.assertEqual(len(voices), 3)
        for name in voices:
            self.assertTrue(name.startswith("en-US-"), name)

    def test_chosen_voice_is_recorded_for_reproducible_resume(self):
        self._run("--random-voice", "en-US")

        recorded = [item["voice_name"] for item in self._manifest()["items"]]
        self.assertTrue(all(name and name.startswith("en-US-") for name in recorded))

    def test_resume_reuses_the_recorded_voice(self):
        def start(**kwargs):
            if kwargs["params"].video_subject == "Topic two":
                return _failed()
            return _ok(["/videos/final-1.mp4"])

        self._run("--random-voice", "en-US", start=start)
        expected = self._manifest()["items"][1]["voice_name"]
        self._resume()

        self.assertEqual(
            self.start_mock.call_args.kwargs["params"].voice_name, expected
        )

    def test_without_the_flag_the_voice_is_untouched(self):
        self._run()

        params = self.start_mock.call_args.kwargs["params"]
        self.assertEqual(params.voice_name, cli.DEFAULT_VOICE_NAME)


class TestVoiceRetry(BatchRunTestCase):
    def setUp(self):
        super().setUp()
        patcher = patch(
            "app.services.voice.get_all_azure_voices",
            return_value=["en-US-AriaNeural-Female", "en-US-GuyNeural-Male"],
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_audio_failure_clears_the_voice_for_retry(self):
        def start(**kwargs):
            return _failed(stage="audio", error="failed to synthesize audio")

        self._run("--random-voice", "en-US", start=start)

        items = self._manifest()["items"]
        # 保留失败音色会让续跑用同一个音色反复失败。
        self.assertTrue(all(item["voice_name"] is None for item in items))
        self.assertTrue(all(item["failed_stage"] == "audio" for item in items))

    def test_non_audio_failure_keeps_the_voice(self):
        def start(**kwargs):
            return _failed(stage="materials", error="no footage")

        self._run("--random-voice", "en-US", start=start)

        items = self._manifest()["items"]
        # 素材失败与音色无关，重跑应保持一致以便复现。
        self.assertTrue(all(item["voice_name"] for item in items))


class TestRandomVoiceValidation(unittest.TestCase):
    def test_requires_batch_mode(self):
        with self.assertRaises(SystemExit):
            cli.parse_args(["--video-subject", "x", "--random-voice", "en-US"])

    def test_conflicts_with_explicit_voice_name(self):
        with self.assertRaises(SystemExit):
            cli.parse_args(
                ["--subjects", "a", "--random-voice", "--voice-name", "en-US-GuyNeural-Male"]
            )

    def test_bare_flag_defaults_to_en_us(self):
        args = cli.parse_args(["--subjects", "a", "--random-voice"])

        self.assertEqual(args.random_voice, "en-US")


class TestPublishLimit(BatchRunTestCase):
    """Shorts 冷启动人群很小，集中发布会让视频互相争夺同一批测试观众。"""

    def setUp(self):
        super().setUp()
        enabled = patch("app.services.youtube.is_enabled", return_value=True)
        enabled.start()
        self.addCleanup(enabled.stop)
        auth = patch("app.services.youtube.ensure_authorized", return_value=(True, ""))
        auth.start()
        self.addCleanup(auth.stop)
        meta = patch(
            "app.services.youtube.build_metadata",
            return_value={"title": "T", "description": "D", "tags": []},
        )
        meta.start()
        self.addCleanup(meta.stop)

    def _upload(self):
        counter = {"n": 0}

        def upload(*args, **kwargs):
            counter["n"] += 1
            return {
                "video_id": f"v{counter['n']}",
                "url": f"https://youtu.be/v{counter['n']}",
                "privacy_status": "public",
                "requested_privacy_status": "public",
            }

        return upload, counter

    def test_only_the_allowed_number_is_uploaded(self):
        upload, counter = self._upload()
        with patch("app.services.youtube.upload_video", side_effect=upload):
            self._run("--publish", "--publish-limit", "2")

        self.assertEqual(counter["n"], 2)

    def test_remaining_videos_are_kept_for_a_later_run(self):
        upload, counter = self._upload()
        with patch("app.services.youtube.upload_video", side_effect=upload):
            self._run("--publish", "--publish-limit", "1")

        items = self._manifest()["items"]
        held = [i for i in items if i["videos"] and not (i.get("youtube") or {}).get("video_id")]
        # 未发布的成片必须保留，等待后续分批上传。
        self.assertEqual(len(held), 2)
        self.assertTrue(all(i["status"] == batch.STATUS_DONE for i in held))

    def test_a_later_run_publishes_the_rest(self):
        upload, counter = self._upload()
        with patch("app.services.youtube.upload_video", side_effect=upload):
            self._run("--publish", "--publish-limit", "1")
            self._resume("--publish", "--publish-limit", "1")

        self.assertEqual(counter["n"], 2)

    def test_no_limit_publishes_everything(self):
        upload, counter = self._upload()
        with patch("app.services.youtube.upload_video", side_effect=upload):
            self._run("--publish")

        self.assertEqual(counter["n"], 3)


class TestPublishLimitValidation(unittest.TestCase):
    def test_conflicts_with_no_publish(self):
        with self.assertRaises(SystemExit):
            cli.parse_args(["--subjects", "a", "--publish-limit", "2", "--no-publish"])

    def test_must_be_positive(self):
        with self.assertRaises(SystemExit):
            cli.parse_args(["--subjects", "a", "--publish-limit", "0"])


class TestStageOrder(unittest.TestCase):
    def test_stage_order_matches_the_cli_pipeline(self):
        # 阶段顺序在两处各有一份定义，漂移会让续跑判断失效。
        self.assertEqual(batch._STAGE_ORDER, cli._PIPELINE_STAGES)


class TestManifestRoundTrip(unittest.TestCase):
    def test_base_params_survive_a_save_and_load_cycle(self):
        from app.models.schema import VideoParams

        params = VideoParams(
            video_subject="ignored",
            video_script="ignored",
            video_aspect="16:9",
            video_count=2,
            font_size=48,
            video_terms=["one", "two"],
        )
        manifest = batch._new_manifest(["A", "B"], params, "video")

        restored = VideoParams(**json.loads(json.dumps(manifest["base_params"])))

        self.assertEqual(restored.video_aspect, "16:9")
        self.assertEqual(restored.video_count, 2)
        self.assertEqual(restored.font_size, 48)
        self.assertEqual(restored.video_terms, ["one", "two"])
        # 主题和文案属于逐条数据，不能混进公共参数。
        self.assertEqual(restored.video_subject, "")
        self.assertEqual(restored.video_script, "")


if __name__ == "__main__":
    unittest.main()

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import cli
from app.models import const
from app.services import batch, youtube


class FakeResponse:
    def __init__(self, status):
        self.status = status


class FakeHttpError(Exception):
    """替身 googleapiclient.errors.HttpError，保持 resp/content 结构一致。"""

    def __init__(self, status, content=b""):
        super().__init__(f"HttpError {status}")
        self.resp = FakeResponse(status)
        self.content = content


def _modules(insert_result=None, insert_error=None):
    """构造被完全替换的 Google 客户端层，用例中不发生任何网络请求。"""
    request = MagicMock()
    if insert_error is not None:
        request.next_chunk.side_effect = insert_error
    else:
        request.next_chunk.return_value = (None, insert_result)

    service = MagicMock()
    service.videos.return_value.insert.return_value = request

    return {
        "Request": MagicMock(),
        "Credentials": MagicMock(),
        "InstalledAppFlow": MagicMock(),
        "build": MagicMock(return_value=service),
        "HttpError": FakeHttpError,
        "MediaFileUpload": MagicMock(),
    }, service


class YouTubeTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.video_path = os.path.join(self._tmp.name, "final-1.mp4")
        with open(self.video_path, "wb") as handle:
            handle.write(b"video-bytes")

        credentials = patch.object(youtube, "_load_credentials", return_value=MagicMock())
        credentials.start()
        self.addCleanup(credentials.stop)


class TestUploadRequest(YouTubeTestCase):
    def _upload(self, response, **kwargs):
        modules, service = _modules(insert_result=response)
        with patch.object(youtube, "_google_modules", return_value=modules):
            result = youtube.upload_video(self.video_path, **kwargs)
        body = service.videos.return_value.insert.call_args.kwargs["body"]
        return result, body

    def test_request_body_carries_required_fields(self):
        result, body = self._upload(
            {"id": "abc123", "status": {"privacyStatus": "public"}},
            title="A title",
            description="A description",
            tags=["ai", "tech"],
            privacy_status="public",
            category_id="27",
        )

        self.assertEqual(body["snippet"]["title"], "A title")
        self.assertEqual(body["snippet"]["tags"], ["ai", "tech"])
        self.assertEqual(body["snippet"]["categoryId"], "27")
        self.assertEqual(body["status"]["privacyStatus"], "public")
        # 生成内容必须按平台要求声明，否则可能违反披露规则。
        self.assertIs(body["status"]["containsSyntheticMedia"], True)
        self.assertEqual(result["video_id"], "abc123")
        self.assertEqual(result["url"], "https://youtu.be/abc123")

    def test_title_is_truncated_to_the_platform_limit(self):
        _, body = self._upload(
            {"id": "abc123", "status": {"privacyStatus": "private"}},
            title="T" * 250,
            privacy_status="private",
        )

        self.assertEqual(len(body["snippet"]["title"]), youtube.MAX_TITLE_LENGTH)

    def test_unknown_privacy_status_falls_back_to_private(self):
        _, body = self._upload(
            {"id": "abc123", "status": {"privacyStatus": "private"}},
            title="A title",
            privacy_status="semi-public",
        )

        self.assertEqual(body["status"]["privacyStatus"], "private")

    def test_forced_private_is_surfaced_not_reported_as_success(self):
        result, _ = self._upload(
            {"id": "abc123", "status": {"privacyStatus": "private"}},
            title="A title",
            privacy_status="public",
        )

        # 未通过审核的项目会把 public 覆盖成 private，两个值都必须保留下来。
        self.assertEqual(result["privacy_status"], "private")
        self.assertEqual(result["requested_privacy_status"], "public")

    def test_missing_video_id_is_an_error(self):
        modules, _ = _modules(insert_result={"status": {"privacyStatus": "public"}})
        with patch.object(youtube, "_google_modules", return_value=modules):
            with self.assertRaises(youtube.YouTubeError):
                youtube.upload_video(self.video_path, title="A title")

    def test_missing_file_is_rejected_before_any_api_call(self):
        modules, service = _modules(insert_result={"id": "x"})
        with patch.object(youtube, "_google_modules", return_value=modules):
            with self.assertRaises(youtube.YouTubeError):
                youtube.upload_video(
                    os.path.join(self._tmp.name, "nope.mp4"), title="A title"
                )
        service.videos.assert_not_called()


class TestUploadErrors(YouTubeTestCase):
    def _upload_expecting_error(self, error):
        modules, _ = _modules(insert_error=error)
        with patch.object(youtube, "_google_modules", return_value=modules):
            with self.assertRaises(youtube.YouTubeError) as caught:
                youtube.upload_video(self.video_path, title="A title")
        return caught.exception

    def test_quota_error_is_flagged_for_the_caller(self):
        error = self._upload_expecting_error(
            FakeHttpError(403, b'{"error":{"errors":[{"reason":"quotaExceeded"}]}}')
        )

        self.assertTrue(error.quota_exceeded)

    def test_upload_limit_is_also_treated_as_quota(self):
        error = self._upload_expecting_error(
            FakeHttpError(403, b'{"error":{"errors":[{"reason":"uploadLimitExceeded"}]}}')
        )

        self.assertTrue(error.quota_exceeded)

    def test_channel_upload_limit_returns_http_400_and_still_counts_as_quota(self):
        """回归用例：频道每日上传上限返回 400，按状态码过滤会漏判。"""
        error = self._upload_expecting_error(
            FakeHttpError(
                400,
                b'{"error":{"errors":[{"reason":"uploadLimitExceeded",'
                b'"message":"The user has exceeded the number of videos they may upload."}]}}',
            )
        )

        self.assertTrue(error.quota_exceeded)

    def test_permission_error_is_not_treated_as_quota(self):
        error = self._upload_expecting_error(
            FakeHttpError(403, b'{"error":{"errors":[{"reason":"forbidden"}]}}')
        )

        self.assertFalse(error.quota_exceeded)

    def test_client_error_is_not_retried(self):
        modules, service = _modules(insert_error=FakeHttpError(400, b"bad request"))
        with patch.object(youtube, "_google_modules", return_value=modules):
            with self.assertRaises(youtube.YouTubeError):
                youtube.upload_video(self.video_path, title="A title")

        request = service.videos.return_value.insert.return_value
        self.assertEqual(request.next_chunk.call_count, 1)

    def test_server_error_is_retried_then_gives_up(self):
        modules, service = _modules(insert_error=FakeHttpError(503, b"unavailable"))
        with patch.object(youtube, "_google_modules", return_value=modules), patch(
            "app.services.youtube.time.sleep"
        ):
            with self.assertRaises(youtube.YouTubeError):
                youtube.upload_video(self.video_path, title="A title")

        request = service.videos.return_value.insert.return_value
        self.assertEqual(request.next_chunk.call_count, youtube._MAX_UPLOAD_ATTEMPTS)

    def test_server_error_recovers_when_a_retry_succeeds(self):
        modules, service = _modules()
        request = service.videos.return_value.insert.return_value
        request.next_chunk.side_effect = [
            FakeHttpError(500, b"oops"),
            (None, {"id": "abc123", "status": {"privacyStatus": "private"}}),
        ]
        with patch.object(youtube, "_google_modules", return_value=modules), patch(
            "app.services.youtube.time.sleep"
        ):
            result = youtube.upload_video(self.video_path, title="A title")

        self.assertEqual(result["video_id"], "abc123")
        self.assertEqual(request.next_chunk.call_count, 2)


class TestMissingDependencies(unittest.TestCase):
    def test_missing_libraries_produce_an_actionable_message(self):
        real_import = __import__

        def fake_import(name, *args, **kwargs):
            if name.startswith(("google", "googleapiclient", "google_auth_oauthlib")):
                raise ImportError(f"No module named {name!r}")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            with self.assertRaises(youtube.YouTubeError) as caught:
                youtube._google_modules()

        self.assertIn("uv sync --extra youtube", str(caught.exception))


class TestAuthCommandHint(unittest.TestCase):
    """错误提示里的命令必须在当前环境可以直接执行。"""

    def test_hint_uses_the_running_interpreter(self):
        command = youtube.auth_command()

        self.assertIn(sys.executable, command)
        self.assertTrue(command.endswith("--youtube-auth"))
        # 通用的 "python cli.py" 在虚拟环境或 worktree 里往往跑不通。
        self.assertNotIn("python cli.py", command)

    def test_unauthorized_message_carries_the_runnable_command(self):
        with patch.object(youtube, "_client_secrets_file", return_value=__file__), patch.object(
            youtube, "_load_credentials", return_value=None
        ):
            ok, message = youtube.ensure_authorized()

        self.assertFalse(ok)
        self.assertIn(sys.executable, message)


class TestScopeHandling(unittest.TestCase):
    """新增只读权限不能让仅有上传权限的旧 token 失去发布能力。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.token = os.path.join(self._tmp.name, "token.json")
        patcher = patch.object(youtube, "_token_file", return_value=self.token)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write_token(self, scopes):
        with open(self.token, "w", encoding="utf-8") as handle:
            json.dump({"scopes": scopes}, handle)

    def test_upload_only_token_reports_the_read_scopes_as_missing(self):
        self._write_token([youtube.UPLOAD_SCOPE])

        self.assertEqual(
            youtube.missing_scopes(),
            [youtube.READONLY_SCOPE, youtube.ANALYTICS_SCOPE],
        )

    def test_upload_only_token_can_still_publish(self):
        self._write_token([youtube.UPLOAD_SCOPE])
        with patch.object(youtube, "_client_secrets_file", return_value=__file__), patch.object(
            youtube, "_load_credentials", return_value=MagicMock()
        ):
            ok, message = youtube.ensure_authorized()

        # 关键回归点：为读取统计新增权限，不能中断正在运行的发布流程。
        self.assertTrue(ok, message)

    def test_full_token_reports_nothing_missing(self):
        self._write_token(
            [youtube.UPLOAD_SCOPE, youtube.READONLY_SCOPE, youtube.ANALYTICS_SCOPE]
        )

        self.assertEqual(youtube.missing_scopes(), [])

    def test_stats_token_without_analytics_still_publishes(self):
        """新增 analytics 权限不能影响仍在运行的发布流程。"""
        self._write_token([youtube.UPLOAD_SCOPE, youtube.READONLY_SCOPE])

        with patch.object(youtube, "_client_secrets_file", return_value=__file__), patch.object(
            youtube, "_load_credentials", return_value=MagicMock()
        ):
            ok, message = youtube.ensure_authorized()

        self.assertTrue(ok, message)
        self.assertEqual(youtube.missing_scopes(), [youtube.ANALYTICS_SCOPE])

    def test_absent_token_reports_no_granted_scopes(self):
        self.assertEqual(youtube.granted_scopes(), [])

    def test_corrupt_token_does_not_raise(self):
        with open(self.token, "w", encoding="utf-8") as handle:
            handle.write("{broken")

        self.assertEqual(youtube.granted_scopes(), [])


class TestMetadata(unittest.TestCase):
    def _metadata(self, social, **kwargs):
        with patch("app.services.llm.generate_social_metadata", return_value=social):
            return youtube.build_metadata("A subject", "A script", **kwargs)

    def test_hashtags_become_plain_tags_and_stay_in_the_description(self):
        metadata = self._metadata(
            {"title": "Title", "caption": "Caption", "hashtags": ["#ai", "#tech"]}
        )

        self.assertEqual(metadata["tags"], ["ai", "tech"])
        self.assertIn("#ai #tech", metadata["description"])

    def test_shorts_marker_is_added_once(self):
        metadata = self._metadata(
            {"title": "Title", "caption": "Caption", "hashtags": ["#shorts"]}
        )

        self.assertEqual(metadata["description"].lower().count("#shorts"), 1)

    def test_extra_tags_are_appended_without_duplicates(self):
        metadata = self._metadata(
            {"title": "Title", "caption": "Caption", "hashtags": ["#ai"]},
            extra_tags=["ai", "channel"],
        )

        self.assertEqual(metadata["tags"], ["ai", "channel"])

    def test_empty_title_falls_back_to_the_subject(self):
        metadata = self._metadata({"title": "", "caption": "", "hashtags": []})

        self.assertEqual(metadata["title"], "A subject")

    def test_tag_list_is_capped_to_the_platform_limit(self):
        metadata = self._metadata(
            {
                "title": "Title",
                "caption": "Caption",
                "hashtags": [f"#{'tag' + str(index) * 20}" for index in range(40)],
            }
        )

        total = sum(len(tag) + 1 for tag in metadata["tags"])
        self.assertLessEqual(total, youtube.MAX_TAGS_TOTAL_LENGTH)


class TestPublishPreflight(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.manifest_path = os.path.join(self._tmp.name, "manifest.json")

    def test_missing_authorization_stops_before_any_generation(self):
        with patch("app.services.youtube.ensure_authorized", return_value=(False, "no token")), patch(
            "app.services.llm.generate_script"
        ) as script_mock, patch("app.services.task.start") as start_mock, patch(
            "builtins.print"
        ):
            code = cli.run_cli(
                [
                    "--subjects",
                    "Topic one",
                    "--batch-manifest",
                    self.manifest_path,
                    "--publish",
                ]
            )

        self.assertEqual(code, 2)
        # 授权失败必须在消耗任何 LLM 与渲染额度之前中止。
        script_mock.assert_not_called()
        start_mock.assert_not_called()


class TestBatchPublishing(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.manifest_path = os.path.join(self._tmp.name, "manifest.json")
        self.video_path = os.path.join(self._tmp.name, "final-1.mp4")
        with open(self.video_path, "wb") as handle:
            handle.write(b"video-bytes")

        authorized = patch(
            "app.services.youtube.ensure_authorized", return_value=(True, "")
        )
        authorized.start()
        self.addCleanup(authorized.stop)

        metadata = patch(
            "app.services.youtube.build_metadata",
            return_value={"title": "T", "description": "D", "tags": ["t"]},
        )
        metadata.start()
        self.addCleanup(metadata.stop)

    def _run(self, upload, subjects=("Topic one", "Topic two"), argv_extra=()):
        with patch("app.services.llm.generate_script", return_value="a script"), patch(
            "app.services.task.start",
            side_effect=lambda **kw: {
                "state": const.TASK_STATE_COMPLETE,
                "videos": [self.video_path],
            },
        ), patch("app.services.youtube.upload_video", side_effect=upload) as upload_mock, patch(
            "builtins.print"
        ) as print_mock:
            code = cli.run_cli(
                [
                    "--subjects",
                    *subjects,
                    "--batch-manifest",
                    self.manifest_path,
                    "--publish",
                    *argv_extra,
                ]
            )
        self.upload_mock = upload_mock
        self.summary = (
            json.loads(print_mock.call_args.args[0]) if print_mock.call_args else None
        )
        return code

    def _manifest(self):
        with open(self.manifest_path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def _ok_upload(self, video_id="vid"):
        return lambda *args, **kwargs: {
            "video_id": video_id,
            "url": f"https://youtu.be/{video_id}",
            "privacy_status": "private",
            "requested_privacy_status": "public",
        }

    def test_every_rendered_video_is_uploaded(self):
        code = self._run(self._ok_upload())

        self.assertEqual(code, 0)
        self.assertEqual(self.upload_mock.call_count, 2)
        self.assertEqual(self.summary["published"], 2)

    def test_failed_upload_keeps_the_rendered_video(self):
        def upload(*args, **kwargs):
            raise youtube.YouTubeError("upload blew up")

        code = self._run(upload)

        items = self._manifest()["items"]
        # 渲染已经成功，发布失败不能推翻成片结果。
        self.assertEqual(items[0]["status"], batch.STATUS_DONE)
        self.assertEqual(items[0]["videos"], [self.video_path])
        self.assertIn("upload blew up", items[0]["youtube"]["error"])
        self.assertEqual(code, 1)
        self.assertEqual(self.summary["publish_failed"], 2)

    def test_quota_exhaustion_stops_further_uploads(self):
        def upload(*args, **kwargs):
            raise youtube.YouTubeError("quota gone", quota_exceeded=True)

        self._run(upload, subjects=("One", "Two", "Three"))

        # 配额耗尽后继续调用接口没有意义，只尝试一次。
        self.assertEqual(self.upload_mock.call_count, 1)
        items = self._manifest()["items"]
        self.assertTrue(all(item["status"] == batch.STATUS_DONE for item in items))
        self.assertIn("quota", items[1]["youtube"]["error"])

    def test_resume_never_republishes_an_uploaded_video(self):
        self._run(self._ok_upload())
        self.assertEqual(self.upload_mock.call_count, 2)

        with patch("app.services.llm.generate_script") as script_mock, patch(
            "app.services.task.start"
        ) as start_mock, patch(
            "app.services.youtube.upload_video", side_effect=self._ok_upload("second")
        ) as upload_mock, patch("builtins.print"):
            code = cli.run_cli(
                ["--batch-manifest", self.manifest_path, "--publish"]
            )

        self.assertEqual(code, 0)
        # 重复上传会在频道上留下重复视频，是最不可逆的副作用。
        upload_mock.assert_not_called()
        start_mock.assert_not_called()
        script_mock.assert_not_called()

    def test_resume_retries_a_failed_upload_without_rerendering(self):
        def failing(*args, **kwargs):
            raise youtube.YouTubeError("temporary failure")

        self._run(failing, subjects=("Only topic",))

        with patch("app.services.task.start") as start_mock, patch(
            "app.services.youtube.upload_video", side_effect=self._ok_upload("retry")
        ) as upload_mock, patch("builtins.print"):
            code = cli.run_cli(["--batch-manifest", self.manifest_path, "--publish"])

        self.assertEqual(code, 0)
        start_mock.assert_not_called()
        self.assertEqual(upload_mock.call_count, 1)
        self.assertEqual(self._manifest()["items"][0]["youtube"]["video_id"], "retry")

    def test_no_publish_skips_uploading_entirely(self):
        code = self._run(self._ok_upload(), argv_extra=("--no-publish",))

        self.assertEqual(code, 0)
        self.upload_mock.assert_not_called()

    def test_stop_at_script_does_not_publish(self):
        code = self._run(self._ok_upload(), argv_extra=("--stop-at", "script"))

        self.assertEqual(code, 0)
        self.upload_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()

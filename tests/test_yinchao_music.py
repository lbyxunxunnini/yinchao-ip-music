#!/usr/bin/env python3
"""yinchao_music.py 单元测试：校验、配置、命名下载、任务处理、轮询、提交 payload。"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import yinchao_music as ym  # noqa: E402


def _set_env(env: dict | None) -> None:
    """设置/清理 YINCHAO_API_KEY 与 YINCHAO_API_BASE 环境变量。"""
    import os
    for key in ("YINCHAO_API_KEY", "YINCHAO_API_BASE"):
        os.environ.pop(key, None)
    if env:
        os.environ.update(env)


class TestInputValidation(unittest.TestCase):
    """长度校验与空输入分支。"""

    def test_lyric_over_limit(self):
        with self.assertRaises(ym.YinchaoAPIError):
            ym._validate_lyric("啊" * 3001)

    def test_lyric_at_limit(self):
        self.assertEqual(ym._validate_lyric("啊" * 3000), "啊" * 3000)

    def test_prompt_over_limit(self):
        with self.assertRaises(ym.YinchaoAPIError):
            ym._validate_prompt("风" * 1001)

    def test_prompt_at_limit(self):
        self.assertEqual(ym._validate_prompt("风" * 1000), "风" * 1000)

    def test_empty_prompt(self):
        with self.assertRaises(ym.YinchaoAPIError):
            ym._validate_prompt("  ")

    def test_empty_lyric(self):
        with self.assertRaises(ym.YinchaoAPIError):
            ym._validate_lyric("")


class TestConfigReading(unittest.TestCase):
    """环境变量读取：缺失、正常、缺省 base、自定义 base。"""

    def tearDown(self):
        _set_env(None)

    def test_missing_env(self):
        _set_env(None)
        with self.assertRaises(ym.YinchaoAPIError):
            ym._read_env()

    def test_empty_env(self):
        _set_env({"YINCHAO_API_KEY": "   "})
        with self.assertRaises(ym.YinchaoAPIError):
            ym._read_env()

    def test_valid_env_with_base(self):
        _set_env({"YINCHAO_API_KEY": "sk-test-123",
                  "YINCHAO_API_BASE": "https://alpha.example.com"})
        token, base = ym._read_env()
        self.assertEqual(token, "sk-test-123")
        self.assertEqual(base, "https://alpha.example.com")

    def test_base_url_fallback(self):
        _set_env({"YINCHAO_API_KEY": "sk-test-123"})
        token, base = ym._read_env()
        self.assertEqual(token, "sk-test-123")
        self.assertEqual(base, ym.DEFAULT_BASE_URL)


class TestFilenameAndDownload(unittest.TestCase):
    """v1/v2 命名、title 兜底、前缀安全化、大小阈值。"""

    def _sample(self, size: int = 20000) -> Path:
        p = Path("/tmp/ym_test_sample.mp3")
        p.write_bytes(b"x" * size)
        return p

    def test_v1_v2_no_overwrite(self):
        sample = self._sample()
        try:
            with tempfile.TemporaryDirectory() as d:
                p1 = ym.download_audio(f"file://{sample}", Path(d), "目标-", "不归", 1, 100)
                p2 = ym.download_audio(f"file://{sample}", Path(d), "目标-", "不归", 2, 100)
                self.assertEqual(p1.name, "目标-不归-v1.mp3")
                self.assertEqual(p2.name, "目标-不归-v2.mp3")
                self.assertTrue(p1.exists() and p2.exists() and p1 != p2)
        finally:
            sample.unlink(missing_ok=True)

    def test_title_fallback_via_finish(self):
        """服务端 title 为空时用 expected_title 兜底命名。"""
        sample = self._sample()
        try:
            with tempfile.TemporaryDirectory() as d:
                task = {"id": "t-1", "choices": [
                    {"status": "done", "title": "", "audio_url": f"file://{sample}", "lyric": "词"},
                    {"status": "done", "title": "", "audio_url": f"file://{sample}", "lyric": "词"},
                ]}
                result = ym._finish_task(task, "k", output_dir=Path(d),
                                         name_prefix="目标-", min_bytes=100,
                                         expected_title="欠你一场白头")
                for song in result["songs"]:
                    self.assertEqual(song["title"], "欠你一场白头")
                    self.assertIn("欠你一场白头", Path(song["mp3_path"]).name)
        finally:
            sample.unlink(missing_ok=True)

    def test_prefix_sanitize(self):
        sample = self._sample()
        try:
            with tempfile.TemporaryDirectory() as d:
                p = ym.download_audio(f"file://{sample}", Path(d), "我的/角色-", "歌", 1, 100)
                self.assertNotIn("/", p.name)
                self.assertTrue(p.name.startswith("我的-角色"))
        finally:
            sample.unlink(missing_ok=True)

    def test_download_too_small(self):
        sample = self._sample(size=50)
        try:
            with tempfile.TemporaryDirectory() as d:
                with self.assertRaises(ym.YinchaoAPIError):
                    ym.download_audio(f"file://{sample}", Path(d), "目标-", "歌", 1, 100)
        finally:
            sample.unlink(missing_ok=True)

    def test_no_title_no_fallback_uses_song(self):
        sample = self._sample()
        try:
            with tempfile.TemporaryDirectory() as d:
                p = ym.download_audio(f"file://{sample}", Path(d), "目标-", "", 1, 100)
                self.assertEqual(p.name, "目标-song-v1.mp3")
        finally:
            sample.unlink(missing_ok=True)


class TestFinishTask(unittest.TestCase):
    """choices 分流：done / fail / 无音频地址 / 下载失败。"""

    def test_choice_fail(self):
        task = {"id": "t-1", "choices": [{"status": "fail", "error": "审核不过"}]}
        result = ym._finish_task(task, "k", output_dir=Path("/tmp/x"),
                                 name_prefix="", min_bytes=100)
        self.assertEqual(result["songs"][0]["status"], "fail")
        self.assertEqual(result["songs"][0]["error"], "审核不过")

    def test_choice_no_audio_url(self):
        task = {"id": "t-1", "choices": [{"status": "done", "title": "歌", "audio_url": "", "lyric": "词"}]}
        result = ym._finish_task(task, "k", output_dir=Path("/tmp/x"),
                                 name_prefix="", min_bytes=100)
        self.assertEqual(result["songs"][0]["status"], "invalid")

    def test_no_choices(self):
        task = {"id": "t-1", "choices": []}
        result = ym._finish_task(task, "k", output_dir=Path("/tmp/x"),
                                 name_prefix="", min_bytes=100)
        self.assertFalse(result["ok"])

    def test_download_failure_keeps_url(self):
        task = {"id": "t-1", "choices": [
            {"status": "done", "title": "歌", "audio_url": "http://invalid.local/x.mp3", "lyric": "词"}]}
        result = ym._finish_task(task, "k", output_dir=Path("/tmp/x"),
                                 name_prefix="", min_bytes=100)
        self.assertEqual(result["songs"][0]["status"], "download_failed")
        self.assertEqual(result["songs"][0]["audio_url"], "http://invalid.local/x.mp3")


class TestWaitForTask(unittest.TestCase):
    """轮询：终止状态返回、超时保留 id、查询失败保留 id。"""

    @patch.object(ym, "query_task")
    @patch.object(ym.time, "sleep")
    def test_terminal_done(self, mock_sleep, mock_query):
        mock_query.return_value = {"id": "t-1", "choices": [{"status": "done"}]}
        task = ym.wait_for_task("t-1", "k", timeout=10, interval=1,
                                expected_count=1, base_url="http://x")
        self.assertNotIn("polling_timeout", task)

    @patch.object(ym, "query_task")
    @patch.object(ym.time, "sleep")
    def test_timeout_keeps_id(self, mock_sleep, mock_query):
        mock_query.return_value = {"id": "t-1", "choices": [{"status": "running"}]}
        task = ym.wait_for_task("t-1", "k", timeout=0.01, interval=0.01,
                                expected_count=1, base_url="http://x")
        self.assertTrue(task.get("polling_timeout"))
        self.assertEqual(task.get("id"), "t-1")

    @patch.object(ym, "query_task")
    @patch.object(ym.time, "sleep")
    def test_polling_error_keeps_id(self, mock_sleep, mock_query):
        mock_query.side_effect = ym.YinchaoAPIError("网络失败")
        task = ym.wait_for_task("t-1", "k", timeout=10, interval=1,
                                expected_count=1, base_url="http://x")
        self.assertTrue(task.get("polling_error"))
        self.assertEqual(task.get("id"), "t-1")

    @patch.object(ym, "query_task")
    @patch.object(ym.time, "sleep")
    def test_running_then_done_transition(self, mock_sleep, mock_query):
        """loop 状态转换：running → done 时继续轮询并最终返回。"""
        mock_query.side_effect = [
            {"id": "t-1", "choices": [{"status": "running"}]},
            {"id": "t-1", "choices": [{"status": "running"}]},
            {"id": "t-1", "choices": [{"status": "done"}]},
        ]
        task = ym.wait_for_task("t-1", "k", timeout=10, interval=0.01,
                                expected_count=1, base_url="http://x")
        self.assertEqual(mock_query.call_count, 3)
        self.assertEqual(task["choices"][0]["status"], "done")

    @patch.object(ym, "query_task")
    @patch.object(ym.time, "sleep")
    def test_expected_count_2_waits_for_both(self, mock_sleep, mock_query):
        """loop 多版本等待：1 done + 1 running 不返回，两个 done 才返回。"""
        mock_query.side_effect = [
            {"id": "t-1", "choices": [{"status": "done"}, {"status": "running"}]},
            {"id": "t-1", "choices": [{"status": "done"}, {"status": "done"}]},
        ]
        task = ym.wait_for_task("t-1", "k", timeout=10, interval=0.01,
                                expected_count=2, base_url="http://x")
        self.assertEqual(mock_query.call_count, 2)
        statuses = [c["status"] for c in task["choices"]]
        self.assertEqual(statuses, ["done", "done"])

    @patch.object(ym, "query_task")
    @patch.object(ym.time, "sleep")
    def test_mixed_terminal_statuses_return(self, mock_sleep, mock_query):
        """loop 混合终止状态：done + fail 同时出现即返回（不再等待）。"""
        mock_query.return_value = {
            "id": "t-1",
            "choices": [{"status": "done"}, {"status": "fail", "error": "审核不过"}],
        }
        task = ym.wait_for_task("t-1", "k", timeout=10, interval=1,
                                expected_count=2, base_url="http://x")
        self.assertEqual(mock_query.call_count, 1)
        statuses = {c["status"] for c in task["choices"]}
        self.assertEqual(statuses, {"done", "fail"})

    @patch.object(ym, "query_task")
    @patch.object(ym.time, "sleep")
    def test_cancelled_is_terminal(self, mock_sleep, mock_query):
        """loop 终止条件：cancelled 也是终止状态，直接返回。"""
        mock_query.return_value = {"id": "t-1", "choices": [{"status": "cancelled"}]}
        task = ym.wait_for_task("t-1", "k", timeout=10, interval=1,
                                expected_count=1, base_url="http://x")
        self.assertNotIn("polling_timeout", task)
        self.assertEqual(task["choices"][0]["status"], "cancelled")


class TestSubmitSong(unittest.TestCase):
    """提交 payload：model v4.5、task_type normal、n 透传。"""

    def test_payload_model_v45(self):
        with patch.object(ym, "_request_json") as mock_req:
            ym.submit_song("曲风", "歌词", 2, "k", base_url="http://x")
            payload = mock_req.call_args.kwargs["payload"]
            self.assertEqual(payload["model"], "v4.5")
            self.assertEqual(payload["task_type"], "normal")
            self.assertEqual(payload["n"], 2)
            self.assertEqual(payload["lyric"], "歌词")

    def test_invalid_count(self):
        with self.assertRaises(ym.YinchaoAPIError):
            ym.submit_song("曲风", "歌词", 3, "k")


class _Resp:
    """可 with 的响应对象，模拟 urlopen 返回值。"""

    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestRequestJsonErrors(unittest.TestCase):
    """HTTP 错误分支：401/402/其他状态/网络错误/非 JSON 响应。"""

    def _http_error(self, code, body=b'{"detail":"oops"}'):
        from urllib.error import HTTPError
        return HTTPError("http://example.com/api", code, "err", {}, io.BytesIO(body))

    @patch.object(ym, "urlopen")
    def test_401_invalid_key(self, mock_urlopen):
        mock_urlopen.side_effect = self._http_error(401)
        with self.assertRaises(ym.YinchaoAPIError) as ctx:
            ym._request_json("GET", "/api/v1/x", "k", base_url="http://x")
        self.assertIn("API Key 无效", str(ctx.exception))

    @patch.object(ym, "urlopen")
    def test_402_quota(self, mock_urlopen):
        mock_urlopen.side_effect = self._http_error(402)
        with self.assertRaises(ym.YinchaoAPIError) as ctx:
            ym._request_json("GET", "/api/v1/x", "k", base_url="http://x")
        self.assertIn("余额不足", str(ctx.exception))

    @patch.object(ym, "urlopen")
    def test_500_with_detail(self, mock_urlopen):
        mock_urlopen.side_effect = self._http_error(500, b'{"detail":"server boom"}')
        with self.assertRaises(ym.YinchaoAPIError) as ctx:
            ym._request_json("GET", "/api/v1/x", "k", base_url="http://x")
        self.assertIn("HTTP 500", str(ctx.exception))
        self.assertIn("server boom", str(ctx.exception))

    @patch.object(ym, "urlopen")
    def test_urlerror(self, mock_urlopen):
        from urllib.error import URLError
        mock_urlopen.side_effect = URLError("connection refused")
        with self.assertRaises(ym.YinchaoAPIError) as ctx:
            ym._request_json("GET", "/api/v1/x", "k", base_url="http://x")
        self.assertIn("网络请求失败", str(ctx.exception))

    @patch.object(ym, "urlopen")
    def test_non_json_response(self, mock_urlopen):
        mock_urlopen.return_value = _Resp(b"<html>error page</html>")
        with self.assertRaises(ym.YinchaoAPIError) as ctx:
            ym._request_json("GET", "/api/v1/x", "k", base_url="http://x")
        self.assertIn("不是有效 JSON", str(ctx.exception))


class TestTaskRecord(unittest.TestCase):
    """task_id 落盘：写入、读取、清理、损坏容错。"""

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            ym._save_task_record(out, "t-9", "歌名", 2, "目标-")
            record = ym._load_task_record(out)
            self.assertEqual(record["task_id"], "t-9")
            self.assertEqual(record["n"], 2)

    def test_load_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(ym._load_task_record(Path(d)))

    def test_load_corrupt_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ym.TASK_RECORD_FILE).write_text("{broken", encoding="utf-8")
            self.assertIsNone(ym._load_task_record(Path(d)))

    def test_clear(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            ym._save_task_record(out, "t-1", "", 2, "")
            ym._clear_task_record(out)
            self.assertIsNone(ym._load_task_record(out))


class TestMainCli(unittest.TestCase):
    """CLI 层：缺 Key 输出、参数互斥、--no-wait 落盘、song 成功、status 自动读取与清理。"""

    def setUp(self):
        self._env_guard = dict(os.environ)
        os.environ.pop("YINCHAO_API_KEY", None)
        os.environ.pop("YINCHAO_API_BASE", None)
        self.stdout = io.StringIO()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env_guard)

    def _run(self, args: list) -> int:
        with redirect_stdout(self.stdout):
            return ym.main(args)

    def test_missing_key_guide_register(self):
        code = self._run(["song", "--prompt", "古风", "--lyric", "词", "--output-dir", "/tmp/x"])
        self.assertEqual(code, 1)
        out = json.loads(self.stdout.getvalue())
        self.assertEqual(out["error"]["guide"], "register")

    def test_prompt_conflict(self):
        os.environ["YINCHAO_API_KEY"] = "sk-test"
        code = self._run(["song", "--prompt", "a", "--prompt-file", "/tmp/p.txt",
                          "--output-dir", "/tmp/x"])
        self.assertEqual(code, 1)
        self.assertIn("只能使用一个", self.stdout.getvalue())

    def test_lyric_conflict(self):
        os.environ["YINCHAO_API_KEY"] = "sk-test"
        code = self._run(["song", "--lyric", "a", "--lyric-file", "/tmp/l.txt",
                          "--output-dir", "/tmp/x"])
        self.assertEqual(code, 1)
        self.assertIn("只能使用一个", self.stdout.getvalue())

    @patch.object(ym, "submit_song")
    def test_no_wait_saves_record(self, mock_submit):
        os.environ["YINCHAO_API_KEY"] = "sk-test"
        mock_submit.return_value = {"id": "t-100"}
        with tempfile.TemporaryDirectory() as d:
            code = self._run(["song", "--prompt", "古风", "--lyric", "词",
                              "--output-dir", d, "--no-wait"])
            self.assertEqual(code, 0)
            record = ym._load_task_record(Path(d))
            self.assertEqual(record["task_id"], "t-100")
            out = json.loads(self.stdout.getvalue())
            self.assertTrue(out["submitted_only"])

    @patch.object(ym, "wait_for_task")
    @patch.object(ym, "submit_song")
    @patch.object(ym, "urlopen")
    def test_song_success_clears_record(self, mock_urlopen, mock_submit, mock_wait):
        os.environ["YINCHAO_API_KEY"] = "sk-test"
        mock_urlopen.return_value = _Resp(b"x" * 20000)
        mock_submit.return_value = {"id": "t-1"}
        mock_wait.return_value = {"id": "t-1", "choices": [
            {"status": "done", "title": "歌名", "audio_url": "http://x/1.mp3", "lyric": "词"}]}
        with tempfile.TemporaryDirectory() as d:
            code = self._run(["song", "--prompt", "古风", "--lyric", "词",
                              "--output-dir", d, "--name-prefix", "目标-"])
            self.assertEqual(code, 0)
            out = json.loads(self.stdout.getvalue())
            self.assertEqual(out["songs"][0]["status"], "done")
            self.assertFalse((Path(d) / ym.TASK_RECORD_FILE).exists())

    @patch.object(ym, "wait_for_task")
    def test_status_no_record_no_task_id(self, mock_wait):
        os.environ["YINCHAO_API_KEY"] = "sk-test"
        with tempfile.TemporaryDirectory() as d:
            code = self._run(["status", "--output-dir", d])
            self.assertEqual(code, 1)
            self.assertIn("未找到任务记录", self.stdout.getvalue())
            mock_wait.assert_not_called()

    @patch.object(ym, "wait_for_task")
    @patch.object(ym, "urlopen")
    def test_status_auto_read_record(self, mock_urlopen, mock_wait):
        os.environ["YINCHAO_API_KEY"] = "sk-test"
        mock_urlopen.return_value = _Resp(b"x" * 20000)
        mock_wait.return_value = {"id": "t-77", "choices": [
            {"status": "done", "title": "歌名", "audio_url": "http://x/1.mp3", "lyric": "词"}]}
        with tempfile.TemporaryDirectory() as d:
            ym._save_task_record(Path(d), "t-77", "歌名", 1, "目标-")
            code = self._run(["status", "--output-dir", d])
            self.assertEqual(code, 0)
            self.assertEqual(mock_wait.call_args.args[0], "t-77")
            out = json.loads(self.stdout.getvalue())
            self.assertEqual(out["songs"][0]["status"], "done")
            self.assertFalse((Path(d) / ym.TASK_RECORD_FILE).exists())


if __name__ == "__main__":
    unittest.main()

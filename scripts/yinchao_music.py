#!/usr/bin/env python3
"""yinchao-ip-music 生歌脚本：提交完整歌曲生成，轮询等待，下载 MP3。

只做「完整歌曲生成」：歌词 + 曲风描述 → 音潮 v4.0 → 默认 2 个版本。
认证读取环境变量 YINCHAO_API_KEY（素材 B 设计）；可选 YINCHAO_API_BASE 指定 API 地址。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://open.yinchaoyongxian.com"
TERMINAL_STATUSES = {"done", "fail", "cancelled"}
MAX_LYRIC_CHARS = 3000
MAX_PROMPT_CHARS = 1000


class YinchaoAPIError(Exception):
    """API 调用错误，message 为用户可理解的中文提示。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)


def _read_env() -> tuple[str, str]:
    """从环境变量读取音潮配置，返回 (api_token, api_base)。

    - YINCHAO_API_KEY：API Key（必填，素材 B 设计）
    - YINCHAO_API_BASE：API 地址（可选，缺省生产地址；测试环境可指向 alpha）
    """
    token = os.environ.get("YINCHAO_API_KEY", "").strip()
    if not token:
        raise YinchaoAPIError(
            "缺少 YINCHAO_API_KEY 环境变量。请前往音潮开放平台注册创建 API Key，"
            "然后在运行当前 Agent 的环境中设置："
            "export YINCHAO_API_KEY=\"你的 API Key\""
        )
    base_url = os.environ.get("YINCHAO_API_BASE", "").strip() or DEFAULT_BASE_URL
    return token, base_url


def _error_detail(raw: bytes) -> str:
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return raw.decode("utf-8", errors="replace")[:200]
    if isinstance(data, dict):
        detail = data.get("detail") or data.get("message") or data.get("error")
        if detail:
            return str(detail)
    return str(data)[:200]


def _request_json(
    method: str,
    path: str,
    api_key: str,
    *,
    payload: dict[str, Any] | None = None,
    query: dict[str, str] | None = None,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 60,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    if query:
        url = f"{url}?{urlencode(query)}"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            raw = response.read()
    except HTTPError as exc:
        try:
            detail = _error_detail(exc.read()) if exc.fp else ""
        finally:
            exc.close()
        if exc.code == 401:
            raise YinchaoAPIError("API Key 无效或未授权，请检查 YINCHAO_API_KEY 环境变量的配置。") from exc
        if exc.code == 402:
            raise YinchaoAPIError("免费额度或余额不足，请前往音潮开放平台检查账户。") from exc
        raise YinchaoAPIError(f"请求失败（HTTP {exc.code}）：{detail or '请稍后重试'}") from exc
    except URLError as exc:
        raise YinchaoAPIError(f"网络请求失败：{exc.reason}。请检查网络后重试。") from exc
    if not raw:
        raise YinchaoAPIError("请求失败，服务端未返回内容。")
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise YinchaoAPIError("服务端响应不是有效 JSON，请稍后重试。") from exc


def _validate_lyric(lyric: str) -> str:
    lyric = lyric.strip()
    if not lyric:
        raise YinchaoAPIError("歌词不能为空。")
    if len(lyric) > MAX_LYRIC_CHARS:
        raise YinchaoAPIError(
            f"歌词超过 {MAX_LYRIC_CHARS} 字符上限，当前 {len(lyric)} 字符，请删减后再试。"
        )
    return lyric


def _validate_prompt(prompt: str) -> str:
    prompt = prompt.strip()
    if not prompt:
        raise YinchaoAPIError("曲风描述不能为空。")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise YinchaoAPIError(
            f"曲风描述超过 {MAX_PROMPT_CHARS} 字符上限，当前 {len(prompt)} 字符，请精简后再试。"
        )
    return prompt


def submit_song(prompt: str, lyric: str, count: int, api_key: str,
                base_url: str = DEFAULT_BASE_URL) -> dict[str, Any]:
    if count not in {1, 2}:
        raise YinchaoAPIError("版本数只能是 1 或 2。")
    payload = {
        "model": "v4.0",
        "task_type": "normal",
        "prompt": prompt,
        "lyric": lyric,
        "n": count,
    }
    return _request_json("POST", "/api/v1/song/generate", api_key, payload=payload,
                         base_url=base_url, timeout=60)


def query_task(task_id: str, api_key: str, base_url: str = DEFAULT_BASE_URL) -> dict[str, Any]:
    return _request_json(
        "GET", "/api/v1/task/query", api_key, query={"task_id": task_id},
        base_url=base_url, timeout=60
    )


def _task_statuses(task: dict[str, Any]) -> list[str]:
    choices = task.get("choices")
    if not isinstance(choices, list):
        return []
    return [str(c.get("status") or "") for c in choices if isinstance(c, dict)]


def wait_for_task(
    task_id: str,
    api_key: str,
    *,
    timeout: float,
    interval: float,
    expected_count: int,
    base_url: str = DEFAULT_BASE_URL,
) -> dict[str, Any]:
    """轮询任务直到完成/失败/超时/中断；超时与中断保留 task_id 供恢复。"""
    deadline = time.monotonic() + timeout
    try:
        latest = query_task(task_id, api_key, base_url=base_url)
        while time.monotonic() < deadline:
            statuses = _task_statuses(latest)
            if len(statuses) >= expected_count and set(statuses) <= TERMINAL_STATUSES:
                return latest
            time.sleep(interval)
            latest = query_task(task_id, api_key, base_url=base_url)
    except YinchaoAPIError as exc:
        return {"id": task_id, "polling_error": True,
                "message": f"暂时无法查询任务状态：{exc}。任务仍可能在生成，请保留任务 ID 稍后查询"}
    latest["id"] = task_id
    latest["polling_timeout"] = True
    latest["message"] = "轮询超时，任务可能仍在生成，请保留任务 ID 稍后查询"
    return latest


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\s]+", "-", name.strip()).strip("-")
    return cleaned or "song"


def download_audio(
    audio_url: str,
    output_dir: Path,
    name_prefix: str,
    title: str,
    version: int,
    min_bytes: int,
) -> Path:
    """下载音频到 {prefix}-{title}-v{version}.mp3；文件名带版本序号避免互相覆盖。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = _safe_filename(name_prefix).rstrip("-")
    title_safe = _safe_filename(title) if title else "song"
    if prefix:
        filename = f"{prefix}-{title_safe}-v{version}.mp3"
    else:
        filename = f"{title_safe}-v{version}.mp3"
    target = output_dir / filename
    request = Request(audio_url, method="GET")
    with urlopen(request, timeout=120) as response:  # noqa: S310
        data = response.read()
    if len(data) < min_bytes:
        raise YinchaoAPIError(f"音频下载不完整（{len(data)} 字节），请保留任务 ID 重试下载。")
    target.write_bytes(data)
    return target


def _finish_task(
    task: dict[str, Any],
    api_key: str,
    *,
    output_dir: Path,
    name_prefix: str,
    min_bytes: int,
    expected_title: str = "",
) -> dict[str, Any]:
    """任务完成后：校验结果、下载音频、输出结构化 JSON。

    expected_title 为阶段 2 定稿歌名；服务端未返回 title 时用它兜底。
    """
    task_id = str(task.get("id") or "未知")
    choices = task.get("choices")
    if not isinstance(choices, list) or not choices:
        return {"ok": False, "type": "song", "task_id": task_id,
                "error": {"message": "服务端未返回歌曲结果，请稍后用 status 命令查询。"}}
    songs = []
    for index, choice in enumerate(choices, start=1):
        if not isinstance(choice, dict):
            continue
        status = str(choice.get("status") or "")
        if status != "done":
            error = str(choice.get("error") or "").strip()
            songs.append({"status": status, "error": error or "歌曲生成失败或已取消"})
            continue
        title = str(choice.get("title") or "").strip() or expected_title
        audio_url = str(choice.get("audio_url") or "").strip()
        lyric = str(choice.get("lyric") or "").strip()
        if not audio_url:
            songs.append({"status": "invalid", "error": "服务端未返回可播放的音频地址"})
            continue
        try:
            mp3_path = download_audio(audio_url, output_dir, name_prefix, title, index, min_bytes)
        except (URLError, OSError, YinchaoAPIError) as exc:
            songs.append({"status": "download_failed", "title": title,
                          "audio_url": audio_url, "error": f"音频下载失败：{exc}"})
            continue
        songs.append({"status": "done", "title": title, "audio_url": audio_url,
                      "lyric": lyric, "mp3_path": str(mp3_path)})
    return {"ok": True, "type": "song", "task_id": task_id, "songs": songs}


TASK_RECORD_FILE = ".task-latest.json"


def _save_task_record(output_dir: Path, task_id: str, title: str, n: int,
                      name_prefix: str) -> None:
    """提交任务后把 task_id 落盘到输出目录，供 status 恢复查询（会话重启后仍可找回）。"""
    record = {"task_id": task_id, "title": title, "n": n, "name_prefix": name_prefix}
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / TASK_RECORD_FILE).write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_task_record(output_dir: Path) -> dict[str, Any] | None:
    """读取最近一次任务的记录；文件缺失或损坏时返回 None。"""
    path = output_dir / TASK_RECORD_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _clear_task_record(output_dir: Path) -> None:
    """任务完成后删除记录，避免陈旧 task_id 误导后续查询。"""
    try:
        (output_dir / TASK_RECORD_FILE).unlink(missing_ok=True)
    except OSError:
        pass


def _handle_key_error(message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "type": "song",
        "error": {"message": message, "guide": "register"},
        "guide_url": "https://platform.yinchaoyongxian.com/?register_channel=skillhub_ip",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="yinchao-ip-music 生歌脚本")
    subparsers = parser.add_subparsers(dest="command", required=True)

    song = subparsers.add_parser("song", help="生成完整歌曲（默认 2 版本）")
    song.add_argument("--prompt", help="曲风描述（中文，≤1000 字符）")
    song.add_argument("--prompt-file", type=Path, help="从文件读取曲风描述（含特殊字符时用，避免 shell 解析问题）")
    song.add_argument("--lyric", help="歌词（≤3000 字符，可含结构标签）")
    song.add_argument("--lyric-file", type=Path, help="从文件读取歌词（含特殊字符时用，避免 shell 解析问题）")
    song.add_argument("--title", default="", help="预期歌名（服务端未返回歌名时用于文件名与展示兜底）")
    song.add_argument("--n", type=int, default=2, choices=[1, 2], help="版本数，默认 2")
    song.add_argument("--output-dir", type=Path, help="MP3 下载目录（未指定时默认当前工作目录 audio/）")
    song.add_argument("--name-prefix", default="", help="文件名前缀（建议传目标名）")
    song.add_argument("--min-bytes", type=int, default=10000, help="音频最小字节数，低于此值判为下载不完整（默认 10000）")
    song.add_argument("--no-wait", action="store_true", help="只提交不等待，返回任务 ID")
    song.add_argument("--timeout", type=float, default=600, help="等待超时秒数")
    song.add_argument("--interval", type=float, default=10, help="轮询间隔秒数")

    status = subparsers.add_parser("status", help="继续查询已提交的歌曲任务")
    status.add_argument("--task-id", default="", help="任务 ID（省略时自动读取输出目录下的 .task-latest.json 记录）")
    status.add_argument("--title", default="", help="预期歌名（服务端未返回歌名时用于文件名与展示兜底）")
    status.add_argument("--n", type=int, default=2, choices=[1, 2], help="等待的版本数，默认 2（与提交时一致）")
    status.add_argument("--output-dir", type=Path, help="MP3 下载目录")
    status.add_argument("--name-prefix", default="", help="文件名前缀")
    status.add_argument("--min-bytes", type=int, default=10000, help="音频最小字节数，低于此值判为下载不完整（默认 10000）")
    status.add_argument("--timeout", type=float, default=600, help="等待超时秒数")
    status.add_argument("--interval", type=float, default=10, help="轮询间隔秒数")

    args = parser.parse_args(argv)

    # 先做输入校验（不依赖 API Key），再读取 Key，避免输入错误被缺 Key 掩盖
    prompt = ""
    lyric = ""
    if args.command == "song":
        try:
            if args.prompt and args.prompt_file:
                raise YinchaoAPIError("--prompt 与 --prompt-file 只能使用一个。")
            if args.lyric and args.lyric_file:
                raise YinchaoAPIError("--lyric 与 --lyric-file 只能使用一个。")
            prompt_text = args.prompt_file.read_text(encoding="utf-8") if args.prompt_file else (args.prompt or "")
            lyric_text = args.lyric_file.read_text(encoding="utf-8") if args.lyric_file else (args.lyric or "")
            prompt = _validate_prompt(prompt_text)
            lyric = _validate_lyric(lyric_text)
        except (OSError, YinchaoAPIError) as exc:
            print(json.dumps({"ok": False, "type": "song",
                              "error": {"message": str(exc)}}, ensure_ascii=False, indent=2))
            return 1

    try:
        api_key, base_url = _read_env()
    except YinchaoAPIError as exc:
        print(json.dumps(_handle_key_error(str(exc)), ensure_ascii=False, indent=2))
        return 1

    output_dir = args.output_dir
    if output_dir is None:
        output_dir = Path.cwd() / "audio"

    try:
        if args.command == "song":
            submitted = submit_song(prompt, lyric, args.n, api_key, base_url=base_url)
            task_id = str(submitted.get("id") or "")
            if not task_id:
                raise YinchaoAPIError("提交响应中缺少任务 ID，请稍后重试。")
            _save_task_record(output_dir, task_id, args.title, args.n, args.name_prefix)
            if args.no_wait:
                print(json.dumps({"ok": True, "type": "song", "task_id": task_id,
                                  "submitted_only": True,
                                  "message": "任务已提交，请稍后用 status 命令继续查询"},
                                 ensure_ascii=False, indent=2))
                return 0
            task = wait_for_task(task_id, api_key, timeout=args.timeout,
                                 interval=args.interval, expected_count=args.n,
                                 base_url=base_url)
        else:  # status
            task_id = args.task_id
            if not task_id:
                record = _load_task_record(output_dir)
                task_id = str((record or {}).get("task_id") or "")
            if not task_id:
                raise YinchaoAPIError(
                    "未找到任务记录：请传入 --task-id，或确保 --output-dir 下存在 .task-latest.json。")
            task = wait_for_task(task_id, api_key, timeout=args.timeout,
                                 interval=args.interval, expected_count=args.n,
                                 base_url=base_url)

        if task.get("polling_timeout") or task.get("polling_error"):
            print(json.dumps({"ok": False, "type": "song", "task_id": str(task.get("id") or ""),
                              "error": {"message": task.get("message", "任务仍在生成中"),
                                        "resume": "status"},
                              "guide": "resume"}, ensure_ascii=False, indent=2))
            return 0
        result = _finish_task(task, api_key, output_dir=output_dir, name_prefix=args.name_prefix,
                              min_bytes=args.min_bytes, expected_title=args.title)
        if any(s.get("status") == "done" for s in result.get("songs", [])):
            _clear_task_record(output_dir)
    except YinchaoAPIError as exc:
        print(json.dumps({"ok": False, "type": "song",
                          "error": {"message": str(exc)}}, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""共通の小さな道具（外部コマンド実行、JSON の安全な保存、ハッシュなど）。"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence


class ToolNotFoundError(RuntimeError):
    pass


class CommandError(RuntimeError):
    def __init__(self, cmd: Sequence[str], returncode: int, stderr: str):
        self.cmd = list(cmd)
        self.returncode = returncode
        self.stderr = stderr
        tail = "\n".join(stderr.strip().splitlines()[-15:])
        super().__init__(f"コマンドが失敗しました（終了コード {returncode}）: {cmd[0]}\n{tail}")


def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def find_tool(name: str) -> str:
    """ffmpeg / ffprobe の場所を返す。環境変数 VFC_FFMPEG_DIR があればそこを優先する。"""
    exe = name + (".exe" if os.name == "nt" else "")
    custom = os.environ.get("VFC_FFMPEG_DIR")
    if custom:
        cand = Path(custom) / exe
        if cand.is_file():
            return str(cand)
    found = shutil.which(name)
    if not found:
        raise ToolNotFoundError(
            f"{name} が見つかりません。FFmpeg をインストールして PATH に追加するか、"
            f"環境変数 VFC_FFMPEG_DIR に {exe} のあるフォルダを指定してください。"
        )
    return found


def _creationflags() -> int:
    # Windows で黒いコンソール窓が開かないようにする
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def run(cmd: Sequence[str], *, check: bool = True, timeout: float | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        list(cmd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        creationflags=_creationflags(),
    )
    if check and proc.returncode != 0:
        raise CommandError(cmd, proc.returncode, proc.stderr.decode("utf-8", "replace"))
    return proc


def popen(cmd: Sequence[str], **kw) -> subprocess.Popen:
    return subprocess.Popen(list(cmd), creationflags=_creationflags(), **kw)


def tool_version(name: str) -> str | None:
    try:
        out = run([find_tool(name), "-version"], timeout=20).stdout.decode("utf-8", "replace")
        return out.splitlines()[0].strip() if out else None
    except Exception:
        return None


def sha256_file(path: str | Path, chunk: int = 4 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def read_json(path: str | Path, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json_atomic(path: str | Path, data: Any) -> None:
    """一時ファイルに書いてから置き換える。途中で落ちても元のファイルが壊れない。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def safe_id(text: str) -> str:
    """フォルダ名に使える ID にする（英数字・ハイフン・下線のみ）。"""
    out = []
    for ch in text.strip():
        if ch.isascii() and (ch.isalnum() or ch in "-_"):
            out.append(ch)
        elif ch in " .":
            out.append("_")
    s = "".join(out).strip("_")
    return s[:48]


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    i = 2
    while True:
        cand = path.with_name(f"{stem}_{i}{suffix}")
        if not cand.exists():
            return cand
        i += 1


def python_version() -> str:
    return sys.version.split()[0]

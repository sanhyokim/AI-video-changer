"""FFmpeg で動画のフレームを読む。

OpenCV の VideoCapture は Windows で日本語を含むパスを開けないことがあるため、
フレームの読み込みはすべて FFmpeg のパイプで行う。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterator

import numpy as np

from .util import find_tool, popen, run

# フレーム番号の時刻からこの秒数だけ手前を指定すると、浮動小数の誤差で
# 1 フレームずれることを防げる（120fps でも半フレームは 4ms 以上ある）。
SEEK_EPS = 0.001


def frame_time(frame: int, fps: float) -> float:
    return frame / fps


def seek_arg(frame: int, fps: float) -> str:
    return f"{max(0.0, frame / fps - SEEK_EPS):.6f}"


def _scale_dims(width: int, height: int, max_width: int | None) -> tuple[int, int]:
    if not max_width or width <= max_width:
        w, h = width, height
    else:
        w, h = max_width, int(round(height * max_width / width))
    return w - (w % 2), h - (h % 2)


def read_frame(path: str | Path, frame: int, fps: float, width: int, height: int,
               max_width: int | None = None) -> np.ndarray | None:
    """指定したフレーム番号の画像（BGR）を 1 枚返す。"""
    w, h = _scale_dims(width, height, max_width)
    cmd = [find_tool("ffmpeg"), "-v", "error", "-ss", seek_arg(frame, fps), "-i", str(path),
           "-frames:v", "1", "-vf", f"scale={w}:{h}:flags=bicubic", "-pix_fmt", "bgr24", "-f", "rawvideo", "-"]
    proc = run(cmd, check=False, timeout=120)
    size = w * h * 3
    if proc.returncode != 0 or len(proc.stdout) < size:
        return None
    return np.frombuffer(proc.stdout[:size], dtype=np.uint8).reshape(h, w, 3).copy()


def iter_frames(path: str | Path, fps: float, width: int, height: int, *, start_frame: int = 0,
                step: int = 1, max_width: int | None = None, max_frames: int | None = None
                ) -> Iterator[tuple[int, np.ndarray]]:
    """start_frame から step フレームおきに (フレーム番号, 画像) を返す。"""
    w, h = _scale_dims(width, height, max_width)
    vf = []
    if step > 1:
        vf.append(f"select=not(mod(n\\,{step}))")
    vf.append(f"scale={w}:{h}:flags=bilinear")
    cmd = [find_tool("ffmpeg"), "-v", "error"]
    if start_frame > 0:
        cmd += ["-ss", seek_arg(start_frame, fps)]
    cmd += ["-i", str(path), "-vf", ",".join(vf), "-fps_mode", "passthrough",
            "-pix_fmt", "bgr24", "-f", "rawvideo", "-"]
    size = w * h * 3
    proc = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=size * 2)
    n = 0
    try:
        while max_frames is None or n < max_frames:
            buf = proc.stdout.read(size)
            if not buf or len(buf) < size:
                break
            yield start_frame + n * step, np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 3)
            n += 1
    finally:
        proc.stdout.close()
        if proc.poll() is None:
            proc.kill()
        proc.wait()


def imread_unicode(path: str | Path) -> np.ndarray | None:
    """日本語を含むパスでも読める cv2.imread。"""
    import cv2

    data = np.fromfile(str(path), dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def imwrite_unicode(path: str | Path, img: np.ndarray) -> bool:
    import cv2

    ext = Path(path).suffix or ".png"
    ok, buf = cv2.imencode(ext, img)
    if ok:
        buf.tofile(str(path))
    return bool(ok)

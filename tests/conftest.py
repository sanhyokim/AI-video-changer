"""テスト用の素材を FFmpeg で作る。

顔の検出のテストには、InsightFace に同梱されているサンプル画像（t1.jpg）を検出の確認だけに使う。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

FFMPEG = "ffmpeg"


def ff(*args: str) -> None:
    subprocess.run([FFMPEG, "-hide_banner", "-v", "error", "-y", *args], check=True)


def sample_image() -> Path:
    import insightface

    return Path(insightface.__file__).parent / "data" / "images" / "t1.jpg"


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("media")
    # 固定 30fps・音声あり・720p（4K 未満、30fps 以下の警告が出るはず）
    ff("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=3",
       "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(d / "cfr.mp4"))
    # 可変フレームレート（時刻を不規則にする）
    ff("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=60:duration=3",
       "-vf", "select=not(eq(mod(n\\,7)\\,3))", "-fps_mode", "vfr",
       "-c:v", "libx264", "-pix_fmt", "yuv420p", str(d / "vfr.mp4"))
    # HDR のタグ付き（PQ / BT.2020、10bit H.265）
    ff("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=60:duration=1",
       "-c:v", "libx265", "-x265-params", "log-level=error", "-pix_fmt", "yuv420p10le",
       "-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc",
       str(d / "hdr.mp4"))
    # 4K 60fps の暗い動画（暗い警告だけが出るはず）
    ff("-f", "lavfi", "-i", "color=c=0x101010:size=3840x2160:rate=60:duration=0.5",
       "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(d / "dark4k.mp4"))
    # 顔あり 1 秒 → 真っ黒 1 秒 → 顔あり 1 秒（顔が消えて再び現れる）
    img = sample_image()
    ff("-loop", "1", "-t", "1", "-i", str(img),
       "-f", "lavfi", "-t", "1", "-i", "color=c=black:size=1280x886:rate=30",
       "-loop", "1", "-t", "1", "-i", str(img),
       "-filter_complex",
       "[0:v]scale=1280:886,fps=30,format=yuv420p[a];[1:v]format=yuv420p[b];"
       "[2:v]scale=1280:886,fps=30,format=yuv420p[c];[a][b][c]concat=n=3:v=1:a=0[v]",
       "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(d / "faces.mp4"))
    # 日本語を含むファイル名
    (d / "日本語の動画.mp4").write_bytes((d / "cfr.mp4").read_bytes())
    return d


@pytest.fixture
def project(tmp_path):
    from vfc_companion.project import Project

    return Project.create(tmp_path / "プロジェクト1", "テスト")


@pytest.fixture(scope="session")
def face_images(tmp_path_factory) -> dict[str, Path]:
    """t1.jpg から 1 人ずつ切り出した画像（同一人物ではない）と、複数人・顔なしの画像。"""
    import cv2
    import numpy as np

    d = tmp_path_factory.mktemp("faces")
    img = cv2.imread(str(sample_image()))
    out = {}
    out["group"] = d / "group.jpg"
    cv2.imwrite(str(out["group"]), img)
    crop = img[200:480, 400:640]  # 1 人だけ
    big = cv2.resize(crop, (crop.shape[1] * 4, crop.shape[0] * 4), interpolation=cv2.INTER_CUBIC)
    out["single_big"] = d / "single_big.png"
    cv2.imwrite(str(out["single_big"]), big)
    out["single_small"] = d / "single_small.png"
    cv2.imwrite(str(out["single_small"]), crop)
    flipped = cv2.flip(big, 1)
    out["single_flip"] = d / "single_flip.png"
    cv2.imwrite(str(out["single_flip"]), flipped)
    out["blank"] = d / "blank.png"
    cv2.imwrite(str(out["blank"]), np.full((600, 600, 3), 128, np.uint8))
    return out


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

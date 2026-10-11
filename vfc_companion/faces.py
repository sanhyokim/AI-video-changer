"""InsightFace（buffalo_l）による顔の検出、向きの推定、似ている度合いの計算。

buffalo_l の学習済みモデルは非商用の研究用ライセンス。個人利用の範囲で使う（仕様 11.1、C7）。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

import numpy as np

_lock = threading.Lock()
_app = None


def get_app(det_size: int = 640):
    """FaceAnalysis を 1 回だけ作る。初回はモデル（約 280MB）を自動でダウンロードする。"""
    global _app
    with _lock:
        if _app is None:
            import onnxruntime as ort
            from insightface.app import FaceAnalysis

            avail = ort.get_available_providers()
            providers = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider") if p in avail]
            app = FaceAnalysis(name="buffalo_l", providers=providers,
                               allowed_modules=["detection", "landmark_3d_68", "landmark_2d_106", "recognition"])
            app.prepare(ctx_id=0 if "CUDAExecutionProvider" in providers else -1, det_size=(det_size, det_size))
            _app = app
        return _app


@dataclass
class FaceResult:
    bbox: np.ndarray            # x1, y1, x2, y2
    det_score: float
    pitch: float
    yaw: float
    roll: float
    embedding: np.ndarray | None  # 正規化済み（長さ 1）
    kps: np.ndarray
    lmk106: np.ndarray | None

    @property
    def width(self) -> float:
        return float(self.bbox[2] - self.bbox[0])

    @property
    def height(self) -> float:
        return float(self.bbox[3] - self.bbox[1])

    @property
    def size(self) -> float:
        return max(self.width, self.height)


def detect(img_bgr: np.ndarray, min_score: float = 0.5) -> list[FaceResult]:
    app = get_app()
    out = []
    for f in app.get(img_bgr):
        if float(f.det_score) < min_score:
            continue
        pose = getattr(f, "pose", None)
        pitch, yaw, roll = (float(pose[0]), float(pose[1]), float(pose[2])) if pose is not None else (0.0, 0.0, 0.0)
        emb = getattr(f, "normed_embedding", None)
        out.append(FaceResult(
            bbox=np.asarray(f.bbox, dtype=np.float32),
            det_score=float(f.det_score),
            pitch=pitch, yaw=yaw, roll=roll,
            embedding=None if emb is None else np.asarray(emb, dtype=np.float32),
            kps=np.asarray(f.kps, dtype=np.float32),
            lmk106=None if getattr(f, "landmark_2d_106", None) is None else np.asarray(f.landmark_2d_106),
        ))
    out.sort(key=lambda r: -r.size)
    return out


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


# ---------------- 向きの分類 ----------------
# 確かめたこと: InsightFace の pose は [pitch, yaw, roll]。サンプル画像で、yaw が負のとき
# 鼻先が両目の中点より画面の左にあった（＝顔が画面の左を向いている）。
# pitch の符号（上向きが正か負か）は確かめていない【未確認】。設定 pitch_up_sign で切り替える。

YAW_BINS = [
    ("left_profile", "左の横顔", -180.0, -60.0),
    ("left_45", "左斜め", -60.0, -15.0),
    ("front", "正面", -15.0, 15.0),
    ("right_45", "右斜め", 15.0, 60.0),
    ("right_profile", "右の横顔", 60.0, 180.0),
]
PITCH_BINS = [
    ("down_strong", "見下ろし", -180.0, -25.0),
    ("down", "少し下向き", -25.0, -8.0),
    ("level", "水平", -8.0, 8.0),
    ("up", "少し上向き", 8.0, 25.0),
    ("up_strong", "見上げ", 25.0, 180.0),
]
# 「左」「右」は画面から見た向き（顔が画面の左を向いている＝左）。


def yaw_bin(yaw: float) -> tuple[str, str]:
    for key, label, lo, hi in YAW_BINS:
        if lo <= yaw < hi:
            return key, label
    return YAW_BINS[-1][0], YAW_BINS[-1][1]


def pitch_bin(pitch: float, up_sign: int = 1) -> tuple[str, str]:
    p = pitch * (1 if up_sign >= 0 else -1)
    for key, label, lo, hi in PITCH_BINS:
        if lo <= p < hi:
            return key, label
    return PITCH_BINS[-1][0], PITCH_BINS[-1][1]


# ---------------- メガネの簡易判定 ----------------

def glasses_score(img_bgr: np.ndarray, face: FaceResult) -> float | None:
    """両目の間（鼻の付け根）と目の下の縁にある、横方向の線の強さを測る簡易判定。

    メガネのブリッジやフレームの下縁は水平な線として現れる。両目が (32,64) と (96,64) に
    来るように 128px 四方へ正規化し、その場所の線の強さを顔全体の線の強さ（中央値）で割る。
    専用のモデルではないので【推測】に基づく目安で、細いフレームや前髪で誤判定しうる。
    値が大きいほどメガネの可能性が高い。メガネのない顔ではおおむね 1〜4。
    """
    import cv2

    le, re_ = face.kps[0].astype(np.float64), face.kps[1].astype(np.float64)
    d = float(np.linalg.norm(re_ - le))
    if d < 8:
        return None
    # 両目を (32,64) と (96,64) に移す相似変換
    ang = np.arctan2(re_[1] - le[1], re_[0] - le[0])
    sc = 64.0 / d
    cos, sin = np.cos(-ang) * sc, np.sin(-ang) * sc
    c = (le + re_) / 2
    M = np.array([[cos, -sin, 64 - (cos * c[0] - sin * c[1])],
                  [sin, cos, 64 - (sin * c[0] + cos * c[1])]], np.float32)
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    can = cv2.warpAffine(gray, M, (128, 128), flags=cv2.INTER_AREA if sc < 1 else cv2.INTER_LINEAR)
    can = cv2.GaussianBlur(can, (3, 3), 0).astype(np.float32)
    gy = np.abs(cv2.Sobel(can, cv2.CV_32F, 0, 1, ksize=3))
    base = float(np.median(gy[40:100, 20:108])) + 2.0
    bridge = float(gy[50:70, 56:72].mean(axis=1).max())
    under = gy[74:88, :][:, list(range(16, 48)) + list(range(80, 112))]
    under_v = float(under.mean(axis=1).max())
    return max(bridge, under_v) / base


def describe_face(face: FaceResult, up_sign: int = 1) -> dict[str, Any]:
    yk, yl = yaw_bin(face.yaw)
    pk, pl = pitch_bin(face.pitch, up_sign)
    return {
        "bbox": [round(float(x), 1) for x in face.bbox],
        "det_score": round(face.det_score, 3),
        "pitch": round(face.pitch, 1),
        "yaw": round(face.yaw, 1),
        "roll": round(face.roll, 1),
        "yaw_bin": yk, "yaw_label": yl,
        "pitch_bin": pk, "pitch_label": pl,
        "face_px": round(face.size, 1),
    }

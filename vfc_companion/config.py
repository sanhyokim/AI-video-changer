"""判定の基準値。すべて初期値で、project.json の "settings" で上書きできる。

【推測】と書いた値は作成者の推測による初期値。実際の素材で確かめて調整すること。
"""

from __future__ import annotations

import copy
from typing import Any

DEFAULT_SETTINGS: dict[str, Any] = {
    # --- M2: 撮影ガイド（仕様 9.1） ---
    "guide_min_short_side": 2160,      # 4K（短辺 2160px）未満なら警告
    "guide_min_fps": 30.0,             # 30fps 以下なら警告（仕様は「30fps以下」）
    "guide_dark_luma": 60.0,           # 平均輝度（0-255）がこれ未満なら「暗い」【推測】
    "vfr_tolerance": 0.10,             # フレーム間隔のずれが 10% を超えたら不規則とみなす【推測】
    "vfr_bad_ratio": 0.01,             # 不規則な間隔が 1% を超えたら可変フレームレート【推測】
    "vfr_scan_frames": 1500,           # 可変フレームレート判定で調べる先頭のフレーム数

    # --- M3: テスト用クリップ ---
    # 中間ファイルの形式。C10 の実機確認が済むまでは、OpenCV で確実に読める
    # ロスレス H.264（.mp4）を初期値にする。ffv1 / prores_hq / h264_high も選べる。
    "clip_format": "h264_lossless",
    "scan_step_seconds": 0.2,          # 顔の区間解析で何秒ごとに調べるか
    "scan_width": 1280,                # 顔の区間解析のときの縮小幅（px）
    "event_yaw_deg": 45.0,             # 左右の向きがこれを超えたら「大きく傾く」【推測】
    "event_pitch_deg": 25.0,           # 上下の向き【推測】
    "event_roll_deg": 30.0,            # 首のかしげ【推測】
    "event_small_face_ratio": 0.08,    # 顔の高さが画面の短辺のこの割合未満なら「小さい」【推測】
    "event_min_duration": 0.2,         # これより短い区間は候補にしない（秒）

    # --- M4: 顔素材（仕様 9.2、11.3） ---
    "asset_min_face_px": 512,          # 顔の部分の解像度の目安（仕様: 512px 以上）
    "asset_det_score": 0.5,            # これ以上の検出スコアの顔だけ数える
    "asset_similarity_warn": 0.45,     # 他の写真との平均の似ている度合いがこれ未満なら警告【推測】
    "asset_glasses_edge": 6.0,         # メガネの簡易判定のしきい値（メガネなしは 1〜4 程度）【推測】
    # InsightFace の pitch の符号。+1 なら「正の値＝上向き」とみなす。【未確認】
    # 上を向いた写真で「下向き」と表示されたら -1 にする。
    "pitch_up_sign": 1,
    "target_images_per_person": 20,    # 動きの多い動画なら 20 枚前後（仕様 9.2）
}


def merged_settings(overrides: dict[str, Any] | None) -> dict[str, Any]:
    s = copy.deepcopy(DEFAULT_SETTINGS)
    if overrides:
        for k, v in overrides.items():
            if k in s:
                s[k] = v
    return s

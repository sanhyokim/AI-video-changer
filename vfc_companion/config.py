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

    # --- M5: 比較動画（FaceFusion の CLI。引数は実行前に --help と照合する） ---
    # FaceFusion の場所。facefusion.py のあるフォルダ。空なら環境変数 VFC_FACEFUSION_DIR を使う。
    "facefusion_dir": "",
    # FaceFusion を動かす Python。conda 環境の python.exe のパスか、
    # 「conda run --no-capture-output -n facefusion python」のようなコマンド。空なら自動で探す。
    "facefusion_python": "",
    "ff_models": "alphaface_256,hyperswap_1a_256",   # 初期の比較（仕様 M5）
    "ff_processors": "face_swapper,expression_restorer,face_enhancer",
    "ff_face_landmarker_model": "hrffa",
    "ff_face_enhancer_model": "gfpgan_1.4",          # FaceFusion の初期値
    "ff_face_enhancer_blend": 30,      # 顔の補正は弱め（仕様 M5、10.1）。FaceFusion の初期値は 80【推測】
    "ff_expression_restorer_factor": 80,             # FaceFusion の初期値
    "ff_pixel_boost": "512x512",       # 画質優先（U2）。本番の 512 モードに合わせた【推測】
    "ff_execution_providers": "auto",  # auto なら cuda があれば cuda、なければ cpu
    "ff_reference_face_distance": 0.3,  # 仕様 M5 の初期値
    "ff_face_selector_order": "large-small",          # FaceFusion の初期値
    "ff_output_video_quality": 95,
    "compare_panel_height": 1080,      # 比較動画の 1 枚分の高さ（4K を 4 枚並べると重いため）
    "compare_image_count": 5,          # 比較画像を何か所で書き出すか

    # --- M6: 品質チェック（基準値はすべて初期値。「良い」出力で決め直す：仕様 M6） ---
    "qc_width": 1920,                  # 解析のときの縮小幅（px）
    "qc_det_size": 640,                # 顔検出の入力サイズ。引きの画で顔が小さいなら 1024 などに【推測】
    "qc_step": 1,                      # 何フレームごとに調べるか（1 = 全フレーム）
    "qc_min_sim_faceset": 0.30,        # 顔素材との似ている度合いの下限【推測】
    "qc_max_sim_original": 0.40,       # 元の人物との似ている度合いの上限（超えたら置き換え不足）【推測】
    "qc_max_flicker": 0.12,            # 隣のフレームとの似ている度合いの変化の上限【推測】
    "qc_max_reappear_delta": 0.10,     # 顔が消える前後での似ている度合いの差の上限【推測】
    "qc_reappear_gap_frames": 3,       # これ以上顔が消えたら「再び現れた」とみなす
    "qc_reappear_window_seconds": 0.5,  # 消える前後の比べる長さ
    "qc_mismatch_margin": 0.05,        # 割り当てと違う人物のほうがこれ以上似ていたら取り違え【推測】
    "qc_track_match": 0.35,            # 元の顔を基準の顔と同じ人とみなす似ている度合い【推測】
    "qc_merge_gap_frames": 2,          # 問題のフレームがこの間隔以内なら 1 つの区間にまとめる
    "qc_pass_ratio": 0.02,             # 問題区間が全体のこの割合以内なら合格（仕様 12.2）
    "qc_calibration_percentile": 1.0,  # 基準値を決めるとき、良い出力の分布の何 % 点を使うか
    "qc_calibration_margin": 0.02,     # 決めた値に持たせる余裕

    # --- M8: DFM の学習用素材（品質チェックで問題がなかったフレームから集める） ---
    "dfm_min_gap_frames": 5,           # 似たフレームばかりにならないよう、この間隔以上あける【推測】
    "dfm_min_face_px": 128,            # 顔の大きさ（px）がこれ未満なら使わない【推測】
    "dfm_min_sim_faceset": 0.30,       # 出力の顔と顔素材の似ている度合いの下限【推測】
    "dfm_min_sharpness": 25.0,         # 顔のぼけの判定（ラプラシアンの分散）。これ未満は使わない【推測】
    "dfm_crop_scale": 2.0,             # 顔の枠の何倍の正方形で切り出すか（DeepFaceLab の抽出に余白が要る）
    "dfm_crop_size": 1024,             # 切り出した画像の最大の一辺（px）。小さければ拡大はしない
    "dfm_max_per_bin": 300,            # 1 つの角度（左右×上下）に入れる最大枚数。超えたら似たものを減らす
    "dfm_min_per_bin": 30,             # 使う角度でこれ未満なら「不足」と警告する【推測】
    "dfm_bias_ratio": 0.6,             # 1 つの左右の向きが全体のこの割合を超えたら「偏り」と警告する【推測】
    # 表情（68 点の特徴点から）。力む・苦しい表情を学習させるには、data_src にも同じような表情が要る
    "dfm_eye_narrow_ear": 0.20,        # 目の縦横比がこれ未満なら「目を細める・閉じる」【推測】
    "dfm_mouth_open_mar": 0.30,        # 口の縦横比がこれを超えたら「口を開ける」【推測】
    "dfm_min_expressive": 30,          # 表情の強い顔がこれ未満なら警告する【推測】
}


def merged_settings(overrides: dict[str, Any] | None) -> dict[str, Any]:
    s = copy.deepcopy(DEFAULT_SETTINGS)
    if overrides:
        for k, v in overrides.items():
            if k in s:
                s[k] = v
    return s

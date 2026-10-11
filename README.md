# 顔置き換えコンパニオン（仕様書 v1.2 フェーズ 1）

VisoMaster Fusion の前後の作業を自動化するツールです。仕様書 11 章の M1〜M4 を実装しています。
顔交換そのものは VisoMaster Fusion の画面で行い、このツールは VisoMaster Fusion の内部には触れません。

## できること

| モジュール | 内容 | 仕様の完了条件 |
|---|---|---|
| M1 プロジェクト管理 | 新規作成で 11.2 節のフォルダ一式を作る。`project.json` に素材、顔素材、クリップ、ツールのバージョン、判定の基準値、処理の履歴を記録する。パスは相対パスなのでフォルダごと移動しても開ける | 満たす（テスト済み） |
| M2 素材の取り込みと検査 | ffprobe で解像度、フレームレート、可変フレームレート、HDR、ビット深度、音声、長さ、フレーム数、明るさを調べて表示。撮影ガイド（9.1）に合わない点を警告。可変フレームレートは CFR への変換、HDR は SDR への変換をボタンで実行。**S2 の確認にチェックしないと取り込めない**。確認は `consent_log.json` に記録 | 満たす（テスト済み） |
| M3 テスト用クリップ | プレビューとタイムラインでフレーム単位に範囲を選び、切り出す（再エンコードでフレーム単位に正確）。顔の解析で「顔が見えない／消えて再び現れる／大きく傾く／小さい／複数人」の区間の候補を色で示し、クリックで範囲に設定。元の動画のフレーム番号と時刻を `project.json` に記録 | 満たす（テスト済み） |
| M4 顔素材の管理 | 人物ごとに写真をまとめて登録。顔が 1 つか、顔の大きさ 512px 以上か、メガネ（簡易判定）、向き（左右・上下・傾き）、写真どうしの似ている度合い（ArcFace）を検査。角度ごとの枚数と不足を表示。**S1 の確認にチェックしないと人物を登録できない** | 満たす（テスト済み） |

## Windows 11 での準備

1. Python 3.11 以降を入れる（https://www.python.org/ 、インストール時に「Add to PATH」）
2. FFmpeg を入れて PATH に追加する。HDR→SDR 変換には zscale か libplacebo を含むビルドが必要（例：gyan.dev の full 版）。PATH に入れない場合は環境変数 `VFC_FFMPEG_DIR` に `ffmpeg.exe` のあるフォルダを指定
3. このフォルダで `setup_windows.bat` を実行（`.venv` を作って必要なライブラリを入れる）
4. `run_companion.bat` で起動

初めて顔を検査するときに、InsightFace の buffalo_l モデル（約 280MB）が自動でダウンロードされ、`%USERPROFILE%\.insightface\models\` に置かれます。
buffalo_l は非商用の研究用ライセンスです（個人利用の範囲で使う。仕様 11.1、C7）。

顔の解析は CPU で動きます。GPU（CUDA）で速くしたい場合は、`onnxruntime` を `onnxruntime-gpu` に入れ替えます。CUDA と cuDNN の用意が必要で、手順は未確認です。

## 使い方（GUI）

1. 「新しいプロジェクト」で場所と名前を決める
2. 「素材」タブで動画を取り込む。警告を読み、必要なら CFR / SDR に変換する
3. 「テスト用クリップ」タブで「顔の区間を解析」し、候補をクリックするか、`I`（開始点）と `O`（終了点）で範囲を決めて「切り出す」。`←` `→` で 1 フレーム、`Shift+←→` で 10 フレーム、`Space` で再生と停止
4. 「顔素材」タブで AI 人物を追加し、写真をまとめて追加する。不足している角度を見て写真を足す
5. 「プロジェクト」タブで VisoMaster Fusion などのバージョンを記入する。判定の基準値もここで変えられる

## 使い方（コマンドライン、AI エージェント向け）

```
python -m vfc_companion.cli new D:\vfc\dance01 --name ダンス1
python -m vfc_companion.cli import-video D:\vfc\dance01 D:\撮影\IMG_0001.MOV --i-confirm-consent
python -m vfc_companion.cli scan D:\vfc\dance01 src_xxxxxxxx
python -m vfc_companion.cli cut D:\vfc\dance01 src_xxxxxxxx 600 1200 --note "回転"
python -m vfc_companion.cli add-person D:\vfc\dance01 Aoi --i-confirm-ai-generated
python -m vfc_companion.cli add-faces D:\vfc\dance01 p_Aoi "D:\faces\aoi\*.png"
python -m vfc_companion.cli faces-report D:\vfc\dance01 p_Aoi
```

`--i-confirm-consent` と `--i-confirm-ai-generated` は、確認したことを本人が宣言するためのものです。付けないと取り込みと登録は失敗します。

## テスト用クリップの形式

初期値は **H.264 ロスレス（.mp4）**です。VisoMaster Fusion は動画を OpenCV で読み込み、拡張子 .mp4 / .mov / .mkv などを受け付けることをソースで確認しました。
ただし、ProRes や FFV1 を実際に読めるかは未確認です（C10）。実機で確かめたら、「プロジェクト」タブの基準値 `clip_format` を `ffv1`、`prores_hq`、`h264_high` から選び直せます。
VisoMaster Fusion は入力を 8bit で扱うとみられるため（推測）、10bit や HDR の動画は SDR 8bit に変換したものを使うことを勧めます。

## 確認できていないこと

- **Windows 11 での動作は未確認**です。Linux のコンテナで、自動テスト 20 件（画面を出さない GUI の操作を含む）が通ることだけを確認しました
- InsightFace の上下の向き（pitch）の符号が「上向き＝正」かどうかは未確認です。上を向いた写真が「下向き」と表示されたら、基準値 `pitch_up_sign` を `-1` にしてください
- メガネの判定は、目の間と目の下の横線の強さを見る簡易なもので、専用のモデルではありません。細いフレームや前髪で誤判定することがあります
- 【推測】と書いた基準値（暗さ、似ている度合い、傾き、顔の小ささなど）は初期値です。実際の素材で調整してください（`vfc_companion/config.py`）
- 可変フレームレートの判定は先頭 1500 フレームだけを見ます

## フォルダ構成

```
vfc_companion/
  project.py      M1 プロジェクト管理
  media.py        M2 素材の検査、取り込み、CFR/SDR 変換
  clips.py        M3 切り出しと顔の区間の候補
  face_assets.py  M4 顔素材の管理
  faces.py        InsightFace による検出、向き、似ている度合い、メガネの簡易判定
  frames.py       FFmpeg でフレームを読む（日本語のパスでも読める）
  consent.py      S1/S2 の確認記録
  config.py       判定の基準値（初期値）
  cli.py          コマンドライン
  gui/            画面（PySide6）
tests/            自動テスト（python -m pytest）
docs/phase0_checks.md  15 章の確認結果（2026-10-09）
docs/spec_v1.2.md      仕様書 v1.2（正とする版）
```

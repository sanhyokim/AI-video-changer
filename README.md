# 顔置き換えコンパニオン（仕様書 v1.2 フェーズ 1・2、M8）

VisoMaster Fusion の前後の作業を自動化するツールです。仕様書 11 章の M1〜M6、M8 と、10 章のシーンプロファイルの記録を実装しています（M7 の書き出しの整理はまだです）。
顔交換そのものは VisoMaster Fusion の画面で行い、このツールは VisoMaster Fusion の内部には触れません。

## できること

| モジュール | 内容 | 仕様の完了条件 |
|---|---|---|
| M1 プロジェクト管理 | 新規作成で 11.2 節のフォルダ一式を作る。`project.json` に素材、顔素材、クリップ、ツールのバージョン、判定の基準値、処理の履歴を記録する。パスは相対パスなのでフォルダごと移動しても開ける | 満たす（テスト済み） |
| M2 素材の取り込みと検査 | ffprobe で解像度、フレームレート、可変フレームレート、HDR、ビット深度、音声、長さ、フレーム数、明るさを調べて表示。撮影ガイド（9.1）に合わない点を警告。可変フレームレートは CFR への変換、HDR は SDR への変換をボタンで実行。**S2 の確認にチェックしないと取り込めない**。確認は `consent_log.json` に記録 | 満たす（テスト済み） |
| M3 テスト用クリップ | プレビューとタイムラインでフレーム単位に範囲を選び、切り出す（再エンコードでフレーム単位に正確）。顔の解析で「顔が見えない／消えて再び現れる／大きく傾く／小さい／複数人」の区間の候補を色で示し、クリックで範囲に設定。元の動画のフレーム番号と時刻を `project.json` に記録 | 満たす（テスト済み） |
| M4 顔素材の管理 | 人物ごとに写真をまとめて登録。顔が 1 つか、顔の大きさ 512px 以上か、メガネ（簡易判定）、向き（左右・上下・傾き）、写真どうしの似ている度合い（ArcFace）を検査。角度ごとの枚数と不足を表示。**S1 の確認にチェックしないと人物を登録できない** | 満たす（テスト済み） |
| M5 比較動画 | ボタン 1 つで、FaceFusion を CLI（headless-run）で実行し、`renders/visomaster/` に置いた VisoMaster Fusion の出力を見つけ、元の動画と一緒にラベル付きで横並び（4 つなら 2×2）の比較動画と比較画像を作る。実行前に FaceFusion の `--help` と引数を照合する。2 人以上は基準フレームの顔の番号で人物を割り当てる。コンテンツ判定で止まったクリップは比較から外す（S4） | 満たす（偽の FaceFusion でテスト済み。本物での確認は未実施） |
| M6 品質チェック | 出力を指定すると、検出漏れ、顔素材との似ている度合い、元の人物との似ている度合い、ちらつき、再び現れたときの一貫性、取り違えをフレームごとに計算し、HTML のレポートを作る。問題区間の一覧から時刻へ移動できる。目視の 6 項目はチェックリスト。「良い」と判断した出力から基準値を決め直せる | 満たす（テスト済み） |
| M8 DFM の学習用素材 | 品質チェックで問題がなかったフレームから、AI 人物の顔（出力）と置き換える前の顔（元のクリップ）を切り出し、DeepFaceLab に渡せる data_src / data_dst のフォルダを作る。顔素材の写真も入れる。角度（左右×上下）ごとの枚数と、目を細めた顔・口を開けた顔の枚数を表示し、偏りを警告する。学習の手順は `docs/dfm_training.md` | 満たす（合成した動画でテスト済み。実際の動画では未確認） |
| シーンプロファイル | P1〜P7 ごとに、VisoMaster Fusion で使う設定、メモ、根拠にした比較と品質チェックを記録し、Markdown のメモに書き出す。VisoMaster Fusion の設定は直接変えない（11.4） | — |

## Windows 11 での準備

1. Python 3.11 以降を入れる（https://www.python.org/ 、インストール時に「Add to PATH」）
2. FFmpeg を入れて PATH に追加する。HDR→SDR 変換には zscale か libplacebo を含むビルドが必要（例：gyan.dev の full 版）。PATH に入れない場合は環境変数 `VFC_FFMPEG_DIR` に `ffmpeg.exe` のあるフォルダを指定
3. このフォルダで `setup_windows.bat` を実行（`.venv` を作って必要なライブラリを入れる）
4. `run_companion.bat` で起動

### FaceFusion（比較に使う場合）

FaceFusion 3.9.1 を conda の環境で入れてある前提です（例：`C:\Users\user\facefusion`、環境名 `facefusion`）。

1. 「比較」タブの「フォルダ」に FaceFusion のフォルダ（`facefusion.py` がある場所）を入れる。空なら `%USERPROFILE%\facefusion` などを自動で探す
2. 「Python」に FaceFusion の環境の python.exe を入れる。分からなければ、Anaconda Prompt で `conda activate facefusion` のあと `where python` を実行すると表示される（例：`C:\Users\user\miniconda3\envs\facefusion\python.exe`）。空なら miniconda3 / anaconda3 の `envs\facefusion` を自動で探す。`conda run --no-capture-output -n facefusion python` のようなコマンドも書ける
3. 「保存して確認」を押す。FaceFusion のバージョンと、使える実行環境（cuda など）が表示される

FaceFusion のモデルは、初めて実行するときに FaceFusion が自分でダウンロードします。

初めて顔を検査するときに、InsightFace の buffalo_l モデル（約 280MB）が自動でダウンロードされ、`%USERPROFILE%\.insightface\models\` に置かれます。
buffalo_l は非商用の研究用ライセンスです（個人利用の範囲で使う。仕様 11.1、C7）。

顔の解析は CPU で動きます。GPU（CUDA）で速くしたい場合は、`onnxruntime` を `onnxruntime-gpu` に入れ替えます。CUDA と cuDNN の用意が必要で、手順は未確認です。

## 使い方（GUI）

1. 「新しいプロジェクト」で場所と名前を決める
2. 「素材」タブで動画を取り込む。警告を読み、必要なら CFR / SDR に変換する
3. 「テスト用クリップ」タブで「顔の区間を解析」し、候補をクリックするか、`I`（開始点）と `O`（終了点）で範囲を決めて「切り出す」。`←` `→` で 1 フレーム、`Shift+←→` で 10 フレーム、`Space` で再生と停止
4. 「顔素材」タブで AI 人物を追加し、写真をまとめて追加する。不足している角度を見て写真を足す
5. 「プロジェクト」タブで VisoMaster Fusion などのバージョンを記入する。判定の基準値もここで変えられる
6. VisoMaster Fusion でクリップを処理し、書き出した動画を `プロジェクト\renders\visomaster\` に、**クリップのファイル名を含む名前**で保存する（例：`faces_f000000-000090_512_表情オン.mp4`）。別の場所に書き出した場合は「比較」タブの「動画を指定して登録…」でも登録できる
7. 「比較」タブでクリップを選び、「比較を作る」を押す。2 人以上映るクリップでは、先に「顔のある最初のフレーム」で顔に番号を付け、番号ごとに置き換える人物を決める。できた比較動画と比較画像はタブの右側で開ける
8. 「品質チェック」タブで出力を選んで「品質チェックを実行」。問題区間をクリックすると、元と出力を左右に並べてそのフレームを表示する（`←` `→` で 1 フレームずつ）。「レポートを開く」でブラウザ用のレポートを開ける。目視の 6 項目もここで記録する
9. 目視でも「良い」と思えた出力ができたら、「良い出力として基準値を決める…」で基準値を決め直す
10. 「DFM 学習素材」タブで AI 人物と品質チェックの結果を選び、「学習用の素材を集める」を押す。DeepFaceLab での学習と VisoMaster Fusion での使い方は `docs/dfm_training.md`
11. 「シーン別の設定」タブで、場面の種類（P1〜P7）ごとに決めた VisoMaster Fusion の設定と根拠を記録し、「メモを書き出す」で `export\scene_profiles.md` に保存する

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

フェーズ 2：

```
python -m vfc_companion.cli ff-check D:\vfc\dance01
python -m vfc_companion.cli assign D:\vfc\dance01 clip_xxxxxxxx p_Aoi:0:0 p_Ren:0:1
python -m vfc_companion.cli compare D:\vfc\dance01 clip_xxxxxxxx
python -m vfc_companion.cli qc D:\vfc\dance01 r_xxxxxxxx
python -m vfc_companion.cli qc-calibrate D:\vfc\dance01 qc_xxxxxxxx --apply
python -m vfc_companion.cli profiles D:\vfc\dance01
python -m vfc_companion.cli dfm-collect D:\vfc\dance01 p_Aoi qc_xxxxxxxx qc_yyyyyyyy
```

`assign` の書き方は `人物ID:基準フレーム:顔の番号`。顔の番号は基準フレームの顔を大きい順に並べたときの番号（0 から）です。

`--i-confirm-consent` と `--i-confirm-ai-generated` は、確認したことを本人が宣言するためのものです。付けないと取り込みと登録は失敗します。

## テスト用クリップの形式

初期値は **H.264 ロスレス（.mp4）**です。VisoMaster Fusion は動画を OpenCV で読み込み、拡張子 .mp4 / .mov / .mkv などを受け付けることをソースで確認しました。
ただし、ProRes や FFV1 を実際に読めるかは未確認です（C10）。実機で確かめたら、「プロジェクト」タブの基準値 `clip_format` を `ffv1`、`prores_hq`、`h264_high` から選び直せます。
VisoMaster Fusion は入力を 8bit で扱うとみられるため（推測）、10bit や HDR の動画は SDR 8bit に変換したものを使うことを勧めます。

## 確認できていないこと

- **Windows 11 での動作は未確認**です。Linux のコンテナで、自動テスト 37 件（画面を出さない GUI の操作を含む）が通ることだけを確認しました
- **本物の FaceFusion での実行は未確認**です。テストでは、本物の 3.9.1 の `--help` を返して入力を出力に写すだけの偽の FaceFusion を使いました。詳しくは `docs/phase2_notes.md`
- 品質チェックの基準値の初期値は【推測】です。「良い」出力で決め直してください
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
  facefusion.py   M5 FaceFusion の場所探し、--help の照合、実行、VisoMaster Fusion の出力探し
  compare.py      M5 比較動画と比較画像
  qc.py           M6 品質チェックの計算と基準値の決め直し
  report.py       M6 HTML レポート
  profiles.py     シーンプロファイル（P1〜P7）の記録
  dfm.py          M8 DFM の学習用素材（data_src / data_dst）を集める
  config.py       判定の基準値（初期値）
  cli.py          コマンドライン
  gui/            画面（PySide6）
tests/            自動テスト（python -m pytest）。tests/fake_facefusion は FaceFusion の代わり
docs/phase0_checks.md  15 章の確認結果（2026-10-09）
docs/dfm_training.md   DFM（AI 人物専用モデル）の学習と VisoMaster Fusion での使い方
docs/phase2_notes.md   フェーズ 2 で確かめたこと、未確認のこと、仕様書の版の違い（2026-10-11）
docs/spec_v1.2.md      仕様書 v1.2（正とする版）
```

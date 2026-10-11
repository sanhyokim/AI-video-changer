# フェーズ 2 のメモ（2026-10-11）

M5（比較動画）、M6（品質チェック）、シーンプロファイル（10 章）を作ったときに確かめたこと、決めたこと、確かめていないことです。

記号：**【確認済み】** 公式の情報やソースで見た／**【未確認】** 確かめられなかった／**【推測】** 確かめた事実からの推論

## 1. FaceFusion 3.9.1 の CLI

- `python facefusion.py headless-run --help` の実際の出力を `tests/data/ff_help_3.9.1.txt`（横幅 200）と `ff_help_3.9.1_narrow.txt`（横幅 80）に保存した。どちらの幅でも同じ 95 個の引数と選択肢が読めることをテストで確かめている【確認済み】
- ツールは実行の前に毎回 `--help` を読み、渡す引数の名前、選択肢、値の範囲を照合する。FaceFusion が更新されて引数が変わったら、実行せずに理由を表示する（M5 の要件）
- `--face-selector-mode` は many / one / reference の 3 つ。**auto はない**ので reference を使う【確認済み】
- 終了コード **3** は、コンテンツ判定で処理が止まったことを表す（`facefusion/workflows/to_video.py`）【確認済み】。このときツールはそのクリップを FaceFusion の比較から外し、`project.json` に記録する。判定を回避する手段は作らない（S4）
- 2 人以上を置き換えるときは、人物ごとに 1 回ずつ順に実行する（1 回目の出力を 2 回目の入力にする）
- 顔の番号（`--reference-face-position`）は、`--face-selector-order` の順に並べた基準フレームの顔の番号。ツールの比較画面では、同じ順で顔に番号を付けて表示する
- 日本語を含むパスは、ASCII だけのフォルダ（`%TEMP%\vfc_ff`、環境変数 `VFC_STAGE_DIR` で変更可）にハードリンクかコピーを作って渡す【推測：FaceFusion は日本語パスで失敗することがある】
- conda の環境の python を直接指定したときは、`CONDA_PREFIX` を設定し、`Library\bin` などを `PATH` の先頭に足してから起動する（`facefusion/conda.py` が `CONDA_PREFIX` を見て DLL の場所を足すため）【確認済み：ソース】【未確認：Windows での実行】

### ユーザーの環境（2026-10-11 に聞いた内容）

- FaceFusion 3.9.1 が `C:\Users\user\facefusion` にあり、conda の環境 `facefusion` で起動できた。画面に cuda / tensorrt / cpu が表示された
- 起動は `conda activate facefusion` → `cd /d C:\Users\user\facefusion` → `python facefusion.py run --open-browser`
- ツールに設定する python のパスはまだ聞いていない。`conda activate facefusion` のあとで `where python` を実行すると分かる（ふつうは `C:\Users\user\miniconda3\envs\facefusion\python.exe` か `anaconda3` の同じ場所）。空のままでも、これらの場所は自動で探す

## 2. VisoMaster Fusion v3.12.3 の初期値（ソースで確認）

`app/ui/widgets/*_layout_data.py` の定義から読んだ値です【確認済み：ソース】。画面の表記と同じかは未確認です（C5）。

| 項目 | 初期値 | 仕様の推奨 |
|---|---|---|
| Similarity Threshold | 60 | 似た 2 人なら厳しく。ユーザーは 50 に下げて改善したとのこと |
| Enable Face Expression Restorer | オフ | オン（Advanced で目・眉・唇） |
| Enable Landmark Detection | オフ | オン |
| Landmark Detect Model | 203 | tufa98（傾く）／orformer98（隠れる） |
| Detect Score | 50 | — |
| Enable Face Tracking (ByteTrack) | オフ | オン |
| Track Buffer (Frames) | 30 | 動きが多いなら長め |
| Occlusion Mask | オフ | オン |

効きそうな機能の多くが初期状態でオフなので、最初のテストの品質が悪かった一因と考えています【推測】。

### ユーザーの最初のテストの症状と、試す順番（2026-10-11）

- 症状：筋トレの苦しい表情が「能面」のようになる。下を向いて顔が少し隠れると外れる。顔がゆがむ。Swapper Resolution を 512 にして少しよくなった
- 能面っぽい（P1）：Face Expression Restorer をオン → Restore Mouth / Restore Eyes をオン → Face Restorer の Blend を下げるかオフ
- 下を向くと外れる・ゆがむ（P3・P4）：Landmark Detection をオンにして tufa98 か orformer98 → ByteTrack をオン → Occlusion Mask と DFL XSeg Mask をオン → 顔素材に「少し下向き」「見下ろし」を足す
- 1 回に 1 つだけ変え、比較の画面で並べて確かめる（仕様 9.3）。決めた値は「シーン別の設定」に根拠と一緒に記録する

## 3. 品質チェック（M6）の作り

- 顔の検出と ArcFace の埋め込みは InsightFace（buffalo_l）。品質チェックでは検出と認識のモデルだけを読み込む
- 初期設定では 1920px の幅に縮めて全フレームを調べる（`qc_width`、`qc_step`）。結果は `qc/cache/` に保存し、同じ動画をもう一度調べるときは使い回す
- 元のクリップと出力の顔を、位置の重なり（IoU 0.3 以上）で対応させる
- 置き換える顔の決め方
  - 人物が 1 人で割り当てがないとき：各フレームのいちばん大きい顔
  - 割り当てがあるとき：基準フレームで選んだ顔の埋め込みを手がかりに、各フレームで同じ人の顔を探す（似ている度合い `qc_track_match` 以上）
- 指標と理由
  - 検出漏れ：元にある顔が出力で見つからない
  - 顔素材と似ていない：顔素材の平均の埋め込みとの似ている度合いが `qc_min_sim_faceset` 未満
  - 元の顔が残っている：元の顔との似ている度合いが `qc_max_sim_original` を超える
  - ちらつき：隣のフレームとの差が `qc_max_flicker` を超える
  - 再び現れたとき別人：顔が `qc_reappear_gap_frames` 以上消えたあと、前後 `qc_reappear_window_seconds` 秒の平均の差が `qc_max_reappear_delta` を超える
  - 取り違え：別の人物の顔素材のほうに `qc_mismatch_margin` 以上近い
  - 置き換えてはいけない顔：割り当てていない人の顔が、どれかの顔素材に近くなった
- 問題のフレームの割合が `qc_pass_ratio`（2%、仕様 12.2）以内なら「合格の目安内」。合格には目視の 6 項目（12.1）も必要
- 基準値の初期値は【推測】。「良い」と判断した出力で品質チェックをし、「良い出力として基準値を決める」を押すと、その分布から決め直す（下限は下位 1% − 0.02、上限は上位 1% ＋ 0.02。どちらも設定で変えられる）
- 比較動画には「AI で改変した」ことを示すメタデータ（comment）を付ける（S5。本番の書き出しは M7）

## 4. 仕様書の版の違い（要確認）

このプロジェクトにアップロードされた仕様書 v1.2 と、前のエージェントのコードに入っていた `docs/spec_v1.2.md` では、3 章の安全ルールの表が違います。

| リポジトリの版 | アップロードされた版 |
|---|---|
| S1 置き換え先の顔は AI で作った実在しない人物に限る | （なし） |
| S2 元の動画は本人か、了承を得たものに限る | （なし） |
| S3 公開時に「AI で加工・改変した」表示をする | S1 |
| S4 安全機能を無効にしたり回避したりしない | S2 |
| S5 「AI で改変した」メタデータを付ける | S3 |

ツールは、取り込み時の確認（本人または了承済み）と、AI 人物の登録時の確認をそのまま残しています。コードとこのメモの S 番号はリポジトリの版に合わせています。

## 5. 仕様書にない新しい候補（未確認、2026-10-11 に調べた）

どちらも、品質、RTX 3080（10GB）で動くか、体や背景をそのまま残せるかは確かめていません。

- **DreamID-V**（ByteDance）：動画の顔交換。2026 年 1 月にコードと重みを公開、Apache-2.0。動画生成モデル Wan2.1 がベース。他の人が作った ComfyUI 版には「16GB の GPU で動く」とある。出典：https://gitee.com/ByteDance/DreamID-V
- **VFace**（WACV 2026）：拡散モデルの画像用の顔交換を、追加の学習なしで動画に使う方法。コード公開済み。出典：https://arxiv.org/abs/2602.07835
- **Wan2.2-Animate**（Alibaba、2025-09-19 公開、Apache-2.0）：Higgsfield の「WAN 2.2 Animate」の元のモデル。Replace モードは、動画の人物を参照画像の人物で描き直し、動きと表情は元の動画から取る（照明を合わせる relighting LoRA あり）。顔だけでなく人物全体を描き直すので、服や体型も参照画像に寄る【推測：論文の説明から】。720P 前提。公式の初期設定は A800 8 枚向けで、量子化版（GGUF）でも 12GB が下限という記事がある。出典：https://huggingface.co/Wan-AI/Wan2.2-Animate-14B 、https://arxiv.org/abs/2509.14055 、https://higgsfield.ai/blog/Higgsfield-Animate-WAN-2.2-Animate
- **Wan-Animate-2**（2026-08-07 公開、Apache-2.0）：ページには画像を動かす機能だけが書かれていて、動画の人物の置き換えには触れていない。出典：https://huggingface.co/Wan-AI/Wan-Animate-2-14B
- これらの出力も、比較の画面の「動画を指定して登録…」で VisoMaster Fusion の結果と並べ、品質チェックにかけられる（フレーム数やフレームレートが違えば、レポートに注意が出る）
- 仕様書で最優先にしている LivingSwap（CVPR 2026）は、2026-10-11 の時点でもコード未公開（https://aim-uofa.github.io/LivingSwap/）

## 6. 確かめていないこと

- **Windows 11 での動作**。Linux のコンテナで、偽の FaceFusion（本物の 3.9.1 の `--help` を返し、入力を出力に写すだけ）を使った自動テストが通ることだけを確かめた
- 本物の FaceFusion での実行（モデルのダウンロード、cuda での速度、進み具合の表示の読み取り）
- Windows での中止（`taskkill /T` で FaceFusion の子プロセスまで止まるか）
- 品質チェックの基準値の初期値が、実際の出力で妥当か
- VisoMaster Fusion の設定項目名が画面の表記と同じか（C5）

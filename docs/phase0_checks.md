# 15 章の確認結果（2026-10-09）

Web で確かめられる項目（C1、C2、C3、C5、C7、C8、C9、C10、C11）を、公式のリポジトリ、リリースページ、ソースコードで確かめた結果です。
C4（VRAM とメモリ）と、C6・C9・C10 の実機テストはユーザーの環境が必要なので、ここには含みません。

記号：**【確認済み】** 公式の情報やソースで見た／**【未確認】** 確かめられなかった／**【推測】** 確かめた事実からの推論

## 仕様書と違っていた点（先に読む）

| No | 仕様書の記述 | 実際 | 影響 |
|---|---|---|---|
| 1 | v3.12.3 を入手する（6.1、6.3） | リリースページで「Latest」が付いているのは **v3.9.3（2026-07-27）**。v3.12.3 には Latest も Pre-release も付いていない【確認済み】 | README の「最新リリースの Start_Portable.bat」のリンクは v3.9.3 を指す可能性が高い【推測】。**v3.12.3 のリリースページから直接ダウンロードする** |
| 2 | AlphaFace は Latest に入っていない可能性がある（6.3） | AlphaFace は **v3.12.3 のリリースに含まれている**。dev 限定ではない【確認済み】。dev ブランチはさらに先の 3.17.2（2026-10-06）だが、v3.13 以降のタグはない | v3.12.3 なら AlphaFace が使える |
| 3 | FaceFusion の顔の選択方式「auto」（C2、M5） | `--face-selector-mode` は **many / one / reference の 3 つだけ**で、auto はない。3.7.0 の「auto」は性別・人種の絞り込みの値【確認済み】 | M5 は reference を使う（仕様どおり）。auto は使わない |
| 4 | 特徴点検出 hrffa（M5） | hrffa は選べるが、初期値は 2dfan4【確認済み】 | M5 では `--face-landmarker-model hrffa` を明示する |
| 5 | 表情を部位ごとに調整できる【未確認】（6.2） | マニュアルの Advanced モードに eyes / brows / lips / Include Jaw・Contour の部位別の設定がある【確認済み】 | 実際の画面での確認は残る |
| 6 | Secondary Swapper、Face Shaping、Number of Streams（10.1） | マニュアルには載っていない。UI の定義ファイルとリリースノートにはある【確認済み】 | 機能はあるとみてよい。名前は実際の画面で確かめる |
| 7 | ReF-LDM | マニュアルの表記は「Ref-LDM Denoiser」【確認済み】 | 表記の違いだけ |
| 8 | ドライバ 576.57 以上 | README の記述は「576.57 以上（CUDA 12.9 用）」。v3.0.0 で CUDA 13 系に移行しているので古い記述の可能性【推測】 | 最新のドライバを入れておけば問題ない【推測】 |

## C1. VisoMaster Fusion

- タグ v3.12.3 は 2026-08-29（dev から main への PR #341）【確認済み】
- リリースノートに AlphaFace、Swapper Resolution（256 / 512 / Auto）、tufa98 / tufa314 / orformer98 がある。タグ内のソース（`app/processors/alphaface/`）と `docs/alphaface.md` でも確認【確認済み】
- AlphaFace の推論コードは MIT（Alphaface_Official 由来）。重みのライセンスは VisoMaster Fusion 側に明記がない【確認済み】
- README に「visomaster.com / visomaster.org は無関係」の注意、「Start_Portable.bat だけをダウンロードして実行」、VRAM「最低 6GB、推奨 8〜12GB 以上」がある【確認済み】
- 出典：https://github.com/VisoMasterFusion/VisoMaster-Fusion/releases

## C2. FaceFusion

- 3.9.0 は 2026-09-03（alphaface_256 と hrffa を追加）。3.9.1 は 2026-09-30 で「Latest」【確認済み】
- **4.0 は未リリース**。`v4-beta` タグ（9/30）と `v4` ブランチはあるが正式版ではない【確認済み】
- CLI（master の `program.py`）【確認済み】
  - サブコマンド `headless-run`、`--processors`
  - `--face-swapper-model`：`alphaface_256`、`hyperswap_1a_256` とも選べる。初期値は hyperswap_1a_256
  - `--face-landmarker-model`：many / 2dfan4 / hrffa / peppa_wutz（初期値 2dfan4）
  - プロセッサ `expression_restorer`（モデルは live_portrait のみ、`--expression-restorer-areas` は upper-face / lower-face）、`face_enhancer`
  - `--reference-frame-number`、`--reference-face-position`、`--reference-face-distance`（初期値 0.3）、`--face-selector-order`、`--face-selector-gender`（auto / female / male）はすべてある
- 出典：https://github.com/facefusion/facefusion/releases/tag/3.9.1 、https://docs.facefusion.io/usage/cli-arguments/face-selector

## C3. LivingSwap

- プロジェクトページのコードは「Code (Coming Soon)」のまま。**未公開**【確認済み】
- 論文は CVPR 2026、arXiv 2512.07951
- 出典：https://aim-uofa.github.io/LivingSwap/

## C5. 10 章の設定項目名（マニュアル docs/user_manual.md）

| 項目 | マニュアル | 備考 |
|---|---|---|
| Swapper Resolution | ある（4.2） | |
| Blend Amount | ある | |
| ByteTrack、Track Buffer (Frames) | ある | |
| Face Expression Restorer（部位別） | ある（Advanced） | eyes / brows / lips / Jaw・Contour |
| Restore Eyes、Restore Mouth | ある（4.5.8、4.5.9） | |
| Mouth Fit & Align | ある（4.5.6） | |
| XSeg Mask、Profile Angle Mask | ある（4.5.4、4.5.2） | |
| Ref-LDM Denoiser | ある（6 章） | 仕様書の「ReF-LDM」 |
| AutoColor Transfer | ある | |
| Scan for Issues | ある（13.1） | 仕様書の「Issue scanning」 |
| Job Manager | ある | |
| DFM | ある | `onnxmodels/dfm_models` に置く |
| Secondary Swapper、Face Shaping、Number of Streams | ない | UI の定義とリリースノートにはある |
| Texture Transfer | 専用の節はない | UI のラベルとパイプライン位置にある |

実際の画面との一致は、インストール後に確かめる必要があります【未確認】。

## C7. ライセンス

- InsightFace：コードは MIT。学習済みモデル（buffalo_l を含む）は非商用の研究目的のみ。2025-11-24 の追記で、inswapper と buffalo_l は個別のライセンスの問い合わせを求めている【確認済み】
- FaceFusion：コードは OpenRAIL-AS。モデルごとのライセンス【確認済み】

| 種類 | モデル | ライセンス |
|---|---|---|
| 顔交換 | alphaface_256 | Non-Commercial |
| 顔交換 | hyperswap_1a/1b/1c | ResearchRAIL |
| 顔交換 | inswapper_128 | Non-Commercial（InsightFace） |
| 特徴点検出 | hrffa | MIT |
| 表情を戻す | live_portrait | MIT |
| 顔の補正 | codeformer | S-Lab-1.0 |
| 顔の補正 | gfpgan | Apache-2.0 |
| 顔の補正 | gpen | Non-Commercial |
| 顔の検出 | retinaface / scrfd | Non-Commercial |
| 顔の検出 | yolo_face | GPL-3.0 |

- VisoMaster Fusion：GPL-3.0【確認済み】
- 個人利用（U4）の範囲では、どれも使えるとみられる【推測】

## C8. 同じ AI 人物の角度違いの写真を作る手法

1. **Qwen-Image-Edit-2509**（2025-09、Apache-2.0）：複数の画像を入力でき、ポーズを変えても顔の同一性を保つ改善が明記されている【確認済み】 https://huggingface.co/Qwen/Qwen-Image-Edit-2509
2. **FLUX.1 Kontext [dev]**（2025-06）：追加学習なしで人物を参照でき、繰り返し編集しても顔がずれにくいと記載。重みは非商用ライセンス【確認済み】 https://huggingface.co/black-forest-labs/FLUX.1-Kontext-dev
3. **Gemini 2.5 Flash Image（Nano Banana）とその後継**：公式ブログあり。同一性の性能の詳細は読んでいない【未確認】 https://developers.googleblog.com/introducing-gemini-2-5-flash-image/
4. **PuLID / PuLID-FLUX**：最終更新 2025-08【確認済み】 https://github.com/ToTheBeginning/PuLID
5. **InstantID**：SDXL 世代の古い手法 https://github.com/instantX-research/InstantID

2025〜2026 年は、1〜3 のような画像編集モデルで角度を変える方法が主流とみられます【推測】。これより新しいモデルが出ていないかは、作る直前にもう一度確かめてください（U1）。

## C9. HDR（参考）

- マニュアル 14.3 に HDR の書き出しプリセットがある。HDR の書き出しは CPU（libx265）で遅い【確認済み】
- 入力は `cv2.VideoCapture`（8bit）で読み込まれるため、HDR や 10bit を高い階調のまま処理する仕組みはないとみられる【推測】
- コンパニオンツールは HDR の動画に警告を出し、SDR への変換を勧める

## C10. 読み込める動画の形式

- ソースの拡張子の一覧は .mp4 .avi .mkv .mov .wmv .flv .webm .m4v .3gp .gif【確認済み】
- 実際に読めるかは OpenCV 同梱の FFmpeg 次第で、ProRes や FFV1 も読める可能性は高いが、8bit に変換される【推測】。実機では未確認
- コンパニオンツールの初期値は、OpenCV で確実に読める H.264 ロスレス（.mp4）にした

## C11. DFM

- DeepFaceLab の最終コミットは 2024-11-13、DeepFaceLive は 2024-10-24。どちらも実質的に止まっている【確認済み】
- DFM の書き出しは DeepFaceLab の `main.py exportdfm --model-dir <dir> --model <SAEHD|AMP>`。SAEHD と AMP の両方に実装がある【確認済み】
- Windows 版に同梱の .bat の名前はリポジトリにないため未確認

## このツールで使ったライブラリ（参考）

- InsightFace 2.1（2026-10-03 公開、純 Python の wheel なので Windows でもビルド不要）、PySide6 6.12.0、onnxruntime 1.29.0、OpenCV 5.0.0.93 を PyPI で確認して使用

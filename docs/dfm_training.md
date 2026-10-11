# DFM（AI 人物専用モデル）の作り方（2026-10-11）

AI 人物の顔を、誰の顔からでも置き換えられる DFM を DeepFaceLab で学習し、VisoMaster Fusion で使う手順です。
方式は「誰にでも使える（RTM）」です（2026-10-11 にユーザーが選択）。

記号：**【確認済み】** 公式の情報やソースで見た／**【未確認】** 確かめられなかった／**【推測】** 確かめた事実からの推論

## 全体の流れ

1. AI 人物の顔を数百〜数千枚集める（data_src）
2. 大勢の顔のセット（RTM WF Faceset）を用意し、自分の動画の顔を足す（data_dst）
3. DeepFaceLab で学習する
4. DFM に書き出し、VisoMaster Fusion に置く
5. 比較と品質チェックで、写真から入れ替える方式（AlphaFace など）と比べる

## 1. AI 人物の顔（data_src）

DFM は「入力の顔の表情を、AI 人物の顔で描き直す」ことを学びます。**data_src にない表情や角度はうまく描けません**【推測：DeepFaceLab で一般に言われていること】。
DeepFaceLive の案内では、src は「左右の向き、光と影の条件に十分な幅があること」が必要とされています【確認済み】。

集め方は 2 つあり、両方を混ぜます。

- **AI で作った写真**：参照画像から同じ顔を保ったまま、表情と角度と照明を変えた写真を作り、「顔素材」タブにまとめて追加する
  - 表情：力む、歯を食いしばる、目をぎゅっと閉じる、口を開けて息を吐く、苦笑い、汗をかいた顔
  - 角度：少し下向き、見下ろし、斜め、横顔、見上げ
  - 照明：ジムのような上からの光、逆光気味、色のついた光
  - 「ほかの写真と別人に見える」と警告が出た写真は、M8 が自動で外す。歯や目が崩れた写真は手で消す（崩れもそのまま学習される）
  - 年齢や化粧を混ぜない（DeepFaceLive の案内）【確認済み】
- **置き換えた動画のフレーム（M8）**：VisoMaster Fusion で置き換えた出力のうち、品質チェックで問題がなかったフレームから AI の顔を切り出す。実際の動画に出る角度と表情が集まる。出力の質が上限になるので、Face Expression Restorer をオンにした良い出力を使う

ツールの「DFM 学習素材」タブで、品質チェックの結果を選んで「学習用の素材を集める」を押すと、`プロジェクト\dfm\<人物ID>\<ID>\` に次ができます。

- `data_src\`：顔素材の写真（photo_*.jpg）と、出力から切り出した顔（顔の枠の 2 倍の正方形、最大 1024px）
- `data_dst\`：同じフレームの、置き換える前の顔
- `manifest.json`：各画像の元のフレーム、向き、表情、似ている度合い、くっきり度
- `次にやること.txt`

角度（左右×上下）ごとの枚数と、目を細めた顔・口を開けた顔の枚数を表示し、少なければ警告します。1 つの角度に入れる枚数には上限（初期値 300）があり、超えた分は表情の強い顔とくっきりした顔を残して減らします。

## 2. 入力側の顔（data_dst）

誰にでも使える方式（RTM）では、DeepFaceLive の作者が公開している **RTM WF Faceset V2** を `workspace\data_dst\aligned` に置きます【確認済み：DeepFaceLive の FAQ】。入手先は FAQ の「public storage」のリンクです。

M8 で集めた自分の動画の顔（data_dst）は、DeepFaceLab で抽出したあと、この aligned に足すと、自分の動画で質が上がると考えています【推測】。

## 3. DeepFaceLab で学習する

DeepFaceLab は 2024-11-13 に開発が終わり、リポジトリは読み取り専用です【確認済み】。Windows 版はリポジトリの README にある配布先（torrent、Mega、Yandex）から入手します。

以下の操作名（「data_src faceset extract」など）は、配布版のバッチファイルの名前です【推測：記憶による。手元の DeepFaceLab で確かめる】。

1. DeepFaceLab の `workspace\data_src` に、M8 の `data_src` の画像をコピーする
2. 「data_src faceset extract」で顔を抽出する。face type は **WF（whole face）**
3. `workspace\data_src\aligned` を見て、AI の顔でないもの、崩れたものを消す
4. XSeg でマスクを作る。DeepFaceLive の案内では Generic XSeg を勧めている【確認済み】
5. 自分の動画の顔を足す場合は、M8 の `data_dst` を同じように抽出し、`data_dst\aligned` に RTM WF Faceset と一緒に入れる
6. SAEHD で学習する

DeepFaceLive の FAQ にある、ゼロから学習するときの設定です【確認済み】。

| 項目 | 値 |
|---|---|
| resolution | 224 |
| face type | WF |
| archi | liae-udt |
| ae_dims / e_dims / d_dims / d_mask_dims | 512 / 64 / 64 / 32 |
| eyes_mouth_prio | N |
| blur_out_mask | Y |
| uniform_yaw | Y |
| lr_dropout | Y |
| batch | 8 |

手順（FAQ より）【確認済み】

1. 上の設定で約 200 万回学習する。10 万〜50 万回ごとに `inter_AB.npy` を消す（保存してから消し、続ける）
2. random_warp をオンのまま、さらに約 50 万回
3. random_warp をオフ、GAN 0.1、patch size 28、gan_dims 32 にして、さらに約 70 万回

FAQ の公開ストレージにある学習済みモデル（RTT model 224 V2）から始める方法もあり、この場合は約 50 万回 → 設定を変えて約 70 万回です。
いちばん速い方法（RTT model から約 2.5 万回 → `inter_AB.npy` を消して約 3 万回 → GAN をオンにして 24 時間で止める）は、RTX 3090 で 1 日、質は落ちるとされています【確認済み】。

**RTX 3080（10GB）について**：FAQ は学習に RTX 3090 以上を勧めています【確認済み】。10GB では batch を下げる必要があり、時間は 3090 より長くかかります【推測】。数日〜数週間の連続運転になる可能性があります【推測】。クラウドの GPU を借りる方法もあります。

## 4. DFM に書き出して VisoMaster Fusion で使う

1. DeepFaceLab で、学習したモデルを DFM に書き出す（バッチの名前は「export SAEHD as dfm」を含むもの【推測：配布版のバッチ名。FAQ には「.dfm 形式で書き出す」とだけある】）
2. できた `.dfm` を VisoMaster Fusion の `model_assets\dfm_models\` に置く【確認済み：ソースの DFMModelManager は `./model_assets/dfm_models` から .dfm と .onnx を探す】
3. VisoMaster Fusion の Swapper Model で「DeepFaceLive (DFM)」を選び、DFM Model で作ったモデルを選ぶ【確認済み：ソースの swapper_layout_data.py】
4. 関係する設定：AMP Morph Factor（AMP 型のモデル用）、RCT Color Transfer（色合わせ）、Maximum DFM Models to use（同時に読み込む数。VRAM に合わせる）【確認済み：ソース】

## 5. 比べる

VisoMaster Fusion で、同じテスト用クリップを「AlphaFace」と「DeepFaceLive (DFM)」で書き出し、ツールの「比較」タブで並べ、「品質チェック」タブで数値を比べます。
筋トレの力む場面（P1）と、下を向いて顔が隠れる場面（P3・P4）を必ず入れます。

## 出典

- DeepFaceLive の FAQ（RTM の学習手順と設定）：https://github.com/iperov/DeepFaceLive/blob/master/doc/user_faq/user_faq.md
- DeepFaceLab（2024-11-13 にアーカイブ）：https://github.com/iperov/DeepFaceLab
- VisoMaster Fusion のソース（2026-10-04 の main）：`app/helpers/miscellaneous.py`（DFMModelManager）、`app/ui/widgets/swapper_layout_data.py`

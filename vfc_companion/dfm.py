"""M8：DFM の学習用素材を集める（仕様 11 章 M8、フェーズ 4）。

DFM は DeepFaceLab で学習する、AI 人物専用の顔交換モデル。VisoMaster Fusion の
Swapper Model「DeepFaceLive (DFM)」で使える（model_assets/dfm_models に置く。ソースで確認）。

学習には 2 種類の顔が大量に要る。
- data_src：AI 人物の顔。品質チェック（M6）で問題がなかったフレームの「出力」から切り出す。顔素材の写真も入れる
- data_dst：置き換える前の顔。同じフレームの「元のクリップ」から切り出す

切り出しは顔の枠より広い正方形（余白つき）にしておき、顔の位置合わせは DeepFaceLab の抽出に任せる。
DeepFaceLab と VisoMaster Fusion の内部には触れない。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import numpy as np

from .faces import PITCH_BINS, YAW_BINS, cosine, pitch_bin, yaw_bin
from .project import Project, ProjectError, new_id
from .util import now_iso, write_json_atomic

DFM_DIR = "dfm"
NEXT_STEPS = """次にやること（詳しくはツールの docs/dfm_training.md）

1. DeepFaceLab の workspace フォルダに、この data_src と data_dst をコピーする
2. 「data_src faceset extract」と「data_dst faceset extract」で顔を抽出する（face type は wf を推奨【推測】）
3. 抽出された顔（aligned）を見て、AI の顔でないもの、崩れた顔を消す
4. SAEHD で学習し、DFM に書き出す（名前に「export SAEHD as dfm」を含むバッチ）
5. できた .dfm を VisoMaster Fusion の model_assets\\dfm_models に置き、Swapper Model で「DeepFaceLive (DFM)」を選ぶ
"""


def _imwrite_jpg(path: Path, img: np.ndarray, quality: int = 95) -> bool:
    import cv2

    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if ok:
        buf.tofile(str(path))
    return bool(ok)


def crop_square(img: np.ndarray, bbox, scale: float, max_size: int) -> np.ndarray:
    """顔の枠の中心から、枠の scale 倍の正方形を切り出す。はみ出した部分は黒で埋める。"""
    import cv2

    x1, y1, x2, y2 = (float(v) for v in bbox)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    side = max(x2 - x1, y2 - y1) * scale
    a = int(round(cx - side / 2))
    b = int(round(cy - side / 2))
    s = max(1, int(round(side)))
    h, w = img.shape[:2]
    out = np.zeros((s, s, 3), np.uint8)
    sx0, sy0, sx1, sy1 = max(0, a), max(0, b), min(w, a + s), min(h, b + s)
    if sx1 > sx0 and sy1 > sy0:
        out[sy0 - b:sy1 - b, sx0 - a:sx1 - a] = img[sy0:sy1, sx0:sx1]
    if s > max_size:
        out = cv2.resize(out, (max_size, max_size), interpolation=cv2.INTER_AREA)
    return out


def sharpness(img: np.ndarray, bbox) -> float:
    """顔の部分のぼけにくさ（256px に揃えたラプラシアンの分散）。大きいほどくっきり。"""
    import cv2

    x1, y1, x2, y2 = (int(round(float(v))) for v in bbox)
    h, w = img.shape[:2]
    face = img[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
    if face.size == 0:
        return 0.0
    g = cv2.cvtColor(cv2.resize(face, (256, 256), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


def expression(face, settings: dict[str, Any]) -> dict[str, Any] | None:
    """68 点の特徴点から、目の細さと口の開きを測る。横を向きすぎた顔（|左右| > 45 度）は測らない。

    目の縦横比（EAR）は開いた目で 0.25〜0.35 ほど、口の縦横比（MAR）は閉じた口で 0.1 未満
    （InsightFace のサンプル画像で確認）。しきい値は推測で、設定で変えられる。
    """
    lm = getattr(face, "lmk68", None)
    if lm is None or abs(face.yaw) > 45:
        return None
    p = np.asarray(lm, dtype=np.float64)[:, :2]

    def d(i: int, j: int) -> float:
        return float(np.linalg.norm(p[i] - p[j]))

    def ear(e: list[int]) -> float:
        return (d(e[1], e[5]) + d(e[2], e[4])) / max(1e-6, 2 * d(e[0], e[3]))

    eye = (ear([36, 37, 38, 39, 40, 41]) + ear([42, 43, 44, 45, 46, 47])) / 2
    mouth = (d(61, 67) + d(62, 66) + d(63, 65)) / max(1e-6, 3 * d(60, 64))
    return {"ear": round(eye, 3), "mar": round(mouth, 3),
            "eyes_narrow": eye < float(settings["dfm_eye_narrow_ear"]),
            "mouth_open": mouth > float(settings["dfm_mouth_open_mar"])}


def _expressive(it: dict[str, Any]) -> bool:
    e = it.get("expr")
    return bool(e and (e["eyes_narrow"] or e["mouth_open"]))


def _iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = (float(v) for v in a)
    bx1, by1, bx2, by2 = (float(v) for v in b)
    iw, ih = max(0.0, min(ax2, bx2) - max(ax1, bx1)), max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / union if union > 0 else 0.0


def candidate_frames(metrics: dict[str, Any], person_id: str, min_gap: int) -> list[int]:
    """品質チェックで問題がなく、その人物の顔があったフレーム（min_gap 以上あけて間引く）。"""
    if person_id not in metrics["series"]:
        return []
    bad = {int(k) for k in metrics["reasons_by_frame"]}
    sims = metrics["series"][person_id]["sim_set"]
    out: list[int] = []
    for n, v in zip(metrics["frames"], sims):
        if v is None or n in bad:
            continue
        if out and n - out[-1] < min_gap:
            continue
        out.append(n)
    return out


def coverage(items: list[dict[str, Any]], settings: dict[str, Any]) -> dict[str, Any]:
    """左右×上下の枚数と、足りない角度・偏りの警告。"""
    yaw_keys = [k for k, *_ in YAW_BINS]
    pitch_keys = [k for k, *_ in PITCH_BINS]
    grid = {y: {p: 0 for p in pitch_keys} for y in yaw_keys}
    for it in items:
        grid[it["yaw_bin"]][it["pitch_bin"]] += 1
    yaw_counts = {y: sum(grid[y].values()) for y in yaw_keys}
    pitch_counts = {p: sum(grid[y][p] for y in yaw_keys) for p in pitch_keys}
    total = len(items)
    need = int(settings["dfm_min_per_bin"])
    yl = {k: lbl for k, lbl, *_ in YAW_BINS}
    pl = {k: lbl for k, lbl, *_ in PITCH_BINS}
    warnings = []
    # 横顔と見上げ・見下ろしは動画によっては出ないので、正面寄りの向きと少しの上下を必須にする
    for k in ("left_45", "front", "right_45"):
        if yaw_counts[k] < need:
            warnings.append(f"{yl[k]}が不足（{yaw_counts[k]}/{need} 枚）")
    for k in ("down", "level", "up"):
        if pitch_counts[k] < need:
            warnings.append(f"{pl[k]}が不足（{pitch_counts[k]}/{need} 枚）")
    if total:
        top = max(yaw_counts, key=yaw_counts.get)
        if yaw_counts[top] / total > float(settings["dfm_bias_ratio"]):
            warnings.append(f"{yl[top]}に偏っています（{yaw_counts[top] / total:.0%}）。ほかの向きが映る動画を足してください")
    expr = {"eyes_narrow": sum(1 for it in items if (it.get("expr") or {}).get("eyes_narrow")),
            "mouth_open": sum(1 for it in items if (it.get("expr") or {}).get("mouth_open")),
            "expressive": sum(1 for it in items if _expressive(it)),
            "measured": sum(1 for it in items if it.get("expr"))}
    need_e = int(settings["dfm_min_expressive"])
    if expr["expressive"] < need_e:
        warnings.append(f"目を細めた顔や口を開けた顔が少ない（{expr['expressive']}/{need_e} 枚）。力む・苦しい表情の"
                        "AI の写真や、表情が強い場面の出力を足してください")
    return {"total": total, "grid": grid, "yaw_counts": yaw_counts, "pitch_counts": pitch_counts,
            "expressions": expr, "warnings": warnings}


def _prune(items: list[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
    """角度ごとに cap 枚を超えた分を、くっきりして似ている順に残して消す。写真は消さない。"""
    by_bin: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for it in items:
        by_bin.setdefault((it["yaw_bin"], it["pitch_bin"]), []).append(it)
    removed = []
    for group in by_bin.values():
        frames = [it for it in group if it["kind"] != "photo"]
        if len(frames) <= cap:
            continue
        # 表情の強い顔は少ないので優先して残す。そのあとは、くっきりして似ている順
        frames.sort(key=lambda it: (_expressive(it), (it.get("sim") or 0) * np.log1p(it["sharpness"])), reverse=True)
        removed += frames[cap:]
    for it in removed:
        Path(it["path"]).unlink(missing_ok=True)
    gone = {id(it) for it in removed}
    return [it for it in items if id(it) not in gone]


def collect(project: Project, person_id: str, qc_ids: list[str], *, include_dst: bool = True,
            detect_fn: Callable[[np.ndarray], list] | None = None,
            progress: Callable[[int, int, str], None] | None = None,
            should_stop: Callable[[], bool] | None = None) -> dict[str, Any]:
    """品質チェックの結果から、DFM の学習用素材（data_src / data_dst）を作る。"""
    from .face_assets import _load_embs
    from .frames import imread_unicode, read_frame
    from .media import probe
    from .qc import load_metrics, person_template, report_record

    say = progress or (lambda *_a: None)
    s = project.settings
    person = project.person(person_id)
    if not qc_ids:
        raise ProjectError("品質チェックの結果を 1 つ以上選んでください。")
    if detect_fn is None:
        from .faces import detect

        def detect_fn(img):
            return detect(img, 0.5)

    template = person_template(project, person_id)
    up = int(s["pitch_up_sign"])
    sid = new_id("dfm")
    root = project.root / DFM_DIR / person_id / sid
    src_dir, dst_dir = root / "data_src", root / "data_dst"
    src_dir.mkdir(parents=True, exist_ok=True)
    if include_dst:
        dst_dir.mkdir(parents=True, exist_ok=True)
    scale, max_size = float(s["dfm_crop_scale"]), int(s["dfm_crop_size"])
    min_px, min_sim = float(s["dfm_min_face_px"]), float(s["dfm_min_sim_faceset"])
    min_sharp = float(s["dfm_min_sharpness"])
    skipped = {"no_face": 0, "low_sim": 0, "small": 0, "blurry": 0, "no_dst": 0, "dissimilar_photo": 0}
    src_items: list[dict[str, Any]] = []
    dst_items: list[dict[str, Any]] = []

    # 顔素材の写真（AI 人物そのもの）
    embs = _load_embs(project, person_id)
    for img in person["images"]:
        a = img.get("analysis", {})
        f = a.get("face")
        # 顔素材の検査で「ほかの写真と似ていない」とされた写真は、別人に見えるので学習に入れない
        if not f or set(a.get("issues", [])) & {"no_face", "multi_face", "unreadable", "dissimilar"}:
            skipped["dissimilar_photo"] += "dissimilar" in a.get("issues", [])
            continue
        dest = src_dir / f"photo_{img['id']}.jpg"
        pic = imread_unicode(project.abs(img["file"]))
        if pic is None or not _imwrite_jpg(dest, pic):
            continue
        e = embs.get(img["id"])
        pf = detect_fn(pic)
        src_items.append({"kind": "photo", "path": str(dest), "image_id": img["id"], "yaw": f["yaw"],
                          "pitch": f["pitch"], "yaw_bin": f["yaw_bin"], "pitch_bin": f["pitch_bin"],
                          "sim": None if e is None else round(cosine(e, template), 3), "sharpness": 0.0,
                          "expr": expression(pf[0], s) if pf else None})

    reports = []
    for qid in qc_ids:
        rec = report_record(project, qid)
        m = load_metrics(project, qid)
        if person_id not in m["series"]:
            raise ProjectError(f"品質チェック {qid} には {person['name']} が含まれていません。")
        render = project.render(rec["render_id"])
        clip = project.clip(rec["clip_id"])
        frames = candidate_frames(m, person_id, int(s["dfm_min_gap_frames"]))
        reports.append((qid, render, clip, frames))
    total = sum(len(r[3]) for r in reports)
    done = 0
    for qid, render, clip, frames in reports:
        out_path, clip_path = project.abs(render["file"]), project.abs(clip["file"])
        omi = probe(out_path, s, measure_luma=False)
        cmi = probe(clip_path, s, measure_luma=False)
        for n in frames:
            if should_stop and should_stop():
                break
            done += 1
            say(done, total, f"学習用の顔を切り出しています（{done}/{total}）")
            oimg = read_frame(out_path, n, omi.fps, omi.display_width, omi.display_height)
            if oimg is None:
                skipped["no_face"] += 1
                continue
            faces = [f for f in detect_fn(oimg) if f.embedding is not None]
            if not faces:
                skipped["no_face"] += 1
                continue
            sims = [cosine(f.embedding, template) for f in faces]
            k = int(np.argmax(sims))
            face, sim = faces[k], sims[k]
            if sim < min_sim:
                skipped["low_sim"] += 1
                continue
            if face.size < min_px:
                skipped["small"] += 1
                continue
            sh = sharpness(oimg, face.bbox)
            if sh < min_sharp:
                skipped["blurry"] += 1
                continue
            yk, _ = yaw_bin(face.yaw)
            pk, _ = pitch_bin(face.pitch, up)
            name = f"{render['id']}_f{n:06d}.jpg"
            p = src_dir / name
            _imwrite_jpg(p, crop_square(oimg, face.bbox, scale, max_size))
            src_items.append({"kind": "frame", "path": str(p), "qc_id": qid, "render_id": render["id"],
                              "clip_id": clip["id"], "frame": n, "yaw": round(face.yaw, 1),
                              "pitch": round(face.pitch, 1), "yaw_bin": yk, "pitch_bin": pk,
                              "sim": round(sim, 3), "sharpness": round(sh, 1),
                              "face_px": round(face.size, 1), "expr": expression(face, s)})
            if not include_dst:
                continue
            cimg = read_frame(clip_path, n, cmi.fps, cmi.display_width, cmi.display_height)
            if cimg is None:
                skipped["no_dst"] += 1
                continue
            # 同じ位置にある元の顔（置き換えでは顔の位置は変わらない）。解像度が違えば枠を合わせる
            fx, fy = cimg.shape[1] / oimg.shape[1], cimg.shape[0] / oimg.shape[0]
            ob = [face.bbox[0] * fx, face.bbox[1] * fy, face.bbox[2] * fx, face.bbox[3] * fy]
            cf = [(f, _iou(f.bbox, ob)) for f in detect_fn(cimg)]
            cf = [c for c in cf if c[1] >= 0.3]
            if not cf:
                skipped["no_dst"] += 1
                continue
            dface = max(cf, key=lambda c: c[1])[0]
            dyk, _ = yaw_bin(dface.yaw)
            dpk, _ = pitch_bin(dface.pitch, up)
            dp = dst_dir / f"{clip['id']}_f{n:06d}.jpg"
            if not dp.exists():
                _imwrite_jpg(dp, crop_square(cimg, dface.bbox, scale, max_size))
                dst_items.append({"kind": "frame", "path": str(dp), "clip_id": clip["id"], "frame": n,
                                  "yaw": round(dface.yaw, 1), "pitch": round(dface.pitch, 1), "yaw_bin": dyk,
                                  "pitch_bin": dpk, "sim": None, "sharpness": round(sharpness(cimg, dface.bbox), 1),
                                  "expr": expression(dface, s)})

    cap = int(s["dfm_max_per_bin"])
    before = len(src_items) + len(dst_items)
    src_items = _prune(src_items, cap)
    dst_items = _prune(dst_items, cap)
    skipped["pruned"] = before - len(src_items) - len(dst_items)
    src_cov = coverage(src_items, s)
    dst_cov = coverage(dst_items, s) if include_dst else None
    for it in src_items + dst_items:
        it["file"] = project.rel(it.pop("path"))
    write_json_atomic(root / "manifest.json", {"id": sid, "person_id": person_id, "qc_ids": qc_ids,
                                               "created_at": now_iso(), "src": src_items, "dst": dst_items})
    (root / "次にやること.txt").write_text(NEXT_STEPS, encoding="utf-8")
    rec = {"id": sid, "person_id": person_id, "qc_ids": list(qc_ids), "dir": project.rel(root),
           "created_at": now_iso(), "include_dst": include_dst,
           "src_photos": sum(1 for it in src_items if it["kind"] == "photo"),
           "src_frames": sum(1 for it in src_items if it["kind"] == "frame"),
           "dst_frames": len(dst_items), "src_coverage": src_cov, "dst_coverage": dst_cov,
           "skipped": skipped, "cancelled": bool(should_stop and should_stop())}
    project.data.setdefault("dfm_sets", []).append(rec)
    project.log("dfm_set_collected", {"id": sid, "person_id": person_id, "src": len(src_items),
                                      "dst": len(dst_items)})
    return rec


def eligible_reports(project: Project, person_id: str) -> list[dict[str, Any]]:
    """その人物が含まれる品質チェックの結果（新しい順）。"""
    from .qc import load_metrics

    out = []
    for q in reversed(project.data.get("qc_reports", [])):
        try:
            m = load_metrics(project, q["id"])
        except Exception:  # noqa: BLE001
            continue
        if person_id in m.get("series", {}):
            out.append(q)
    return out

"""M4: 顔素材の管理（仕様 11.3 M4、9.2）。

人物 ID ごとに faces/<人物ID>/ に写真をコピーし、次を検査する。
- 顔が 1 つだけ写っているか
- 顔の部分の解像度が十分か（目安 512px 以上）
- メガネがないか（簡易判定）
- 顔の向き（左右・上下・傾き）と、角度ごとの枚数の偏り
- 同じ人物の写真どうしが十分に似ているか
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .consent import KIND_FACE_PERSON, STATEMENT_FACE_PERSON, ConsentRequired
from .faces import PITCH_BINS, YAW_BINS, cosine, describe_face, detect, glasses_score
from .frames import imread_unicode
from .project import Project, ProjectError, new_id
from .util import now_iso, safe_id, sha256_file, unique_path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
EMB_FILE = ".embeddings.npz"

# 角度ごとに最低限ほしい枚数【推測】。仕様 9.2 の角度の一覧から決めた初期値。
MIN_PER_YAW = {"front": 3, "left_45": 2, "right_45": 2, "left_profile": 2, "right_profile": 2}
MIN_PER_PITCH = {"up": 1, "down": 1, "up_strong": 1, "down_strong": 1}

ISSUE_TEXT = {
    "unreadable": "画像を読み込めません",
    "no_face": "顔が見つかりません",
    "multi_face": "顔が複数写っています（1 人だけの写真にしてください）",
    "low_res": "顔の部分の解像度が足りません",
    "glasses": "メガネをかけている可能性があります（簡易判定）",
    "dissimilar": "ほかの写真と別人に見える可能性があります",
}


def person_dir(project: Project, person_id: str) -> Path:
    return project.root / "faces" / person_id


def add_person(project: Project, name: str, *, consent_confirmed: bool, note: str = "") -> dict[str, Any]:
    """AI 人物を登録する。S1（AI で作った実在しない人物であること）の確認が必要。"""
    if not consent_confirmed:
        raise ConsentRequired("AI で作った実在しない人物であることの確認（S1）が必要です。")
    if not name.strip():
        raise ProjectError("人物の名前を入力してください")
    base = safe_id(name) or "person"
    pid = f"p_{base}"
    existing = {p["id"] for p in project.data["persons"]}
    if pid in existing or person_dir(project, pid).exists():
        pid = f"p_{base}_{new_id('x')[2:6]}"
    person_dir(project, pid).mkdir(parents=True, exist_ok=True)
    c = project.consent.add(KIND_FACE_PERSON, STATEMENT_FACE_PERSON, target=pid, extra={"name": name})
    rec = {
        "id": pid,
        "name": name,
        "note": note,
        "consent_id": c["id"],
        "created_at": now_iso(),
        "images": [],
        "summary": {},
    }
    project.data["persons"].append(rec)
    project.log("person_added", {"id": pid, "name": name})
    return rec


def analyze_image(path: str | Path, settings: dict[str, Any]) -> tuple[dict[str, Any], np.ndarray | None]:
    img = imread_unicode(path)
    if img is None:
        return {"issues": ["unreadable"], "n_faces": 0}, None
    faces = detect(img, float(settings["asset_det_score"]))
    res: dict[str, Any] = {"image_size": [int(img.shape[1]), int(img.shape[0])], "n_faces": len(faces), "issues": []}
    if not faces:
        res["issues"].append("no_face")
        return res, None
    if len(faces) > 1:
        res["issues"].append("multi_face")
    f = faces[0]
    res["face"] = describe_face(f, int(settings["pitch_up_sign"]))
    if f.size < float(settings["asset_min_face_px"]):
        res["issues"].append("low_res")
    g = glasses_score(img, f)
    res["glasses_score"] = None if g is None else round(g, 3)
    if g is not None and g >= float(settings["asset_glasses_edge"]):
        res["issues"].append("glasses")
    return res, f.embedding


def _load_embs(project: Project, person_id: str) -> dict[str, np.ndarray]:
    p = person_dir(project, person_id) / EMB_FILE
    if not p.exists():
        return {}
    with np.load(p) as z:
        return {k: z[k] for k in z.files}


def _save_embs(project: Project, person_id: str, embs: dict[str, np.ndarray]) -> None:
    p = person_dir(project, person_id) / EMB_FILE
    tmp = p.with_name(p.name + ".tmp.npz")
    np.savez(tmp, **embs)
    tmp.replace(p)


def add_images(project: Project, person_id: str, paths: list[str | Path], *,
               progress: Callable[[int, int, str], None] | None = None) -> list[dict[str, Any]]:
    """写真をまとめて登録して検査する。"""
    person = project.person(person_id)
    settings = project.settings
    d = person_dir(project, person_id)
    d.mkdir(parents=True, exist_ok=True)
    embs = _load_embs(project, person_id)
    known = {img["sha256"] for img in person["images"]}
    added = []
    files = [Path(p) for p in paths]
    for i, src in enumerate(files):
        if progress:
            progress(i, len(files), src.name)
        if src.suffix.lower() not in IMAGE_EXTS or not src.is_file():
            continue
        digest = sha256_file(src)
        if digest in known:
            continue  # 同じ写真は二重に登録しない
        dest = unique_path(d / src.name)
        shutil.copy2(src, dest)
        analysis, emb = analyze_image(dest, settings)
        iid = new_id("img")
        if emb is not None:
            embs[iid] = emb
        rec = {
            "id": iid,
            "file": project.rel(dest),
            "original_name": src.name,
            "sha256": digest,
            "added_at": now_iso(),
            "analysis": analysis,
        }
        person["images"].append(rec)
        known.add(digest)
        added.append(rec)
    if progress:
        progress(len(files), len(files), "")
    _save_embs(project, person_id, embs)
    recompute_person(project, person_id, save=False)
    project.log("face_images_added", {"person_id": person_id, "count": len(added)})
    return added


def remove_image(project: Project, person_id: str, image_id: str, *, delete_file: bool = True) -> None:
    person = project.person(person_id)
    rec = next((x for x in person["images"] if x["id"] == image_id), None)
    if rec is None:
        raise ProjectError(f"写真が見つかりません: {image_id}")
    person["images"].remove(rec)
    embs = _load_embs(project, person_id)
    embs.pop(image_id, None)
    _save_embs(project, person_id, embs)
    if delete_file:
        f = project.abs(rec["file"])
        if f.exists():
            f.unlink()
    recompute_person(project, person_id, save=False)
    project.log("face_image_removed", {"person_id": person_id, "image_id": image_id})


def reanalyze_person(project: Project, person_id: str) -> None:
    """設定を変えたあとなどに、全写真を検査し直す。"""
    person = project.person(person_id)
    settings = project.settings
    embs = {}
    for rec in person["images"]:
        analysis, emb = analyze_image(project.abs(rec["file"]), settings)
        rec["analysis"] = analysis
        if emb is not None:
            embs[rec["id"]] = emb
    _save_embs(project, person_id, embs)
    recompute_person(project, person_id, save=False)
    project.log("face_images_reanalyzed", {"person_id": person_id})


def recompute_person(project: Project, person_id: str, *, save: bool = True) -> dict[str, Any]:
    """似ている度合いと、角度ごとの枚数を計算し直す。"""
    person = project.person(person_id)
    s = project.settings
    embs = _load_embs(project, person_id)
    ids = [img["id"] for img in person["images"] if img["id"] in embs and "multi_face" not in img["analysis"]["issues"]]
    for img in person["images"]:
        a = img["analysis"]
        a["issues"] = [x for x in a.get("issues", []) if x != "dissimilar"]
        a.pop("similarity_to_others", None)
    if len(ids) >= 2:
        mat = np.stack([embs[i] / (np.linalg.norm(embs[i]) + 1e-9) for i in ids])
        total = mat.sum(axis=0)
        for k, iid in enumerate(ids):
            # その写真を除いた残りの平均と比べる（leave-one-out）
            others = (total - mat[k]) / (len(ids) - 1)
            sim = cosine(mat[k], others)
            img = next(x for x in person["images"] if x["id"] == iid)
            img["analysis"]["similarity_to_others"] = round(sim, 3)
            if sim < float(s["asset_similarity_warn"]):
                img["analysis"]["issues"].append("dissimilar")
    person["summary"] = coverage(person)
    if save:
        project.log("person_recomputed", {"person_id": person_id})
    return person["summary"]


def coverage(person: dict[str, Any]) -> dict[str, Any]:
    """角度ごとの枚数と、足りない角度の一覧。"""
    yaw_counts = {k: 0 for k, *_ in YAW_BINS}
    pitch_counts = {k: 0 for k, *_ in PITCH_BINS}
    usable = 0
    sims = []
    for img in person["images"]:
        a = img["analysis"]
        face = a.get("face")
        if not face or "multi_face" in a["issues"]:
            continue
        usable += 1
        yaw_counts[face["yaw_bin"]] += 1
        pitch_counts[face["pitch_bin"]] += 1
        if "similarity_to_others" in a:
            sims.append(a["similarity_to_others"])
    yaw_label = {k: lbl for k, lbl, *_ in YAW_BINS}
    pitch_label = {k: lbl for k, lbl, *_ in PITCH_BINS}
    shortages = []
    for k, need in MIN_PER_YAW.items():
        if yaw_counts[k] < need:
            shortages.append(f"{yaw_label[k]}が不足（{yaw_counts[k]}/{need} 枚）")
    for k, need in MIN_PER_PITCH.items():
        if pitch_counts[k] < need:
            shortages.append(f"{pitch_label[k]}が不足（{pitch_counts[k]}/{need} 枚）")
    n_issues = sum(1 for img in person["images"] if img["analysis"].get("issues"))
    return {
        "total": len(person["images"]),
        "usable": usable,
        "with_issues": n_issues,
        "yaw_counts": yaw_counts,
        "pitch_counts": pitch_counts,
        "shortages": shortages,
        "similarity_min": round(min(sims), 3) if sims else None,
        "similarity_mean": round(float(np.mean(sims)), 3) if sims else None,
    }


def issue_texts(analysis: dict[str, Any], settings: dict[str, Any]) -> list[str]:
    out = []
    for code in analysis.get("issues", []):
        t = ISSUE_TEXT.get(code, code)
        if code == "low_res" and analysis.get("face"):
            t += f"（{analysis['face']['face_px']:.0f}px、目安 {settings['asset_min_face_px']}px 以上）"
        if code == "dissimilar" and "similarity_to_others" in analysis:
            t += f"（{analysis['similarity_to_others']:.2f}）"
        out.append(t)
    return out

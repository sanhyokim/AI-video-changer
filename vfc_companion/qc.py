"""M6: 品質チェック（仕様 11.3 M6、12 章）。

出力の動画と元のクリップを、同じフレーム番号どうしで比べ、フレームごとに次を計算する。

| 指標 | 計算 |
|---|---|
| 顔の検出漏れ | 元のフレームでは置き換える顔があるのに、出力の同じ位置で顔が見つからない |
| 顔素材との似ている度合い | 出力の顔の ArcFace 埋め込みと、人物の顔素材の平均とのコサイン類似度 |
| 元の人物との似ている度合い | 出力の顔と、元の同じフレームの顔とのコサイン類似度（高いと置き換え不足） |
| ちらつき | 隣り合うフレームでの「顔素材との似ている度合い」の変化量 |
| 再び現れたときの一貫性 | 顔が消える前と後の「顔素材との似ている度合い」の平均の差 |
| 取り違え | 複数人のとき、割り当てと違う人物の顔素材のほうが似ている。置き換えてはいけない顔が置き換わった |

基準値はすべて初期値【推測】。ユーザーが「良い」と判断した出力で計算した分布から決め直す（calibrate）。
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .project import Project, ProjectError, new_id
from .util import now_iso, read_json, write_json_atomic

REASONS = {
    "miss": "顔の検出漏れ",
    "low_sim_faceset": "顔素材と似ていない",
    "high_sim_original": "元の顔が残っている",
    "flicker": "ちらつき",
    "reappear": "再び現れたとき別人",
    "mismatch": "取り違え（別の人物の顔）",
    "wrong_swap": "置き換えてはいけない顔が置き換わった",
}

# 12.1 の見るポイント。数値にしにくいものは目視のチェックリストにする（仕様 M6）
VISUAL_CHECKS = [
    ("expression", "表情の再現度（元の動画と同じ表情か。力み、口の開き、眉の動き）"),
    ("neck_seam", "首やあごの境目が目立たないか"),
    ("flicker", "フレーム間のちらつきがないか（自動の指標も参照）"),
    ("skin_color", "顔と首・体の肌の色が合っているか"),
    ("reappear", "顔が再び見えたときに同じ顔に戻っているか（自動の指標も参照）"),
    ("motion_blur", "顔と体のブレ方が合っているか"),
]

DetectFn = Callable[[np.ndarray], list]


# =====================================================================
# 顔の解析（キャッシュつき）
# =====================================================================
def _default_detect(settings: dict[str, Any]) -> DetectFn:
    from .faces import QC_MODULES, detect

    det_size = int(settings["qc_det_size"])
    return lambda img: detect(img, 0.5, det_size=det_size, modules=QC_MODULES)


def _cache_key(path: Path, settings: dict[str, Any], tag: str) -> str:
    st = path.stat()
    blob = json.dumps([str(path.resolve()), st.st_size, int(st.st_mtime), settings["qc_width"],
                       settings["qc_det_size"], settings["qc_step"], tag])
    return hashlib.sha256(blob.encode()).hexdigest()[:20]


def analyze_video(path: Path, settings: dict[str, Any], *, detect_fn: DetectFn | None = None,
                  cache_dir: Path | None = None, cache_tag: str = "",
                  progress: Callable[[int, int], None] | None = None,
                  should_stop: Callable[[], bool] | None = None) -> dict[int, list[dict[str, Any]]]:
    """フレーム番号 → 顔の一覧（bbox は画面に対する 0〜1 の割合、emb は長さ 1 の埋め込み）。"""
    from .frames import iter_frames
    from .media import probe

    key = None
    if cache_dir is not None and detect_fn is None:
        key = _cache_key(path, settings, cache_tag)
        cf = cache_dir / f"{key}.npz"
        if cf.exists():
            with np.load(cf) as z:
                return _unpack(z)
    detect_fn = detect_fn or _default_detect(settings)
    mi = probe(path, settings, measure_luma=False)
    step = max(1, int(settings["qc_step"]))
    total = (mi.frame_count + step - 1) // step
    out: dict[int, list[dict[str, Any]]] = {}
    for i, (n, img) in enumerate(iter_frames(path, mi.fps, mi.display_width, mi.display_height, step=step,
                                             max_width=int(settings["qc_width"]))):
        if should_stop and should_stop():
            raise InterruptedError("中止しました")
        h, w = img.shape[:2]
        faces = []
        for f in detect_fn(img):
            if f.embedding is None:
                continue
            b = np.asarray(f.bbox, dtype=np.float32) / np.array([w, h, w, h], np.float32)
            e = np.asarray(f.embedding, dtype=np.float32)
            faces.append({"bbox": b, "emb": e / (np.linalg.norm(e) + 1e-9), "score": float(f.det_score)})
        out[n] = faces
        if progress and i % 5 == 0:
            progress(i, total)
    if key is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        _pack(out, cache_dir / f"{key}.npz")
    return out


def _pack(data: dict[int, list[dict[str, Any]]], path: Path) -> None:
    frames = sorted(data)
    counts = [len(data[n]) for n in frames]
    faces = [f for n in frames for f in data[n]]
    np.savez_compressed(
        path, frames=np.array(frames, np.int64), counts=np.array(counts, np.int64),
        bbox=np.array([f["bbox"] for f in faces], np.float32).reshape(-1, 4),
        emb=np.array([f["emb"] for f in faces], np.float32).reshape(-1, 512) if faces else np.zeros((0, 512)),
        score=np.array([f["score"] for f in faces], np.float32))


def _unpack(z) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = {}
    k = 0
    for n, c in zip(z["frames"].tolist(), z["counts"].tolist()):
        out[n] = [{"bbox": z["bbox"][k + j], "emb": z["emb"][k + j], "score": float(z["score"][k + j])}
                  for j in range(c)]
        k += c
    return out


# =====================================================================
# 照合の道具
# =====================================================================
def iou(a: np.ndarray, b: np.ndarray) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return float(inter / ua) if ua > 0 else 0.0


def match_faces(src: list[dict], out: list[dict], min_iou: float = 0.3) -> dict[int, int]:
    """元の顔の番号 → 出力の顔の番号。位置の重なり（IoU）の大きい順に 1 対 1 で結ぶ。"""
    pairs = sorted(((iou(s["bbox"], o["bbox"]), i, j) for i, s in enumerate(src) for j, o in enumerate(out)),
                   reverse=True)
    used_s, used_o, res = set(), set(), {}
    for v, i, j in pairs:
        if v < min_iou:
            break
        if i in used_s or j in used_o:
            continue
        res[i] = j
        used_s.add(i)
        used_o.add(j)
    return res


def _area(b: np.ndarray) -> float:
    return float((b[2] - b[0]) * (b[3] - b[1]))


def sort_by_order(faces: list[dict], order: str) -> list[dict]:
    """FaceFusion の --face-selector-order と同じ並べ方（位置の番号を合わせるため）。"""
    keyfn = {
        "large-small": lambda f: -_area(f["bbox"]), "small-large": lambda f: _area(f["bbox"]),
        "left-right": lambda f: f["bbox"][0], "right-left": lambda f: -f["bbox"][0],
        "top-bottom": lambda f: f["bbox"][1], "bottom-top": lambda f: -f["bbox"][1],
        "best-worst": lambda f: -f["score"], "worst-best": lambda f: f["score"],
    }.get(order, lambda f: -_area(f["bbox"]))
    return sorted(faces, key=keyfn)


def person_template(project: Project, person_id: str) -> np.ndarray:
    from .face_assets import _load_embs

    person = project.person(person_id)
    embs = _load_embs(project, person_id)
    vecs = []
    for img in person["images"]:
        issues = img.get("analysis", {}).get("issues", [])
        if img["id"] in embs and "multi_face" not in issues:
            e = embs[img["id"]].astype(np.float32)
            vecs.append(e / (np.linalg.norm(e) + 1e-9))
    if not vecs:
        raise ProjectError(f"{person['name']} の顔素材に、使える顔がありません。")
    m = np.mean(vecs, axis=0)
    return m / (np.linalg.norm(m) + 1e-9)


def _nearest(frames: list[int], target: int) -> int | None:
    if not frames:
        return None
    return min(frames, key=lambda n: abs(n - target))


# =====================================================================
# 指標の計算
# =====================================================================
def compute(src: dict[int, list[dict]], out: dict[int, list[dict]], assigns: list[dict[str, Any]],
            templates: dict[str, np.ndarray], settings: dict[str, Any], fps: float) -> dict[str, Any]:
    """解析結果から、フレームごとの指標、問題のフレーム、区間を計算する（純粋な計算だけ）。"""
    s = settings
    step = max(1, int(s["qc_step"]))
    frames = sorted(set(src) & set(out))
    pids = [a["person_id"] for a in assigns]
    implicit = len(assigns) == 1 and assigns[0].get("reference_frame") is None

    # 置き換える人の、元の動画での顔（基準の顔）
    refs: list[np.ndarray | None] = []
    for a in assigns:
        ref = None
        if a.get("reference_frame") is not None:
            n = _nearest([f for f in frames if src[f]], int(a["reference_frame"]))
            if n is not None:
                ordered = sort_by_order(src[n], a.get("face_selector_order") or "large-small")
                pos = int(a.get("reference_face_position") or 0)
                if pos < len(ordered):
                    ref = ordered[pos]["emb"]
        refs.append(ref)
    if not implicit and all(r is None for r in refs):
        implicit = len(assigns) == 1

    series = {pid: {"sim_set": [], "sim_orig": [], "flicker": [], "present_src": [], "present_out": []}
              for pid in pids}
    reasons_by_frame: dict[int, set[str]] = {}
    target_frames = 0
    wrong_swaps = []
    prev_sim: dict[str, tuple[int, float]] = {}
    for n in frames:
        S, O = src[n], out[n]
        m = match_faces(S, O)
        # 元の顔がどの人物か
        owner: dict[int, int | None] = {}
        if implicit:
            if S:
                big = max(range(len(S)), key=lambda i: _area(S[i]["bbox"]))
                owner = {i: (0 if i == big else -1) for i in range(len(S))}  # -1 は評価しない
        else:
            for i, f in enumerate(S):
                sims = [(-1.0 if r is None else float(f["emb"] @ r)) for r in refs]
                k = int(np.argmax(sims)) if sims else -1
                owner[i] = k if sims and sims[k] >= float(s["qc_track_match"]) else None
        reasons: set[str] = set()
        any_target = False
        for k, pid in enumerate(pids):
            cands = [i for i, o in owner.items() if o == k]
            ser = series[pid]
            if not cands:
                for key in ser:
                    ser[key].append(None if key not in ("present_src", "present_out") else False)
                prev_sim.pop(pid, None)
                continue
            any_target = True
            i = cands[0] if len(cands) == 1 else max(cands, key=lambda c: _area(S[c]["bbox"]))
            ser["present_src"].append(True)
            if i not in m:
                ser["present_out"].append(False)
                for key in ("sim_set", "sim_orig", "flicker"):
                    ser[key].append(None)
                reasons.add("miss")
                prev_sim.pop(pid, None)
                continue
            o = O[m[i]]
            ser["present_out"].append(True)
            sim_set = float(o["emb"] @ templates[pid])
            sim_orig = float(o["emb"] @ S[i]["emb"])
            ser["sim_set"].append(round(sim_set, 4))
            ser["sim_orig"].append(round(sim_orig, 4))
            fl = None
            if pid in prev_sim and n - prev_sim[pid][0] <= step:
                fl = abs(sim_set - prev_sim[pid][1])
            ser["flicker"].append(None if fl is None else round(fl, 4))
            prev_sim[pid] = (n, sim_set)
            if sim_set < float(s["qc_min_sim_faceset"]):
                reasons.add("low_sim_faceset")
            if sim_orig > float(s["qc_max_sim_original"]):
                reasons.add("high_sim_original")
            if fl is not None and fl > float(s["qc_max_flicker"]):
                reasons.add("flicker")
            if len(pids) > 1:
                others = [float(o["emb"] @ templates[q]) for q in pids if q != pid]
                if others and max(others) > sim_set + float(s["qc_mismatch_margin"]):
                    reasons.add("mismatch")
        # 置き換えてはいけない顔（割り当てのない人）が、AI 人物の顔になっていないか
        if not implicit:
            for i, k in owner.items():
                if k is None and i in m:
                    o = O[m[i]]
                    best = max(float(o["emb"] @ templates[q]) for q in pids)
                    if best >= float(s["qc_min_sim_faceset"]) and best > float(o["emb"] @ S[i]["emb"]):
                        reasons.add("wrong_swap")
                        wrong_swaps.append(n)
        if any_target:
            target_frames += 1
        if reasons:
            reasons_by_frame[n] = reasons

    # 再び現れたときの一貫性
    reappear_events = []
    win = max(1, int(round(float(s["qc_reappear_window_seconds"]) * fps / step)))
    gap_min = max(1, int(s["qc_reappear_gap_frames"]))
    for pid in pids:
        ser = series[pid]
        present = ser["present_src"]
        idx = 0
        while idx < len(frames):
            if present[idx]:
                idx += 1
                continue
            g0 = idx
            while idx < len(frames) and not present[idx]:
                idx += 1
            g1 = idx  # 消えていた区間は [g0, g1)
            gap_frames = frames[g1 - 1] - frames[g0] + step if g1 > g0 else 0
            if g0 == 0 or g1 >= len(frames) or gap_frames < gap_min:
                continue
            before = [v for v in ser["sim_set"][max(0, g0 - win):g0] if v is not None]
            after_idx = list(range(g1, min(len(frames), g1 + win)))
            after = [ser["sim_set"][j] for j in after_idx if ser["sim_set"][j] is not None]
            if not before or not after:
                continue
            delta = abs(float(np.mean(before)) - float(np.mean(after)))
            flagged = delta > float(s["qc_max_reappear_delta"])
            reappear_events.append({"person_id": pid, "gap_start": frames[g0], "gap_end": frames[g1 - 1],
                                    "before": round(float(np.mean(before)), 4),
                                    "after": round(float(np.mean(after)), 4),
                                    "delta": round(delta, 4), "flagged": flagged})
            if flagged:
                for j in after_idx:
                    reasons_by_frame.setdefault(frames[j], set()).add("reappear")

    problem = sorted(reasons_by_frame)
    intervals = []
    gap = step * (int(s["qc_merge_gap_frames"]) + 1)
    for n in problem:
        if intervals and n - intervals[-1]["last"] <= gap:
            iv = intervals[-1]
            iv["last"] = n
            iv["frames"].append(n)
        else:
            intervals.append({"first": n, "last": n, "frames": [n]})
    out_intervals = []
    for iv in intervals:
        cnt = Counter(r for n in iv["frames"] for r in reasons_by_frame[n])
        worst = _worst_frame(iv["frames"], frames, series, pids)
        out_intervals.append({
            "start_frame": iv["first"], "end_frame": min(iv["last"] + step - 1, frames[-1] + step - 1),
            "frames": len(iv["frames"]), "reasons": dict(cnt), "worst_frame": worst,
        })
    denom = target_frames
    ratio = (len(problem) / denom) if denom else None
    return {
        "frames": frames, "step": step, "persons": pids, "implicit": implicit,
        "series": series, "reasons_by_frame": {str(n): sorted(r) for n, r in reasons_by_frame.items()},
        "problem_frames": len(problem), "target_frames": target_frames, "ratio": ratio,
        "pass": None if ratio is None else ratio <= float(s["qc_pass_ratio"]),
        "intervals": out_intervals, "reappear_events": reappear_events,
        "wrong_swap_frames": sorted(set(wrong_swaps)),
    }


def _worst_frame(iv_frames: list[int], frames: list[int], series: dict, pids: list[str]) -> int:
    """区間の中で、顔素材との似ている度合いが最も低い（または顔がない）フレーム。"""
    pos = {n: i for i, n in enumerate(frames)}
    best_n, best_v = iv_frames[0], 9.0
    for n in iv_frames:
        for pid in pids:
            ser = series[pid]
            i = pos[n]
            if ser["present_src"][i] and not ser["present_out"][i]:
                return n
            v = ser["sim_set"][i]
            if v is not None and v < best_v:
                best_n, best_v = n, v
    return best_n


# =====================================================================
# 実行
# =====================================================================
def run_qc(project: Project, render_id: str, *, detect_fn: DetectFn | None = None,
           progress: Callable[[int, int, str], None] | None = None,
           should_stop: Callable[[], bool] | None = None) -> dict[str, Any]:
    from . import facefusion as ff
    from .media import MediaInfo, probe

    say = progress or (lambda *_a: None)
    render = project.render(render_id)
    clip = project.clip(render["clip_id"])
    s = project.settings
    assigns = render.get("assignments") or ff.clip_assignments(project, clip)
    templates = {a["person_id"]: person_template(project, a["person_id"]) for a in assigns}
    clip_path, out_path = project.abs(clip["file"]), project.abs(render["file"])
    if not out_path.exists():
        raise ProjectError(f"出力の動画がありません: {render['file']}")
    cmi = probe(clip_path, s, measure_luma=False)
    omi = probe(out_path, s, measure_luma=False)
    notes = []
    if omi.frame_count != cmi.frame_count:
        notes.append(f"出力のフレーム数（{omi.frame_count}）が元のクリップ（{cmi.frame_count}）と違います。"
                     "フレーム番号で並べて比べているので、ずれがあると結果が正しくありません。")
    if abs(omi.fps - cmi.fps) > 0.01:
        notes.append(f"出力のフレームレート（{omi.fps:.3f}）が元のクリップ（{cmi.fps:.3f}）と違います。")
    cache = project.root / "qc" / "cache"
    say(0, 0, "元のクリップの顔を解析しています")
    src = analyze_video(clip_path, s, detect_fn=detect_fn, cache_dir=cache, cache_tag="src",
                        progress=lambda i, n: say(i, n, "元のクリップの顔を解析しています"), should_stop=should_stop)
    say(0, 0, "出力の顔を解析しています")
    out = analyze_video(out_path, s, detect_fn=detect_fn, cache_dir=cache, cache_tag="out",
                        progress=lambda i, n: say(i, n, "出力の顔を解析しています"), should_stop=should_stop)
    res = compute(src, out, assigns, templates, s, cmi.fps)

    src_rec = project.source(clip["source_id"])
    src_fps = MediaInfo.from_dict(src_rec["media"]).fps
    for iv in res["intervals"]:
        iv["start_time"] = round(iv["start_frame"] / cmi.fps, 3)
        iv["end_time"] = round((iv["end_frame"] + 1) / cmi.fps, 3)
        iv["source_start_frame"] = clip["start_frame"] + iv["start_frame"]
        iv["source_end_frame"] = clip["start_frame"] + iv["end_frame"]
        iv["source_start_time"] = round(iv["source_start_frame"] / src_fps, 3)

    qid = new_id("qc")
    out_dir = project.root / "qc" / qid
    out_dir.mkdir(parents=True, exist_ok=True)
    thresholds = {k: v for k, v in s.items() if k.startswith("qc_")}
    metrics = {"id": qid, "render_id": render_id, "clip_id": clip["id"], "fps": cmi.fps, "notes": notes,
               "thresholds": thresholds, "assignments": assigns,
               "person_names": {pid: project.person(pid)["name"] for pid in templates}, **res}
    write_json_atomic(out_dir / "metrics.json", metrics)

    say(0, 0, "レポートを書いています")
    from .report import write_report

    thumbs = _write_thumbs(project, clip_path, out_path, cmi, omi, res["intervals"], out_dir)
    report = write_report(project, metrics, render, clip, out_dir, thumbs)
    rec = {"id": qid, "render_id": render_id, "clip_id": clip["id"], "created_at": now_iso(),
           "pass": res["pass"], "ratio": res["ratio"], "problem_frames": res["problem_frames"],
           "target_frames": res["target_frames"], "intervals": len(res["intervals"]), "notes": notes,
           "report": project.rel(report), "metrics": project.rel(out_dir / "metrics.json"),
           "thresholds": thresholds, "visual_checks": {}, "judged_good": False}
    project.data.setdefault("qc_reports", []).append(rec)
    project.log("qc_report", {"id": qid, "render_id": render_id, "pass": res["pass"], "ratio": res["ratio"]})
    return rec


def _write_thumbs(project: Project, clip_path: Path, out_path: Path, cmi, omi, intervals: list[dict],
                  out_dir: Path, limit: int = 60) -> dict[int, str]:
    """問題区間ごとに、いちばん悪いフレームの「元 | 出力」の画像を作る。"""
    import cv2

    from .frames import imwrite_unicode, read_frame

    d = out_dir / "thumbs"
    d.mkdir(exist_ok=True)
    res = {}
    for iv in intervals[:limit]:
        n = iv["worst_frame"]
        a = read_frame(clip_path, n, cmi.fps, cmi.display_width, cmi.display_height, max_width=640)
        b = read_frame(out_path, n, omi.fps, omi.display_width, omi.display_height, max_width=640)
        if a is None or b is None:
            continue
        if b.shape != a.shape:
            b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
        img = np.hstack([a, np.full((a.shape[0], 4, 3), 40, np.uint8), b])
        f = d / f"f{n:06d}.jpg"
        if imwrite_unicode(f, img):
            res[n] = f"thumbs/{f.name}"
    return res


def load_metrics(project: Project, qc_id: str) -> dict[str, Any]:
    rec = report_record(project, qc_id)
    return read_json(project.abs(rec["metrics"]))


def report_record(project: Project, qc_id: str) -> dict[str, Any]:
    for r in project.data.get("qc_reports", []):
        if r["id"] == qc_id:
            return r
    raise ProjectError(f"品質チェックの結果が見つかりません: {qc_id}")


def set_visual_check(project: Project, qc_id: str, key: str, ok: bool | None, note: str = "") -> None:
    rec = report_record(project, qc_id)
    rec.setdefault("visual_checks", {})[key] = {"ok": ok, "note": note, "at": now_iso()}
    project.log("qc_visual_check", {"id": qc_id, "key": key, "ok": ok})


# =====================================================================
# 基準値の決め方（仕様 M6：ユーザーが「良い」と判断した出力の分布から初期値を決める）
# =====================================================================
LOWER_BOUND = {"qc_min_sim_faceset": "sim_set"}
UPPER_BOUND = {"qc_max_sim_original": "sim_orig", "qc_max_flicker": "flicker"}


def propose_thresholds(project: Project, qc_ids: list[str]) -> dict[str, dict[str, Any]]:
    """「良い」出力の品質チェック結果から、基準値の案を作る。"""
    s = project.settings
    p = float(s["qc_calibration_percentile"])
    margin = float(s["qc_calibration_margin"])
    pool: dict[str, list[float]] = {"sim_set": [], "sim_orig": [], "flicker": [], "reappear": []}
    for qid in qc_ids:
        m = load_metrics(project, qid)
        for ser in m["series"].values():
            for key in ("sim_set", "sim_orig", "flicker"):
                pool[key] += [v for v in ser[key] if v is not None]
        pool["reappear"] += [e["delta"] for e in m.get("reappear_events", [])]
    out = {}
    for key, metric in LOWER_BOUND.items():
        vals = pool[metric]
        if vals:
            out[key] = {"current": s[key], "proposed": round(float(np.percentile(vals, p)) - margin, 3),
                        "samples": len(vals)}
    for key, metric in UPPER_BOUND.items():
        vals = pool[metric]
        if vals:
            out[key] = {"current": s[key], "proposed": round(float(np.percentile(vals, 100 - p)) + margin, 3),
                        "samples": len(vals)}
    if pool["reappear"]:
        out["qc_max_reappear_delta"] = {"current": s["qc_max_reappear_delta"],
                                        "proposed": round(max(pool["reappear"]) + margin, 3),
                                        "samples": len(pool["reappear"])}
    return out


def apply_thresholds(project: Project, proposals: dict[str, dict[str, Any]], qc_ids: list[str]) -> None:
    for key, d in proposals.items():
        project.data["settings"][key] = d["proposed"]
    for qid in qc_ids:
        report_record(project, qid)["judged_good"] = True
    project.data.setdefault("qc_calibrations", []).append(
        {"at": now_iso(), "from_reports": qc_ids, "values": {k: d["proposed"] for k, d in proposals.items()}})
    project.log("qc_thresholds_calibrated", {"from": qc_ids, "values": {k: d["proposed"] for k, d in proposals.items()}})


def reasons_text(reasons: dict[str, int] | list[str]) -> str:
    keys = reasons if isinstance(reasons, list) else sorted(reasons, key=lambda k: -reasons[k])
    return "、".join(REASONS.get(k, k) for k in keys)

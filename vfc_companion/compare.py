"""M5: 比較動画と比較画像の作成（仕様 11.3 M5、12 章）。

同じテスト用クリップに対する 2〜4 本の結果を、ラベル付きで左右（4 本なら 2x2）に並べる。
フレームは番号でそろえる（各動画の n 番目のフレーム同士が並ぶ）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import numpy as np

from . import facefusion as ff
from .frames import imwrite_unicode, read_frame
from .media import probe
from .project import Project, ProjectError, new_id
from .util import find_tool, now_iso, run

LABEL_H = 44
MAX_PANELS = 4
# 仕様 S5：AI で改変した動画であることをメタデータに残す（比較動画にも付けておく）
AI_NOTE = "AI-modified video (face replacement comparison)"


# =====================================================================
# ラベル
# =====================================================================
def label_image(text: str, width: int, height: int = LABEL_H) -> np.ndarray:
    """日本語も描けるラベルの帯（OpenCV 5 の FontFace を使う。なければ英数字だけの描画）。"""
    import cv2

    img = np.full((height, width, 3), 24, np.uint8)
    size = int(height * 0.55)
    try:
        face = cv2.FontFace("sans")
        cv2.putText(img, text, (12, int(height * 0.72)), (235, 235, 235), fface=face, size=size, weight=500)
    except (AttributeError, TypeError, cv2.error):
        safe = text.encode("ascii", "replace").decode()
        cv2.putText(img, safe, (12, int(height * 0.7)), cv2.FONT_HERSHEY_SIMPLEX, height / 60, (235, 235, 235), 1,
                    cv2.LINE_AA)
    return img


# =====================================================================
# 並べる動画の決定
# =====================================================================
def _panel(project: Project, label: str, path: Path, render_id: str | None) -> dict[str, Any]:
    return {"label": label, "file": project.rel(path), "render_id": render_id}


def choose_panels(project: Project, clip_id: str, render_ids: list[str] | None = None,
                  include_original: bool = True) -> list[dict[str, Any]]:
    """並べる動画を決める。結果が 4 本以上なら結果だけを 4 本、3 本以下なら元の動画も並べる。"""
    clip = project.clip(clip_id)
    if render_ids is None:
        renders = ff.renders_for_clip(project, clip_id)
        # FaceFusion は設定（variant）ごとに最新の 1 本、VisoMaster Fusion はすべて
        latest: dict[str, dict] = {}
        others = []
        for r in renders:
            if r["tool"] == "facefusion":
                latest[r["variant"]["name"]] = r
            else:
                others.append(r)
        chosen = list(latest.values()) + others
    else:
        chosen = [project.render(rid) for rid in render_ids]
    if not chosen:
        raise ProjectError("並べる結果がありません。FaceFusion を実行するか、VisoMaster Fusion の出力を "
                           "renders/visomaster/ に保存してください。")
    panels = [_panel(project, r.get("label") or r["id"], project.abs(r["file"]), r["id"]) for r in chosen]
    if include_original and len(panels) < MAX_PANELS:
        panels.insert(0, _panel(project, "元の動画", project.abs(clip["file"]), None))
    return panels[:MAX_PANELS]


# =====================================================================
# 比較動画
# =====================================================================
def _panel_size(project: Project, clip: dict[str, Any]) -> tuple[int, int]:
    mi = probe(project.abs(clip["file"]), project.data.get("settings"), measure_luma=False)
    h = min(int(project.settings["compare_panel_height"]), mi.display_height)
    w = int(round(mi.display_width * h / mi.display_height))
    return w - (w % 2), h - (h % 2)


def build_video_cmd(inputs: list[Path], labels: list[Path], out: Path, w: int, h: int, fps_num: int, fps_den: int,
                    frames: int) -> list[str]:
    n = len(inputs)
    cmd = [find_tool("ffmpeg"), "-hide_banner", "-y"]
    for p in inputs:
        cmd += ["-i", str(p)]
    for p in labels:
        cmd += ["-loop", "1", "-i", str(p)]
    parts = []
    for i in range(n):
        # n 番目のフレームの時刻を n/fps にそろえる（元の時刻のずれを無視して番号で並べる）
        parts.append(f"[{i}:v:0]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                     f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,"
                     f"setpts=N*{fps_den}/{fps_num}/TB,format=yuv420p[v{i}]")
        parts.append(f"[{n + i}:v:0]setsar=1,setpts=N*{fps_den}/{fps_num}/TB,format=yuv420p[l{i}]")
        parts.append(f"[l{i}][v{i}]vstack=inputs=2:shortest=1[p{i}]")
    pins = "".join(f"[p{i}]" for i in range(n))
    if n == 1:
        parts.append("[p0]null[out]")
    elif n == 4:
        parts.append(f"{pins}xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0:shortest=1[out]")
    else:
        parts.append(f"{pins}hstack=inputs={n}:shortest=1[out]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[out]", "-frames:v", str(frames),
            "-r", f"{fps_num}/{fps_den}", "-c:v", "libx264", "-preset", "medium", "-crf", "14",
            "-pix_fmt", "yuv420p", "-an", "-metadata", f"comment={AI_NOTE}", str(out)]
    return cmd


def make_video(project: Project, clip_id: str, panels: list[dict[str, Any]], out: Path) -> list[str]:
    from fractions import Fraction

    from .media import count_frames

    clip = project.clip(clip_id)
    w, h = _panel_size(project, clip)
    fr = Fraction(clip.get("fps_fraction") or str(clip["fps"])).limit_denominator(1001000)
    counts = []
    for p in panels:
        c = count_frames(project.abs(p["file"]))
        counts.append(c if c else clip["frame_count"])
    frames = min(counts)
    work = out.parent / "labels"
    work.mkdir(parents=True, exist_ok=True)
    label_files = []
    for i, p in enumerate(panels):
        lf = work / f"label_{i}.png"
        imwrite_unicode(lf, label_image(p["label"], w))
        label_files.append(lf)
    cmd = build_video_cmd([project.abs(p["file"]) for p in panels], label_files, out, w, h, fr.numerator,
                          fr.denominator, frames)
    run(cmd)
    return cmd


# =====================================================================
# 比較画像（同じフレームを並べる）
# =====================================================================
def default_frames(frame_count: int, count: int) -> list[int]:
    count = max(1, min(count, frame_count))
    return sorted({int(round((i + 0.5) * frame_count / count)) for i in range(count)})


def make_image(project: Project, panels: list[dict[str, Any]], frame: int, out: Path, h: int) -> bool:
    import cv2

    tiles = []
    for p in panels:
        path = project.abs(p["file"])
        mi = probe(path, project.data.get("settings"), measure_luma=False)
        img = read_frame(path, frame, mi.fps, mi.display_width, mi.display_height)
        if img is None:
            img = np.zeros((h, int(h * 16 / 9), 3), np.uint8)
        w = int(round(img.shape[1] * h / img.shape[0]))
        img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
        tiles.append(np.vstack([label_image(f"{p['label']}  #{frame}", w), img]))
    th = max(t.shape[0] for t in tiles)
    tiles = [t if t.shape[0] == th else np.vstack([t, np.zeros((th - t.shape[0], t.shape[1], 3), np.uint8)])
             for t in tiles]
    if len(tiles) == 4:
        wmax = max(t.shape[1] for t in tiles)
        tiles = [np.hstack([t, np.zeros((th, wmax - t.shape[1], 3), np.uint8)]) if t.shape[1] < wmax else t
                 for t in tiles]
        grid = np.vstack([np.hstack(tiles[:2]), np.hstack(tiles[2:])])
    else:
        grid = np.hstack(tiles)
    return imwrite_unicode(out, grid)


def make_comparison(project: Project, clip_id: str, *, render_ids: list[str] | None = None,
                    include_original: bool = True, frames: list[int] | None = None,
                    progress: Callable[[int, int, str], None] | None = None) -> dict[str, Any]:
    say = progress or (lambda *_a: None)
    clip = project.clip(clip_id)
    panels = choose_panels(project, clip_id, render_ids, include_original)
    cid = new_id("cmp")
    out_dir = project.root / "compare" / cid
    out_dir.mkdir(parents=True, exist_ok=True)
    video = out_dir / f"{cid}_{clip_id}.mp4"
    say(0, 0, "比較動画を作っています")
    cmd = make_video(project, clip_id, panels, video)
    frames = frames or default_frames(clip["frame_count"], int(project.settings["compare_image_count"]))
    _w, h = _panel_size(project, clip)
    images = []
    for i, f in enumerate(frames):
        say(i, len(frames), f"比較画像を書き出しています（フレーム {f}）")
        out = out_dir / f"frame_{f:06d}.png"
        if make_image(project, panels, f, out, min(h, 720)):
            images.append(project.rel(out))
    rec = {"id": cid, "clip_id": clip_id, "panels": panels, "video": project.rel(video), "images": images,
           "frames": frames, "created_at": now_iso(), "command": cmd[1:]}
    project.data.setdefault("comparisons", []).append(rec)
    project.log("comparison_made", {"id": cid, "clip_id": clip_id, "panels": [p["label"] for p in panels]})
    return rec


# =====================================================================
# ボタン 1 つで：FaceFusion の実行 → VisoMaster Fusion の出力探し → 比較動画（M5 の完了条件）
# =====================================================================
def variant_signature(project: Project, clip_id: str, variant: dict[str, str]) -> str:
    s = project.settings
    clip = project.clip(clip_id)
    ff_settings = {k: v for k, v in s.items() if k.startswith("ff_")}
    persons = sorted({a["person_id"] for a in (clip.get("assignments") or [])}) or \
        [p["id"] for p in project.data["persons"]]
    imgs = {pid: sorted(i["sha256"] for i in project.person(pid)["images"]) for pid in persons}
    blob = json.dumps({"v": variant, "s": ff_settings, "a": clip.get("assignments") or [], "i": imgs,
                       "clip": clip["file"]}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def run_one_button(project: Project, clip_id: str, *, run_facefusion: bool = True,
                   progress: Callable[[int, int, str], None] | None = None,
                   should_stop: Callable[[], bool] | None = None) -> dict[str, Any]:
    say = progress or (lambda *_a: None)
    clip = project.clip(clip_id)
    result: dict[str, Any] = {"facefusion": [], "skipped": [], "comparison": None}
    if run_facefusion:
        if clip.get("facefusion_blocked"):
            result["skipped"].append("このクリップは FaceFusion のコンテンツ判定で止まったため、FaceFusion の比較から"
                                     "外しています（仕様 S4）。")
        else:
            say(0, 0, "FaceFusion を確かめています")
            inst = ff.locate(project.settings)
            info = ff.probe(inst, project.root / ff.HELP_CACHE)
            for variant in ff.default_variants(project.settings):
                if should_stop and should_stop():
                    break
                sig = variant_signature(project, clip_id, variant)
                done = [r for r in ff.renders_for_clip(project, clip_id)
                        if r["tool"] == "facefusion" and r.get("signature") == sig]
                if done:
                    result["skipped"].append(f"{variant['face_swapper_model']}：同じ設定の結果があるので再利用しました")
                    continue
                rec = ff.run_variant(project, clip_id, variant, info=info, inst=inst, progress=progress,
                                     should_stop=should_stop)
                rec["signature"] = sig
                project.save()
                result["facefusion"].append(rec)
                if rec["status"] == ff.STATUS_BLOCKED:
                    result["skipped"].append(rec["error"])
                    break
                if rec["status"] != ff.STATUS_DONE:
                    result["skipped"].append(f"{variant['face_swapper_model']}：{ff.STATUS_TEXT[rec['status']]}")
    if should_stop and should_stop():
        return result
    say(0, 0, "VisoMaster Fusion の出力を探しています")
    ff.sync_visomaster(project, clip_id)
    usable = ff.renders_for_clip(project, clip_id)
    if clip.get("facefusion_blocked"):
        usable = [r for r in usable if r["tool"] != "facefusion"]
    if not usable:
        project.save()
        raise ProjectError("並べる結果がありません。" + " ".join(result["skipped"]))
    ids = None
    if clip.get("facefusion_blocked"):
        ids = [r["id"] for r in usable]
    result["comparison"] = make_comparison(project, clip_id, render_ids=ids, progress=progress)
    return result

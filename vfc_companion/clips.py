"""M3: テスト用クリップの切り出し（仕様 11.3 M3、9.3）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .frames import iter_frames, seek_arg
from .media import MediaInfo
from .project import Project, ProjectError, new_id
from .util import find_tool, now_iso, read_json, run, unique_path, write_json_atomic

# 中間ファイルの形式。VisoMaster Fusion の読み込み側は拡張子 .mp4/.mov/.mkv などを受け付ける
# （ソースの video_extensions で確認済み）。実際に読めるかは C10 の実機確認が必要【未確認】。
CLIP_FORMATS: dict[str, dict[str, Any]] = {
    "h264_lossless": {
        "label": "H.264 ロスレス（.mp4）",
        "ext": ".mp4",
        "video": ["-c:v", "libx264", "-preset", "slow", "-qp", "0"],
        "audio": ["-c:a", "aac", "-b:a", "320k"],
    },
    "ffv1": {
        "label": "FFV1 ロスレス（.mkv）",
        "ext": ".mkv",
        "video": ["-c:v", "ffv1", "-level", "3", "-g", "1", "-slices", "16", "-slicecrc", "1"],
        "audio": ["-c:a", "pcm_s16le"],
    },
    "prores_hq": {
        "label": "ProRes 422 HQ（.mov）",
        "ext": ".mov",
        "video": ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le", "-vendor", "apl0"],
        "audio": ["-c:a", "pcm_s16le"],
    },
    "h264_high": {
        "label": "H.264 高画質（.mp4、CRF 12）",
        "ext": ".mp4",
        "video": ["-c:v", "libx264", "-preset", "slow", "-crf", "12"],
        "audio": ["-c:a", "aac", "-b:a", "320k"],
    },
}


def _pix_fmt_args(fmt_key: str, info: MediaInfo) -> list[str]:
    if fmt_key == "prores_hq":
        return []
    pf = info.pix_fmt or "yuv420p"
    if fmt_key == "ffv1":
        return ["-pix_fmt", pf] if pf.startswith(("yuv", "gbr", "gray")) else ["-pix_fmt", "yuv420p"]
    # H.264 は元の色の形式のまま（4:2:0 8bit なら yuv420p）
    allowed = {"yuv420p", "yuvj420p", "yuv422p", "yuv444p", "yuv420p10le", "yuv422p10le", "yuv444p10le"}
    return ["-pix_fmt", pf if pf in allowed else "yuv420p"]


def build_cut_cmd(src: Path, dest: Path, info: MediaInfo, start_frame: int, end_frame: int,
                  fmt_key: str) -> list[str]:
    """[start_frame, end_frame) を切り出すコマンド。再エンコードするのでフレーム単位で正確。"""
    fmt = CLIP_FORMATS[fmt_key]
    n = end_frame - start_frame
    cmd = [find_tool("ffmpeg"), "-hide_banner", "-y", "-ss", seek_arg(start_frame, info.fps), "-i", str(src),
           "-map", "0:v:0", "-frames:v", str(n), *fmt["video"], *_pix_fmt_args(fmt_key, info)]
    if info.has_audio:
        cmd += ["-map", "0:a:0?", "-t", f"{n / info.fps:.6f}", *fmt["audio"]]
    # 色の情報を引き継ぐ
    for opt, val in (("-color_primaries", info.color_primaries), ("-color_trc", info.color_transfer),
                     ("-colorspace", info.color_space)):
        if val and val != "unknown":
            cmd += [opt, val]
    cmd += ["-map_metadata", "-1", str(dest)]
    return cmd


def cut_clip(project: Project, source_id: str, start_frame: int, end_frame: int, *,
             fmt_key: str | None = None, note: str = "", tags: list[str] | None = None) -> dict[str, Any]:
    src_rec = project.source(source_id)
    info = MediaInfo.from_dict(src_rec["media"])
    start_frame, end_frame = int(start_frame), int(end_frame)
    if not (0 <= start_frame < end_frame <= info.frame_count):
        raise ProjectError(f"範囲が正しくありません（0 ≤ 開始 < 終了 ≤ {info.frame_count}）: {start_frame}〜{end_frame}")
    fmt_key = fmt_key or project.settings["clip_format"]
    if fmt_key not in CLIP_FORMATS:
        raise ProjectError(f"未知の形式です: {fmt_key}")
    src = project.abs(src_rec["file"])
    dest = unique_path(project.root / "clips" /
                       f"{src.stem}_f{start_frame:06d}-{end_frame:06d}{CLIP_FORMATS[fmt_key]['ext']}")
    cmd = build_cut_cmd(src, dest, info, start_frame, end_frame, fmt_key)
    run(cmd)
    # 実際のフレーム数を確かめる
    from .media import count_frames

    actual = count_frames(dest)
    rec = {
        "id": new_id("clip"),
        "source_id": source_id,
        "file": project.rel(dest),
        "format": fmt_key,
        "start_frame": start_frame,
        "end_frame": end_frame,           # この番号のフレームは含まない
        "frame_count": end_frame - start_frame,
        "actual_frame_count": actual,
        "fps": info.fps,
        "fps_fraction": info.fps_fraction,
        "start_time": round(start_frame / info.fps, 6),
        "end_time": round(end_frame / info.fps, 6),
        "note": note,
        "tags": tags or [],
        "created_at": now_iso(),
        "command": cmd[1:],
    }
    if actual is not None and actual != rec["frame_count"]:
        rec["warning"] = f"切り出したフレーム数が {actual} で、指定（{rec['frame_count']}）と違います"
    project.data["clips"].append(rec)
    project.log("clip_cut", {"id": rec["id"], "source_id": source_id, "start_frame": start_frame,
                             "end_frame": end_frame, "format": fmt_key})
    return rec


# ---------------- 顔の区間の候補（9.3 の条件を満たすクリップを選びやすくする） ----------------

EVENT_LABELS = {
    "no_face": "顔が見えない",
    "reappear": "顔が消えて再び現れる",
    "tilted": "顔が大きく傾く",
    "small": "顔が小さい（引きの画）",
    "multi": "複数人が映る",
}


def scan_path(project: Project, source_id: str) -> Path:
    return project.root / "qc" / f"face_scan_{source_id}.json"


def scan_face_events(project: Project, source_id: str, *,
                     progress: Callable[[int, int], None] | None = None,
                     should_stop: Callable[[], bool] | None = None) -> dict[str, Any]:
    from .faces import detect

    s = project.settings
    src_rec = project.source(source_id)
    info = MediaInfo.from_dict(src_rec["media"])
    step = max(1, int(round(info.fps * float(s["scan_step_seconds"]))))
    total = (info.frame_count + step - 1) // step
    samples = []
    for i, (frame_no, img) in enumerate(iter_frames(project.abs(src_rec["file"]), info.fps,
                                                    info.display_width, info.display_height,
                                                    step=step, max_width=int(s["scan_width"]))):
        if should_stop and should_stop():
            break
        faces = detect(img, 0.5)
        short = min(img.shape[0], img.shape[1])
        if faces:
            f = faces[0]
            samples.append({"f": frame_no, "n": len(faces), "yaw": round(f.yaw, 1), "pitch": round(f.pitch, 1),
                            "roll": round(f.roll, 1), "ratio": round(f.height / short, 4)})
        else:
            samples.append({"f": frame_no, "n": 0})
        if progress and (i % 5 == 0):
            progress(i + 1, total)
    if progress:
        progress(total, total)

    def flags(smp: dict) -> set[str]:
        out = set()
        if smp["n"] == 0:
            out.add("no_face")
            return out
        if smp["n"] > 1:
            out.add("multi")
        if (abs(smp["yaw"]) > s["event_yaw_deg"] or abs(smp["pitch"]) > s["event_pitch_deg"]
                or abs(smp["roll"]) > s["event_roll_deg"]):
            out.add("tilted")
        if smp["ratio"] < s["event_small_face_ratio"]:
            out.add("small")
        return out

    segments = []
    for kind in ("no_face", "tilted", "small", "multi"):
        run_start = None
        for idx, smp in enumerate(samples):
            on = kind in flags(smp)
            if on and run_start is None:
                run_start = idx
            if (not on or idx == len(samples) - 1) and run_start is not None:
                last = idx if on else idx - 1
                start_f = samples[run_start]["f"]
                end_f = min(info.frame_count, samples[last]["f"] + step)
                if (end_f - start_f) / info.fps >= float(s["event_min_duration"]):
                    label = kind
                    # 前後に顔がある「顔が見えない」区間は、回転などで消えて再び現れる場面
                    if kind == "no_face" and run_start > 0 and (last + 1) < len(samples):
                        label = "reappear"
                    segments.append({"kind": label, "label": EVENT_LABELS[label],
                                     "start_frame": int(start_f), "end_frame": int(end_f),
                                     "start_time": round(start_f / info.fps, 3),
                                     "end_time": round(end_f / info.fps, 3)})
                run_start = None
    segments.sort(key=lambda x: x["start_frame"])
    result = {
        "source_id": source_id,
        "scanned_at": now_iso(),
        "step_frames": step,
        "complete": len(samples) >= total,
        "thresholds": {k: s[k] for k in ("event_yaw_deg", "event_pitch_deg", "event_roll_deg",
                                         "event_small_face_ratio", "event_min_duration")},
        "segments": segments,
        "samples": samples,
    }
    write_json_atomic(scan_path(project, source_id), result)
    project.log("face_scan", {"source_id": source_id, "segments": len(segments), "complete": result["complete"]})
    return result


def load_scan(project: Project, source_id: str) -> dict[str, Any] | None:
    return read_json(scan_path(project, source_id))

"""M2: 素材の取り込みと検査（仕様 11.3 M2、9.1）。"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .consent import KIND_SOURCE_VIDEO, STATEMENT_SOURCE_VIDEO, ConsentRequired
from .project import Project, new_id
from .util import find_tool, now_iso, run, sha256_file, unique_path

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".mts", ".m2ts", ".mxf", ".wmv"}
HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}  # PQ と HLG


@dataclass
class MediaInfo:
    path: str
    width: int
    height: int
    codec: str
    pix_fmt: str
    bit_depth: int | None
    fps: float                       # 平均フレームレート
    fps_fraction: str                # 例 "60000/1001"
    r_frame_rate: str
    duration: float
    frame_count: int
    frame_count_source: str          # "nb_frames" / "count_packets" / "estimated"
    is_vfr: bool
    vfr_detail: str
    color_transfer: str | None
    color_primaries: str | None
    color_space: str | None
    is_hdr: bool
    has_audio: bool
    audio_codec: str | None
    audio_sample_rate: int | None
    audio_channels: int | None
    rotation: int
    timecode: str | None
    mean_luma: float | None = None
    format_name: str = ""
    size_bytes: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def display_width(self) -> int:
        return self.height if abs(self.rotation) in (90, 270) else self.width

    @property
    def display_height(self) -> int:
        return self.width if abs(self.rotation) in (90, 270) else self.height

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MediaInfo":
        names = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in names})


@dataclass
class Warning_:
    code: str
    message: str
    suggestion: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _frac(s: str | None) -> Fraction | None:
    if not s or s in ("0/0", "N/A"):
        return None
    try:
        f = Fraction(s)
        return f if f > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


def _ffprobe_json(path: Path, *args: str) -> dict:
    out = run([find_tool("ffprobe"), "-v", "error", "-of", "json", *args, str(path)], timeout=600)
    return json.loads(out.stdout.decode("utf-8", "replace") or "{}")


def _rotation(stream: dict) -> int:
    tags = stream.get("tags", {}) or {}
    if "rotate" in tags:
        try:
            return int(tags["rotate"])
        except ValueError:
            pass
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            try:
                return int(sd["rotation"])
            except (TypeError, ValueError):
                pass
    return 0


def _bit_depth(stream: dict) -> int | None:
    v = stream.get("bits_per_raw_sample")
    if v and str(v).isdigit():
        return int(v)
    pf = stream.get("pix_fmt", "") or ""
    for d in (16, 12, 10):
        if f"p{d}" in pf:
            return d
    return 8 if pf else None


def detect_vfr(path: Path, scan_frames: int, tolerance: float, bad_ratio: float) -> tuple[bool, str]:
    """先頭のフレームの表示時刻を調べ、間隔がそろっているかを見る。"""
    data = _ffprobe_json(
        path, "-select_streams", "v:0", "-read_intervals", f"%+#{scan_frames}",
        "-show_entries", "packet=pts_time,dts_time,flags",
    )
    times = []
    for p in data.get("packets", []):
        t = p.get("pts_time")
        if t in (None, "N/A"):
            t = p.get("dts_time")
        if t not in (None, "N/A"):
            times.append(float(t))
    if len(times) < 3:
        return False, "フレームの時刻を読めなかったため判定していません"
    ts = np.sort(np.array(times))
    d = np.diff(ts)
    d = d[d > 0]
    if len(d) < 2:
        return False, "判定に必要なフレーム数がありません"
    med = float(np.median(d))
    bad = np.abs(d - med) > tolerance * med
    ratio = float(bad.mean())
    detail = f"先頭 {len(ts)} フレームのうち、間隔がそろっていないもの {ratio * 100:.2f}%（中央値 {1 / med:.3f}fps）"
    return ratio > bad_ratio, detail


def count_frames(path: Path) -> int | None:
    data = _ffprobe_json(path, "-select_streams", "v:0", "-count_packets", "-show_entries", "stream=nb_read_packets")
    try:
        return int(data["streams"][0]["nb_read_packets"])
    except (KeyError, IndexError, ValueError, TypeError):
        return None


def mean_luma(path: Path, duration: float, samples: int = 8) -> float | None:
    """動画全体から数枚を取り出して、平均の明るさ（0-255）を返す。"""
    if duration <= 0:
        return None
    ffmpeg = find_tool("ffmpeg")
    vals = []
    for i in range(samples):
        t = duration * (i + 0.5) / samples
        proc = run([ffmpeg, "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                    "-vf", "scale=160:-2,format=gray", "-f", "rawvideo", "-"], check=False, timeout=120)
        if proc.returncode == 0 and proc.stdout:
            vals.append(np.frombuffer(proc.stdout, dtype=np.uint8).mean())
    return float(np.mean(vals)) if vals else None


def probe(path: str | Path, settings: dict[str, Any] | None = None, *, measure_luma: bool = True) -> MediaInfo:
    from .config import merged_settings

    s = merged_settings(settings)
    path = Path(path)
    data = _ffprobe_json(path, "-show_format", "-show_streams")
    streams = data.get("streams", [])
    v = next((x for x in streams if x.get("codec_type") == "video"
              and not (x.get("disposition", {}) or {}).get("attached_pic")), None)
    if v is None:
        raise ValueError(f"映像が含まれていません: {path}")
    a = next((x for x in streams if x.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})

    avg = _frac(v.get("avg_frame_rate")) or _frac(v.get("r_frame_rate")) or Fraction(30)
    rfr = v.get("r_frame_rate", "")
    duration = float(v.get("duration") or fmt.get("duration") or 0.0)

    nb = v.get("nb_frames")
    if nb and str(nb).isdigit() and int(nb) > 0:
        frames, src = int(nb), "nb_frames"
    else:
        counted = count_frames(path)
        if counted:
            frames, src = counted, "count_packets"
        else:
            frames, src = int(round(duration * float(avg))), "estimated"

    is_vfr, vfr_detail = detect_vfr(path, int(s["vfr_scan_frames"]), float(s["vfr_tolerance"]),
                                    float(s["vfr_bad_ratio"]))
    r = _frac(rfr)
    if r and abs(float(r) - float(avg)) / float(avg) > 0.005:
        vfr_detail += f"。名目のフレームレート {float(r):.3f} と平均 {float(avg):.3f} が違います"

    trc = v.get("color_transfer")
    tc = (v.get("tags", {}) or {}).get("timecode") or (fmt.get("tags", {}) or {}).get("timecode")
    info = MediaInfo(
        path=str(path),
        width=int(v.get("width", 0)),
        height=int(v.get("height", 0)),
        codec=v.get("codec_name", ""),
        pix_fmt=v.get("pix_fmt", ""),
        bit_depth=_bit_depth(v),
        fps=float(avg),
        fps_fraction=f"{avg.numerator}/{avg.denominator}",
        r_frame_rate=rfr,
        duration=duration,
        frame_count=frames,
        frame_count_source=src,
        is_vfr=is_vfr,
        vfr_detail=vfr_detail,
        color_transfer=trc,
        color_primaries=v.get("color_primaries"),
        color_space=v.get("color_space"),
        is_hdr=trc in HDR_TRANSFERS,
        has_audio=a is not None,
        audio_codec=a.get("codec_name") if a else None,
        audio_sample_rate=int(a["sample_rate"]) if a and a.get("sample_rate") else None,
        audio_channels=a.get("channels") if a else None,
        rotation=_rotation(v),
        timecode=tc,
        format_name=fmt.get("format_name", ""),
        size_bytes=int(fmt.get("size") or path.stat().st_size),
    )
    if measure_luma:
        info.mean_luma = mean_luma(path, duration)
    return info


def check_guide(info: MediaInfo, settings: dict[str, Any] | None = None) -> list[Warning_]:
    """仕様 9.1 の撮影ガイドと、取り込み時の注意点に照らして警告を返す。"""
    from .config import merged_settings

    s = merged_settings(settings)
    w: list[Warning_] = []
    short = min(info.display_width, info.display_height)
    if short < s["guide_min_short_side"]:
        w.append(Warning_("resolution", f"4K 未満です（{info.display_width}x{info.display_height}）。"
                          "全身が映る動画では特に 4K を推奨します。", "可能なら 4K で撮り直してください。"))
    if info.fps <= s["guide_min_fps"] + 0.01:
        w.append(Warning_("fps", f"フレームレートが {info.fps:.2f}fps です（30fps 以下）。60fps を推奨します。",
                          "速い動きの動画は 60fps で撮り直すと、顔のブレと崩れが減ります。"))
    if info.is_vfr:
        w.append(Warning_("vfr", f"可変フレームレートの可能性があります。{info.vfr_detail}",
                          "音ずれを防ぐため、固定フレームレートに変換してから使ってください（「CFRに変換」）。"))
    if info.is_hdr:
        w.append(Warning_("hdr", f"HDR 動画です（{info.color_transfer} / {info.color_primaries}）。"
                          "VisoMaster Fusion は入力を 8bit で読み込むとみられ、HDR を正しく扱えるかは未確認です（C9）。",
                          "SDR に変換したものを使うことを提案します（「SDRに変換」）。"))
    elif (info.bit_depth or 8) > 8:
        w.append(Warning_("bit_depth", f"{info.bit_depth}bit の動画です。VisoMaster Fusion は 8bit で読み込むとみられます【推測】。",
                          "仕上げ（DaVinci Resolve）では元の動画を使うと階調を保てます。"))
    if info.mean_luma is not None and info.mean_luma < s["guide_dark_luma"]:
        w.append(Warning_("dark", f"全体が暗めです（平均の明るさ {info.mean_luma:.0f}/255）。",
                          "顔に均一に光を当てて撮ると、顔交換が安定します。"))
    if info.rotation:
        w.append(Warning_("rotation", f"回転情報（{info.rotation}度）が付いています。",
                          "ツールによって向きが変わることがあります。テスト用クリップの向きを確認してください。"))
    if not info.has_audio:
        w.append(Warning_("no_audio", "音声がありません。", ""))
    return w


# ---------------- 取り込み ----------------

def import_source(project: Project, path: str | Path, *, consent_confirmed: bool, copy: bool = True,
                  note: str = "", progress: Callable[[str], None] | None = None,
                  info: MediaInfo | None = None) -> dict[str, Any]:
    """動画をプロジェクトに取り込む。S2 の確認がないと取り込まない。"""
    if not consent_confirmed:
        raise ConsentRequired("本人が映っている、または映っている人の了承を得ていることの確認（S2）が必要です。")
    src = Path(path)
    if not src.is_file():
        raise FileNotFoundError(src)
    say = progress or (lambda _m: None)
    if info is None:
        say("動画の情報を調べています")
        info = probe(src, project.data.get("settings"))
    warnings = check_guide(info, project.data.get("settings"))

    if copy:
        dest = unique_path(project.root / "sources" / src.name)
        say("プロジェクトにコピーしています")
        shutil.copy2(src, dest)
    else:
        dest = src
    say("ハッシュを計算しています")
    digest = sha256_file(dest)
    rec_consent = project.consent.add(KIND_SOURCE_VIDEO, STATEMENT_SOURCE_VIDEO,
                                      target=project.rel(dest), sha256=digest,
                                      extra={"original_path": str(src.resolve())})
    info.path = project.rel(dest)
    rec = {
        "id": new_id("src"),
        "file": project.rel(dest),
        "copied_into_project": copy,
        "original_path": str(src.resolve()),
        "sha256": digest,
        "media": info.to_dict(),
        "warnings": [x.to_dict() for x in warnings],
        "consent_id": rec_consent["id"],
        "imported_at": now_iso(),
        "note": note,
    }
    project.data["sources"].append(rec)
    project.log("source_imported", {"id": rec["id"], "file": rec["file"], "warnings": [x.code for x in warnings]})
    return rec


# ---------------- 変換の提案（CFR / SDR） ----------------

STANDARD_FPS = [Fraction(24000, 1001), Fraction(24), Fraction(25), Fraction(30000, 1001), Fraction(30),
                Fraction(50), Fraction(60000, 1001), Fraction(60), Fraction(120000, 1001), Fraction(120)]


def nearest_standard_fps(fps: float) -> Fraction:
    return min(STANDARD_FPS, key=lambda f: abs(float(f) - fps))


def has_filter(name: str) -> bool:
    out = run([find_tool("ffmpeg"), "-hide_banner", "-filters"], check=False).stdout.decode("utf-8", "replace")
    return any(line.split()[1:2] == [name] for line in out.splitlines() if len(line.split()) > 1)


def tonemap_filter() -> str:
    """HDR を SDR（BT.709）に変換するフィルタ。FFmpeg のビルドによって使えるものが違う。"""
    if has_filter("zscale") and has_filter("tonemap"):
        return ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
                "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
    if has_filter("libplacebo"):
        return ("libplacebo=tonemapping=bt.2390:colorspace=bt709:color_primaries=bt709:"
                "color_trc=bt709:range=tv:format=yuv420p")
    raise RuntimeError("この FFmpeg には HDR を変換するフィルタ（zscale+tonemap か libplacebo）がありません。"
                       "Windows では gyan.dev の full 版などを使ってください。")


def build_convert_cmd(src: Path, dest: Path, info: MediaInfo, *, to_cfr: bool, to_sdr: bool,
                      fps: Fraction | None = None) -> list[str]:
    vf = []
    args = [find_tool("ffmpeg"), "-hide_banner", "-y", "-i", str(src)]
    if to_sdr:
        vf.append(tonemap_filter())
    if to_cfr:
        target = fps or nearest_standard_fps(info.fps)
        vf.append(f"fps={target.numerator}/{target.denominator}")
    if vf:
        args += ["-vf", ",".join(vf)]
    # 画質優先（U2）。中間の素材なので CRF を低くする。
    pix = "yuv420p" if to_sdr else None
    args += ["-c:v", "libx264", "-preset", "slow", "-crf", "10"]
    if pix:
        args += ["-pix_fmt", pix, "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]
    elif (info.bit_depth or 8) > 8:
        args += ["-pix_fmt", "yuv420p10le", "-profile:v", "high10"]
    if info.has_audio:
        args += ["-c:a", "pcm_s16le" if dest.suffix.lower() in (".mov", ".mkv") else "aac", "-b:a", "320k"]
    if to_cfr:
        args += ["-fps_mode", "cfr"]
    args += ["-map_metadata", "0", "-movflags", "+write_colr", str(dest)]
    if dest.suffix.lower() != ".mov":
        args.remove("-movflags")
        args.remove("+write_colr")
    return args


def convert_source(project: Project, source_id: str, *, to_cfr: bool, to_sdr: bool,
                   fps: Fraction | None = None) -> dict[str, Any]:
    """CFR / SDR に変換した版を sources/ に作り、元の素材から派生したものとして登録する。"""
    parent = project.source(source_id)
    src = project.abs(parent["file"])
    info = MediaInfo.from_dict(parent["media"])
    tag = "_".join(t for t, on in (("cfr", to_cfr), ("sdr", to_sdr)) if on) or "copy"
    dest = unique_path(project.root / "sources" / f"{src.stem}_{tag}.mov")
    cmd = build_convert_cmd(src, dest, info, to_cfr=to_cfr, to_sdr=to_sdr, fps=fps)
    run(cmd)
    new_info = probe(dest, project.data.get("settings"))
    warnings = check_guide(new_info, project.data.get("settings"))
    new_info.path = project.rel(dest)
    rec = {
        "id": new_id("src"),
        "file": project.rel(dest),
        "copied_into_project": True,
        "original_path": parent["original_path"],
        "sha256": sha256_file(dest),
        "media": new_info.to_dict(),
        "warnings": [x.to_dict() for x in warnings],
        # 元の素材の確認記録を引き継ぐ（同じ撮影素材から作ったものなので）
        "consent_id": parent["consent_id"],
        "derived_from": source_id,
        "conversion": {"to_cfr": to_cfr, "to_sdr": to_sdr, "command": cmd[1:]},
        "imported_at": now_iso(),
        "note": f"{parent['file']} から変換",
    }
    project.data["sources"].append(rec)
    project.log("source_converted", {"id": rec["id"], "from": source_id, "to_cfr": to_cfr, "to_sdr": to_sdr})
    return rec

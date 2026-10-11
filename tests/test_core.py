from __future__ import annotations

import json
import shutil

import pytest

from vfc_companion.consent import ConsentRequired
from vfc_companion.project import FOLDERS, Project, ProjectError


# ---------------- M1 ----------------

def test_create_and_reopen(project):
    for sub in FOLDERS:
        assert (project.root / sub).is_dir()
    assert (project.root / "project.json").is_file()
    assert (project.root / "consent_log.json").is_file()
    project.set_manual_tool_version("visomaster_fusion", version="3.12.3")
    project.set_setting("clip_format", "ffv1")
    again = Project.open(project.root)
    assert again.name == "テスト"
    assert again.data["tools"]["visomaster_fusion"]["version"] == "3.12.3"
    assert again.settings["clip_format"] == "ffv1"
    assert [h["action"] for h in again.data["history"]][:1] == ["project_created"]
    assert again.data["tools"]["detected"]["ffmpeg"].startswith("ffmpeg version")


def test_create_twice_fails(project):
    with pytest.raises(ProjectError):
        Project.create(project.root)


def test_project_survives_move(project, tmp_path):
    moved = tmp_path / "moved"
    shutil.move(str(project.root), str(moved))
    assert Project.open(moved).name == "テスト"


# ---------------- M2 ----------------

def codes(rec):
    return {w["code"] for w in rec["warnings"]}


def test_probe_cfr(media_dir):
    from vfc_companion.media import check_guide, probe

    info = probe(media_dir / "cfr.mp4")
    assert (info.width, info.height) == (1280, 720)
    assert abs(info.fps - 30) < 0.01
    assert info.frame_count == 90
    assert info.has_audio and not info.is_vfr and not info.is_hdr
    assert {w.code for w in check_guide(info)} >= {"resolution", "fps"}


def test_probe_vfr_hdr_dark(media_dir):
    from vfc_companion.media import check_guide, probe

    assert probe(media_dir / "vfr.mp4").is_vfr
    hdr = probe(media_dir / "hdr.mp4")
    assert hdr.is_hdr and hdr.bit_depth == 10
    assert "hdr" in {w.code for w in check_guide(hdr)}
    dark = probe(media_dir / "dark4k.mp4")
    assert {w.code for w in check_guide(dark)} == {"dark", "no_audio"}


def test_import_requires_consent(project, media_dir):
    from vfc_companion.media import import_source

    with pytest.raises(ConsentRequired):
        import_source(project, media_dir / "cfr.mp4", consent_confirmed=False)
    assert project.data["sources"] == []


def test_import_records_consent(project, media_dir):
    from vfc_companion.media import import_source

    rec = import_source(project, media_dir / "日本語の動画.mp4", consent_confirmed=True)
    assert (project.root / rec["file"]).is_file()
    c = project.consent.find(rec["consent_id"])
    assert c["kind"] == "source_video" and c["sha256"] == rec["sha256"] and "S2" in c["statement"]
    again = Project.open(project.root)
    assert again.source(rec["id"])["media"]["frame_count"] == 90


def test_convert_cfr_and_sdr(project, media_dir):
    from vfc_companion.media import convert_source, import_source

    v = import_source(project, media_dir / "vfr.mp4", consent_confirmed=True)
    assert "vfr" in codes(v)
    c = convert_source(project, v["id"], to_cfr=True, to_sdr=False)
    assert "vfr" not in codes(c) and c["derived_from"] == v["id"] and c["consent_id"] == v["consent_id"]
    h = import_source(project, media_dir / "hdr.mp4", consent_confirmed=True)
    s = convert_source(project, h["id"], to_cfr=False, to_sdr=True)
    assert not s["media"]["is_hdr"] and s["media"]["color_transfer"] == "bt709"


# ---------------- M3 ----------------

@pytest.mark.parametrize("fmt", ["h264_lossless", "ffv1", "prores_hq", "h264_high"])
def test_cut_clip_frame_exact(project, media_dir, fmt):
    import numpy as np

    from vfc_companion.clips import cut_clip
    from vfc_companion.frames import read_frame
    from vfc_companion.media import import_source

    src = import_source(project, media_dir / "cfr.mp4", consent_confirmed=True)
    clip = cut_clip(project, src["id"], 30, 45, fmt_key=fmt)
    assert clip["actual_frame_count"] == 15 and "warning" not in clip
    assert clip["start_time"] == 1.0
    # 切り出しの最初のフレームが元の 30 フレーム目と一致する（前後のフレームより近い）
    first = read_frame(project.abs(clip["file"]), 0, 30, 1280, 720, max_width=320).astype(float)
    diffs = {n: np.abs(first - read_frame(project.abs(src["file"]), n, 30, 1280, 720, max_width=320)).mean()
             for n in (29, 30, 31)}
    assert min(diffs, key=diffs.get) == 30
    assert Project.open(project.root).clip(clip["id"])["source_id"] == src["id"]


def test_cut_clip_bad_range(project, media_dir):
    from vfc_companion.clips import cut_clip
    from vfc_companion.media import import_source

    src = import_source(project, media_dir / "cfr.mp4", consent_confirmed=True)
    with pytest.raises(ProjectError):
        cut_clip(project, src["id"], 50, 200)


def test_scan_face_events(project, media_dir):
    from vfc_companion.clips import load_scan, scan_face_events
    from vfc_companion.media import import_source

    src = import_source(project, media_dir / "faces.mp4", consent_confirmed=True)
    res = scan_face_events(project, src["id"])
    kinds = {s["kind"] for s in res["segments"]}
    assert "reappear" in kinds and "multi" in kinds
    gap = next(s for s in res["segments"] if s["kind"] == "reappear")
    assert 0.8 <= gap["start_time"] <= 1.2 and 1.8 <= gap["end_time"] <= 2.2
    assert load_scan(project, src["id"])["complete"]


# ---------------- M4 ----------------

def test_person_requires_consent(project):
    from vfc_companion.face_assets import add_person

    with pytest.raises(ConsentRequired):
        add_person(project, "Aoi", consent_confirmed=False)


def test_face_assets(project, face_images):
    from vfc_companion.face_assets import add_images, add_person, remove_image

    p = add_person(project, "Aoi", consent_confirmed=True)
    assert project.consent.find(p["consent_id"])["kind"] == "face_person"
    add_images(project, p["id"], [face_images[k] for k in ("single_big", "single_small", "group", "blank",
                                                            "single_flip")])
    person = Project.open(project.root).person(p["id"])
    by_name = {i["original_name"]: i["analysis"] for i in person["images"]}
    assert by_name["single_big.png"]["issues"] == []
    assert "low_res" in by_name["single_small.png"]["issues"]
    assert "multi_face" in by_name["group.jpg"]["issues"]
    assert "no_face" in by_name["blank.png"]["issues"]
    # 左右反転した写真は左右の向きの符号が逆になる
    y1 = by_name["single_big.png"]["face"]["yaw"]
    y2 = by_name["single_flip.png"]["face"]["yaw"]
    assert y1 * y2 <= 0 or abs(y1) < 3
    # 同じ写真の拡大・縮小・反転なので似ている度合いは高い
    assert by_name["single_big.png"]["similarity_to_others"] > 0.6
    s = person["summary"]
    assert s["total"] == 5 and s["usable"] == 3 and s["shortages"]
    # 同じ写真は二重に登録されない
    add_images(project, p["id"], [face_images["single_big"]])
    assert len(project.person(p["id"])["images"]) == 5
    img_id = person["images"][0]["id"]
    project2 = Project.open(project.root)
    remove_image(project2, p["id"], img_id)
    assert len(Project.open(project.root).person(p["id"])["images"]) == 4


def test_glasses_heuristic(face_images, tmp_path):
    """メガネを描き足すと簡易判定の値が上がる。"""
    import cv2

    from vfc_companion.faces import detect, glasses_score
    from vfc_companion.frames import imread_unicode

    img = imread_unicode(face_images["single_big"])
    f = detect(img)[0]
    base = glasses_score(img, f)
    le, re_ = f.kps[0].astype(int), f.kps[1].astype(int)
    r = int((re_[0] - le[0]) * 0.32)
    g = img.copy()
    for c in (le, re_):
        cv2.rectangle(g, (c[0] - r, c[1] - int(r * 0.7)), (c[0] + r, c[1] + int(r * 0.7)), (20, 20, 20), 6)
    cv2.line(g, (le[0] + r, le[1]), (re_[0] - r, re_[1]), (20, 20, 20), 6)
    with_glasses = glasses_score(g, detect(g)[0])
    print("glasses score", base, with_glasses)
    assert with_glasses > base


def test_cli(tmp_path, media_dir, capsys):
    from vfc_companion.cli import main

    root = tmp_path / "cli_proj"
    assert main(["new", str(root), "--name", "CLI"]) == 0
    capsys.readouterr()
    assert main(["import-video", str(root), str(media_dir / "cfr.mp4"), "--i-confirm-consent"]) == 0
    out = capsys.readouterr().out
    sid = json.loads(out)["id"]
    assert main(["cut", str(root), sid, "0", "10"]) == 0
    with pytest.raises(ConsentRequired):
        main(["add-person", str(root), "X"])

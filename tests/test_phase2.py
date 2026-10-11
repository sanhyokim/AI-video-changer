"""フェーズ 2（M5 比較動画、M6 品質チェック、シーンプロファイル）のテスト。

FaceFusion は tests/fake_facefusion/facefusion.py（本物の 3.9.1 の --help を返し、入力を出力に写すだけ）で代用する。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
FAKE_FF = HERE / "fake_facefusion"
HELP_WIDE = (HERE / "data" / "ff_help_3.9.1.txt").read_text(encoding="utf-8")
HELP_NARROW = (HERE / "data" / "ff_help_3.9.1_narrow.txt").read_text(encoding="utf-8")


def frames_of(path) -> int:
    from vfc_companion.media import count_frames

    return count_frames(Path(path))


def size_of(path) -> tuple[int, int]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "json", str(path)], capture_output=True, check=True)
    s = json.loads(out.stdout)["streams"][0]
    return s["width"], s["height"]


@pytest.fixture
def p2(project, media_dir, face_images, monkeypatch):
    """取り込み済みの素材、3 秒のクリップ、顔素材 2 枚の人物、偽の FaceFusion を設定したプロジェクト。"""
    from vfc_companion.clips import cut_clip
    from vfc_companion.face_assets import add_images, add_person
    from vfc_companion.media import import_source

    src = import_source(project, media_dir / "faces.mp4", consent_confirmed=True)
    clip = cut_clip(project, src["id"], 0, 90)
    person = add_person(project, "Aoi", consent_confirmed=True)
    add_images(project, person["id"], [face_images["single_big"], face_images["single_flip"]])
    project.set_setting("facefusion_dir", str(FAKE_FF))
    project.set_setting("facefusion_python", sys.executable)
    monkeypatch.delenv("FAKE_FF_MODE", raising=False)
    project.save()
    return project, clip, person


# =====================================================================
# --help の読み取りと照合
# =====================================================================
@pytest.mark.parametrize("text", [HELP_WIDE, HELP_NARROW], ids=["wide", "narrow"])
def test_parse_real_help(text):
    from vfc_companion.facefusion import parse_help

    h = parse_help(text)
    assert {"alphaface_256", "hyperswap_1a_256"} <= set(h.choices["--face-swapper-model"])
    assert "hrffa" in h.choices["--face-landmarker-model"]
    assert h.choices["--face-selector-mode"] == ["many", "one", "reference"]  # auto はない（phase0 No.3）
    assert h.choices["--expression-restorer-areas"] == ["upper-face", "lower-face"]
    assert {"face_swapper", "expression_restorer", "face_enhancer"} <= set(h.choices["--processors"])
    assert h.choices["--execution-providers"] == ["cpu"]
    assert h.ranges["--face-enhancer-blend"] == (0.0, 100.0)
    assert h.ranges["--reference-face-distance"] == (0.0, 1.0)
    for flag in ("--reference-frame-number", "--reference-face-position", "--face-selector-order",
                 "--face-selector-gender", "--face-swapper-pixel-boost", "--expression-restorer-factor"):
        assert h.has(flag), flag


def test_build_args_pass_validation_and_catch_mistakes():
    from vfc_companion.config import DEFAULT_SETTINGS
    from vfc_companion.facefusion import build_args, parse_help, validate_args

    h = parse_help(HELP_WIDE)
    a = {"person_id": "p", "reference_frame": 12, "reference_face_position": 1, "reference_face_distance": 0.3,
         "face_selector_order": "large-small", "face_selector_gender": "female"}
    for model in ("alphaface_256", "hyperswap_1a_256"):
        args = build_args(DEFAULT_SETTINGS, h, {"name": model, "face_swapper_model": model}, a,
                          [Path("a.png"), Path("b.png")], Path("t.mp4"), Path("o.mp4"))
        assert validate_args(args, h) == []
        assert args[args.index("--execution-providers") + 1] == "cpu"  # auto → ヘルプにある cpu
        assert args[args.index("--face-landmarker-model") + 1] == "hrffa"
        assert args[args.index("--face-selector-mode") + 1] == "reference"
        assert args[args.index("--reference-frame-number") + 1] == "12"
        assert args[args.index("--face-enhancer-blend") + 1] == "30"
    bad = ["--face-swapper-model", "inswapper_512", "--face-selector-mode", "auto", "--no-such-flag", "1",
           "--face-enhancer-blend", "150"]
    problems = validate_args(bad, h)
    assert len(problems) == 4
    assert any("inswapper_512" in p for p in problems)
    assert any("auto" in p for p in problems)


def test_locate_reports_missing(tmp_path):
    from vfc_companion.facefusion import FaceFusionError, locate

    with pytest.raises(FaceFusionError, match="facefusion_dir"):
        locate({"facefusion_dir": str(tmp_path), "facefusion_python": ""})


# =====================================================================
# M5：FaceFusion の実行と比較動画（ボタン 1 つ）
# =====================================================================
def test_one_button_comparison(p2, tmp_path, monkeypatch):
    from vfc_companion.compare import run_one_button
    from vfc_companion.facefusion import STATUS_DONE

    project, clip, _person = p2
    log = tmp_path / "args.log"
    monkeypatch.setenv("FAKE_FF_ARGS_LOG", str(log))
    res = run_one_button(project, clip["id"])
    project.save()
    ff_runs = res["facefusion"]
    assert [r["variant"]["face_swapper_model"] for r in ff_runs] == ["alphaface_256", "hyperswap_1a_256"]
    assert all(r["status"] == STATUS_DONE for r in ff_runs)
    for r in ff_runs:
        assert project.abs(r["file"]).exists()
        # 1 人だけなので、自動で基準の顔（最初に顔があるフレーム）を決めて reference モードで実行
        assert r["assignments"][0]["reference_frame"] == 0
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and all("--face-selector-mode reference" in ln for ln in lines)
    assert project.data["tools"]["facefusion"].get("version") in (None, "", "3.9.1")

    cmp = res["comparison"]
    video = project.abs(cmp["video"])
    assert [p["label"] for p in cmp["panels"]] == ["元の動画", "FaceFusion alphaface_256", "FaceFusion hyperswap_1a_256"]
    assert frames_of(video) == 90
    w, h = size_of(video)
    assert h == 886 + 44 and w == 3 * 1280
    assert len(cmp["images"]) == 5 and all(project.abs(i).exists() for i in cmp["images"])

    # 同じ設定ならもう一度は実行しない
    res2 = run_one_button(project, clip["id"])
    assert res2["facefusion"] == []
    assert sum("再利用" in s for s in res2["skipped"]) == 2


def test_two_people_run_in_two_passes_with_ascii_staging(p2, face_images, tmp_path, monkeypatch):
    from vfc_companion.face_assets import add_images, add_person
    from vfc_companion.facefusion import set_assignments, run_variant

    project, clip, person = p2
    other = add_person(project, "Ren", consent_confirmed=True)
    add_images(project, other["id"], [face_images["single_big"]])
    set_assignments(project, clip["id"], [{"person_id": person["id"], "reference_frame": 0, "reference_face_position": 0},
                                          {"person_id": other["id"], "reference_frame": 0, "reference_face_position": 1}])
    log = tmp_path / "args.log"
    monkeypatch.setenv("FAKE_FF_ARGS_LOG", str(log))
    monkeypatch.setenv("VFC_STAGE_DIR", str(tmp_path / "stage"))
    rec = run_variant(project, clip["id"], {"name": "ff_alphaface_256", "face_swapper_model": "alphaface_256"},
                      force_stage=True)
    assert rec["status"] == "done", rec.get("error")
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert "--reference-face-position 0" in lines[0] and "--reference-face-position 1" in lines[1]
    assert all(str(project.root) not in ln for ln in lines)  # 日本語のパスは渡さない
    assert frames_of(project.abs(rec["file"])) == 90
    assert not (tmp_path / "stage" / "vfc_ff" / rec["id"]).exists()
    assert not list(project.abs(rec["file"]).parent.glob("*_step*.mp4"))


def test_content_filter_stop_excludes_clip(p2, monkeypatch):
    from vfc_companion.compare import run_one_button
    from vfc_companion.facefusion import STATUS_BLOCKED, ContentFilterStop, register_render, run_variant

    project, clip, _person = p2
    monkeypatch.setenv("FAKE_FF_MODE", "block")
    vm_dir = project.root / "renders" / "visomaster"
    shutil.copy(project.abs(clip["file"]), vm_dir / "vm_take1.mp4")
    register_render(project, clip["id"], vm_dir / "vm_take1.mp4", label="VisoMaster take1")
    res = run_one_button(project, clip["id"])
    assert res["facefusion"][0]["status"] == STATUS_BLOCKED
    assert len(res["facefusion"]) == 1  # 1 本目で止まったら残りも実行しない
    assert project.clip(clip["id"])["facefusion_blocked"]
    labels = [p["label"] for p in res["comparison"]["panels"]]
    assert labels == ["元の動画", "VisoMaster take1"]
    # 同じクリップを FaceFusion で処理し直さない（S4）
    monkeypatch.setenv("FAKE_FF_MODE", "ok")
    with pytest.raises(ContentFilterStop):
        run_variant(project, clip["id"], {"name": "x", "face_swapper_model": "alphaface_256"})
    res2 = run_one_button(project, clip["id"])
    assert res2["facefusion"] == [] and any("S4" in s for s in res2["skipped"])


def test_failed_run_is_recorded(p2, monkeypatch):
    from vfc_companion.facefusion import STATUS_FAILED, run_variant

    project, clip, _ = p2
    monkeypatch.setenv("FAKE_FF_MODE", "fail")
    rec = run_variant(project, clip["id"], {"name": "x", "face_swapper_model": "alphaface_256"})
    assert rec["status"] == STATUS_FAILED and "終了コード 1" in rec["error"]
    assert project.data["renders"][-1]["id"] == rec["id"]


def test_find_visomaster_outputs(p2):
    from vfc_companion.facefusion import find_visomaster_outputs, sync_visomaster

    project, clip, _ = p2
    vm = project.root / "renders" / "visomaster"
    (vm / "v1").mkdir()
    shutil.copy(project.abs(clip["file"]), vm / "v1" / (Path(clip["file"]).stem + "_swapped.mp4"))
    shutil.copy(project.abs(clip["file"]), vm / "output_0001.mp4")
    hits = {Path(h["file"]).name: h for h in find_visomaster_outputs(project)}
    assert hits[Path(clip["file"]).stem + "_swapped.mp4"]["match"] == "name"
    assert hits["output_0001.mp4"]["match"] == "frame_count"
    recs = sync_visomaster(project, clip["id"])
    assert len(recs) == 2 and all(r["tool"] == "visomaster" for r in recs)
    assert len(sync_visomaster(project, clip["id"])) == 2  # 2 回目は登録済みを返すだけ
    assert len(project.data["renders"]) == 2
    # 書き出しのバージョンのフォルダに置いたものは、名前に関係なくそのクリップの出力
    ver = project.add_render_version(clip_id=clip["id"], person_ids=[], settings_note="512")
    shutil.copy(project.abs(clip["file"]), project.abs(ver["folders"]["visomaster"]) / "out.mp4")
    hit = next(h for h in find_visomaster_outputs(project) if h["file"].endswith("/out.mp4"))
    assert (hit["clip_id"], hit["match"]) == (clip["id"], "version")


def test_four_panels_use_grid(p2):
    from vfc_companion.compare import make_comparison
    from vfc_companion.facefusion import register_render

    project, clip, _ = p2
    vm = project.root / "renders" / "visomaster"
    ids = []
    for i in range(4):
        f = vm / f"take{i}.mp4"
        shutil.copy(project.abs(clip["file"]), f)
        ids.append(register_render(project, clip["id"], f, label=f"テイク{i}")["id"])
    cmp = make_comparison(project, clip["id"], frames=[5, 70])
    assert len(cmp["panels"]) == 4 and cmp["panels"][0]["label"] == "テイク0"  # 4 本なら元の動画は並べない
    w, h = size_of(project.abs(cmp["video"]))
    assert (w, h) == (2 * 1280, 2 * (886 + 44))
    assert [Path(i).name for i in cmp["images"]] == ["frame_000005.png", "frame_000070.png"]


def test_label_image_draws_japanese():
    from vfc_companion.compare import label_image

    img = label_image("元の動画 FaceFusion", 400)
    assert img.shape == (44, 400, 3) and img.max() > 200


# =====================================================================
# M6：指標の計算（合成データ）
# =====================================================================
def _unit(v):
    v = np.asarray(v, np.float32)
    return v / np.linalg.norm(v)


def _face(x, emb, w=0.2):
    return {"bbox": np.array([x, 0.2, x + w, 0.6], np.float32), "emb": _unit(emb), "score": 0.9}


def _basis(i, n=512):
    v = np.zeros(n, np.float32)
    v[i] = 1
    return v


S = {
    "qc_step": 1, "qc_min_sim_faceset": 0.30, "qc_max_sim_original": 0.40, "qc_max_flicker": 0.12,
    "qc_max_reappear_delta": 0.10, "qc_reappear_gap_frames": 3, "qc_reappear_window_seconds": 0.2,
    "qc_mismatch_margin": 0.05, "qc_track_match": 0.35, "qc_merge_gap_frames": 2, "qc_pass_ratio": 0.02,
}


def test_compute_single_person_metrics():
    from vfc_companion.qc import compute

    orig, ai = _basis(0), _basis(1)
    good = 0.8 * ai + 0.2 * orig          # 顔素材に近い出力
    src, out = {}, {}
    for n in range(40):
        src[n] = [_face(0.4, orig)]
        out[n] = [_face(0.41, good)]
    for n in (10, 11):                    # 検出漏れ
        out[n] = []
    out[20] = [_face(0.41, 0.2 * ai + 0.8 * orig)]   # 元の顔が残る＋似ていない＋ちらつき
    for n in range(25, 30):               # 顔が消える（回転など）
        src[n], out[n] = [], []
    for n in range(30, 34):               # 再び現れたら別人っぽい
        out[n] = [_face(0.41, 0.55 * ai + 0.45 * _basis(2))]
    res = compute(src, out, [{"person_id": "p", "reference_frame": None}], {"p": _unit(ai)}, S, fps=10.0)
    assert res["implicit"] and res["target_frames"] == 35
    rb = {int(k): set(v) for k, v in res["reasons_by_frame"].items()}
    assert rb[10] == {"miss"} and rb[11] == {"miss"}
    assert {"high_sim_original", "low_sim_faceset", "flicker"} <= rb[20]
    assert "flicker" in rb[21]
    assert "reappear" in rb[30] and "reappear" in rb[31]
    ev = res["reappear_events"][0]
    assert (ev["gap_start"], ev["gap_end"], ev["flagged"]) == (25, 29, True)
    # 10-11 と 20-21 と 30-31 の 3 区間（間隔 2 フレーム以内はまとめる）
    spans = [(iv["start_frame"], iv["end_frame"]) for iv in res["intervals"]]
    assert spans[0] == (10, 11) and spans[1][0] == 20
    assert res["pass"] is False and res["ratio"] == pytest.approx(len(rb) / 35)


def test_compute_multi_person_mismatch_and_wrong_swap():
    from vfc_companion.qc import compute

    origA, origB, bystander = _basis(0), _basis(1), _basis(2)
    aiA, aiB = _basis(3), _basis(4)
    src, out = {}, {}
    for n in range(10):
        # 大きい順：A（幅 0.3）、B（0.2）、通りがかりの人（0.1）
        src[n] = [_face(0.05, origA, 0.3), _face(0.5, origB, 0.2), _face(0.8, bystander, 0.1)]
        out[n] = [_face(0.05, aiA, 0.3), _face(0.5, aiB, 0.2), _face(0.8, bystander, 0.1)]
    out[5] = [_face(0.05, aiB, 0.3), _face(0.5, aiB, 0.2), _face(0.8, bystander, 0.1)]  # A が B の顔に
    out[7] = [_face(0.05, aiA, 0.3), _face(0.5, aiB, 0.2), _face(0.8, aiA, 0.1)]        # 通りがかりが A の顔に
    assigns = [{"person_id": "A", "reference_frame": 0, "reference_face_position": 0},
               {"person_id": "B", "reference_frame": 0, "reference_face_position": 1}]
    res = compute(src, out, assigns, {"A": aiA, "B": aiB}, S, fps=30.0)
    rb = {int(k): set(v) for k, v in res["reasons_by_frame"].items()}
    assert "mismatch" in rb[5]
    assert rb[7] == {"wrong_swap"}
    assert set(rb) == {5, 6, 7}  # 6 は A のちらつき（5 → 6 で似ている度合いが戻る）
    assert res["wrong_swap_frames"] == [7]


# =====================================================================
# M6：実際の動画で（InsightFace を使う）
# =====================================================================
def test_run_qc_on_unchanged_output_flags_original_face(p2):
    from vfc_companion.facefusion import register_render
    from vfc_companion.qc import apply_thresholds, load_metrics, propose_thresholds, run_qc, set_visual_check

    project, clip, _ = p2
    project.set_setting("qc_step", 3)
    f = project.root / "renders" / "visomaster" / "same.mp4"
    shutil.copy(project.abs(clip["file"]), f)
    r = register_render(project, clip["id"], f)
    rec = run_qc(project, r["id"])
    project.save()
    m = load_metrics(project, rec["id"])
    # 置き換えていないので、元の人物との似ている度合いがほぼ 1 で「元の顔が残っている」
    sims = [v for v in m["series"][m["persons"][0]]["sim_orig"] if v is not None]
    assert sims and min(sims) > 0.9
    assert rec["pass"] is False and rec["ratio"] == pytest.approx(1.0)
    assert m["intervals"] and "high_sim_original" in m["intervals"][0]["reasons"]
    # 黒い 1 秒（顔なし）をはさむので、区間は 2 つ、再び現れる場面が 1 回
    assert len(m["intervals"]) == 2 and len(m["reappear_events"]) == 1
    iv = m["intervals"][1]
    assert iv["source_start_frame"] == clip["start_frame"] + iv["start_frame"]
    report = project.abs(rec["report"])
    html = report.read_text(encoding="utf-8")
    assert "品質チェック" in html and "この時刻へ" in html and "../../renders/visomaster/same.mp4" in html
    assert (report.parent / m["intervals"][0].get("thumb", "thumbs")).exists()
    # キャッシュが効く（2 回目は元のクリップを解析し直さない）
    assert list((project.root / "qc" / "cache").glob("*.npz"))
    set_visual_check(project, rec["id"], "expression", True)
    assert project.data["qc_reports"][-1]["visual_checks"]["expression"]["ok"] is True
    # 基準値の決め直し
    props = propose_thresholds(project, [rec["id"]])
    assert props["qc_max_sim_original"]["proposed"] > 0.9
    apply_thresholds(project, props, [rec["id"]])
    assert project.settings["qc_max_sim_original"] == props["qc_max_sim_original"]["proposed"]
    assert project.data["qc_reports"][-1]["judged_good"] is True


# =====================================================================
# シーンプロファイルと CLI
# =====================================================================
def test_scene_profiles(p2):
    from vfc_companion.profiles import PROFILES, export_markdown, get_profile, save_profile
    from vfc_companion.project import ProjectError

    project, *_ = p2
    assert [p["id"] for p in PROFILES] == [f"P{i}" for i in range(1, 8)]
    with pytest.raises(ProjectError):
        save_profile(project, "P1", settings=[], evidence=["cmp_nothere"])
    save_profile(project, "P1", settings=[("Enable Face Expression Restorer", "オン")], notes="苦しい表情",
                 decided=True)
    rec = get_profile(project, "P1")
    assert rec["decided_at"] and rec["settings"] == [["Enable Face Expression Restorer", "オン"]]
    md = export_markdown(project)
    assert "## P1 表情が激しい（決定" in md and "## P7 照明が変わる（未決定）" in md


def test_cli_phase2(p2, capsys):
    from vfc_companion.cli import main

    project, clip, person = p2
    root = str(project.root)
    assert main(["ff-check", root]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["version"] == "3.9.1" and "alphaface_256" in out["face_swapper_models"]
    assert main(["assign", root, clip["id"], f"{person['id']}:0:0"]) == 0
    capsys.readouterr()
    assert main(["compare", root, clip["id"]]) == 0
    res = json.loads(capsys.readouterr().out)
    assert len(res["facefusion"]) == 2 and res["comparison"]["video"].endswith(".mp4")
    assert main(["profiles", root]) == 0
    assert (project.root / "export" / "scene_profiles.md").exists()


# =====================================================================
# M8：DFM の学習用素材
# =====================================================================
def test_crop_square_pads_outside_and_limits_size():
    from vfc_companion.dfm import crop_square

    img = np.full((100, 200, 3), 200, np.uint8)
    c = crop_square(img, [-10, -10, 30, 30], 2.0, 1024)   # 枠の 2 倍 = 80px、左上がはみ出す
    assert c.shape == (80, 80, 3)
    assert c[0, 0].tolist() == [0, 0, 0] and c[-1, -1].tolist() == [200, 200, 200]
    big = crop_square(np.zeros((2000, 2000, 3), np.uint8), [500, 500, 1300, 1300], 2.0, 512)
    assert big.shape == (512, 512, 3)


def test_dfm_collect_from_clean_frames(p2):
    from vfc_companion.dfm import candidate_frames, collect, eligible_reports
    from vfc_companion.facefusion import register_render
    from vfc_companion.qc import load_metrics, run_qc

    project, clip, person = p2
    # 入力をそのまま写した出力を「問題なし」にするため、基準をゆるめる（テスト用）
    for k, v in {"qc_step": 3, "qc_min_sim_faceset": -1.0, "qc_max_sim_original": 1.01, "qc_max_flicker": 2.0,
                 "qc_max_reappear_delta": 2.0, "dfm_min_face_px": 20, "dfm_min_sim_faceset": -1.0,
                 "dfm_max_per_bin": 3, "dfm_min_sharpness": 0.0}.items():
        project.set_setting(k, v)
    f = project.root / "renders" / "visomaster" / "clean.mp4"
    shutil.copy(project.abs(clip["file"]), f)
    r = register_render(project, clip["id"], f)
    q = run_qc(project, r["id"])
    m = load_metrics(project, q["id"])
    assert m["problem_frames"] == 0
    # 顔があるのは 0-29 と 60-89。3 フレームおきに調べ、5 フレーム以上あけて選ぶ
    assert candidate_frames(m, person["id"], 5) == [0, 6, 12, 18, 24, 60, 66, 72, 78, 84]
    assert [x["id"] for x in eligible_reports(project, person["id"])] == [q["id"]]

    rec = collect(project, person["id"], [q["id"]])
    root = project.abs(rec["dir"])
    src = sorted((root / "data_src").glob("*.jpg"))
    dst = sorted((root / "data_dst").glob("*.jpg"))
    # 静止画の動画なので全部同じ角度。1 つの角度に 3 枚まで残し、写真 2 枚は必ず残す
    assert rec["src_photos"] == 2 and rec["src_frames"] == 3 and len(src) == 5
    assert rec["dst_frames"] == 3 and len(dst) == 3
    assert rec["skipped"]["pruned"] == 14
    assert rec["src_coverage"]["total"] == 5 and rec["src_coverage"]["warnings"]
    for p in [x for x in src if not x.name.startswith("photo_")] + dst:
        h, w = cv2_size(p)
        assert h == w and h <= 1024
    man = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert len(man["src"]) == 5 and all((project.root / it["file"]).exists() for it in man["src"] + man["dst"])
    assert (root / "次にやること.txt").exists()
    assert project.data["dfm_sets"][-1]["id"] == rec["id"]
    ex = rec["src_coverage"]["expressions"]
    assert ex["measured"] >= 3 and any("表情" in w or "口を開けた" in w for w in rec["src_coverage"]["warnings"])


def test_dfm_cli_without_dst(p2, capsys):
    from vfc_companion.cli import main
    from vfc_companion.facefusion import register_render
    from vfc_companion.qc import run_qc

    project, clip, person = p2
    for k, v in {"qc_step": 6, "qc_min_sim_faceset": -1.0, "qc_max_sim_original": 1.01, "qc_max_flicker": 2.0,
                 "qc_max_reappear_delta": 2.0, "dfm_min_face_px": 20, "dfm_min_sim_faceset": -1.0}.items():
        project.set_setting(k, v)
    f = project.root / "renders" / "visomaster" / "clean.mp4"
    shutil.copy(project.abs(clip["file"]), f)
    q = run_qc(project, register_render(project, clip["id"], f)["id"])
    project.save()
    assert main(["dfm-collect", str(project.root), person["id"], q["id"], "--no-dst"]) == 0
    rec = json.loads(capsys.readouterr().out)
    assert rec["dst_frames"] == 0 and rec["src_frames"] > 0 and rec["dst_coverage"] is None
    assert not (project.root / rec["dir"] / "data_dst").exists()


def test_expression_from_landmarks(face_images):
    import cv2

    from vfc_companion.config import DEFAULT_SETTINGS
    from vfc_companion.dfm import expression
    from vfc_companion.faces import detect

    img = cv2.imread(str(face_images["single_big"]))
    face = detect(img)[0]
    e = expression(face, DEFAULT_SETTINGS)
    assert e is not None and not e["mouth_open"] and 0.15 < e["ear"] < 0.45
    lm = face.lmk68.copy()
    c = lm[60:68, 1].mean()
    lm[60:68, 1] = c + (lm[60:68, 1] - c) * 8          # 口を縦に大きく開いたことにする
    lm[[41, 40, 47, 46]] = lm[[37, 38, 43, 44]]           # 下まぶたを上まぶたに重ねる（目を閉じたことにする）
    face.lmk68 = lm
    e2 = expression(face, DEFAULT_SETTINGS)
    assert e2["mouth_open"] and e2["eyes_narrow"]
    face.yaw = 60.0
    assert expression(face, DEFAULT_SETTINGS) is None


def cv2_size(path):
    import cv2

    img = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
    return img.shape[:2]

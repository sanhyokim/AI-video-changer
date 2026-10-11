"""コマンドラインでの操作（AI エージェントや一括処理向け）。

例:
  python -m vfc_companion.cli new D:\\vfc\\dance01 --name ダンス1
  python -m vfc_companion.cli probe D:\\撮影\\IMG_0001.MOV
  python -m vfc_companion.cli import-video D:\\vfc\\dance01 D:\\撮影\\IMG_0001.MOV --i-confirm-consent
  python -m vfc_companion.cli scan D:\\vfc\\dance01 src_xxxx
  python -m vfc_companion.cli cut D:\\vfc\\dance01 src_xxxx 600 1200
  python -m vfc_companion.cli add-person D:\\vfc\\dance01 "Aoi" --i-confirm-ai-generated
  python -m vfc_companion.cli add-faces D:\\vfc\\dance01 p_Aoi D:\\faces\\aoi\\*.png
  python -m vfc_companion.cli ff-check D:\\vfc\\dance01
  python -m vfc_companion.cli assign D:\\vfc\\dance01 clip_xxxx p_Aoi:0:0 p_Ren:0:1
  python -m vfc_companion.cli compare D:\\vfc\\dance01 clip_xxxx
  python -m vfc_companion.cli qc D:\\vfc\\dance01 r_xxxx
  python -m vfc_companion.cli qc-calibrate D:\\vfc\\dance01 qc_xxxx --apply
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="vfc_companion")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("new", help="プロジェクトを作る")
    p.add_argument("root")
    p.add_argument("--name")

    p = sub.add_parser("info", help="プロジェクトの概要を表示する")
    p.add_argument("root")

    p = sub.add_parser("probe", help="動画を調べる（取り込まない）")
    p.add_argument("video")

    p = sub.add_parser("import-video", help="動画を取り込む")
    p.add_argument("root")
    p.add_argument("video")
    p.add_argument("--i-confirm-consent", action="store_true",
                   help="本人が映っている、または映っている人の了承を得ていることを確認した（S2）")
    p.add_argument("--no-copy", action="store_true", help="コピーせず元の場所を参照する")

    p = sub.add_parser("convert", help="CFR / SDR に変換した版を作る")
    p.add_argument("root")
    p.add_argument("source_id")
    p.add_argument("--cfr", action="store_true")
    p.add_argument("--sdr", action="store_true")

    p = sub.add_parser("scan", help="顔が消える・傾く・小さい区間の候補を探す")
    p.add_argument("root")
    p.add_argument("source_id")

    p = sub.add_parser("cut", help="テスト用クリップを切り出す（終了フレームは含まない）")
    p.add_argument("root")
    p.add_argument("source_id")
    p.add_argument("start_frame", type=int)
    p.add_argument("end_frame", type=int)
    p.add_argument("--format", choices=["h264_lossless", "ffv1", "prores_hq", "h264_high"])
    p.add_argument("--note", default="")

    p = sub.add_parser("add-person", help="AI 人物を登録する")
    p.add_argument("root")
    p.add_argument("name")
    p.add_argument("--i-confirm-ai-generated", action="store_true",
                   help="AI で作った実在しない人物であることを確認した（S1）")

    p = sub.add_parser("add-faces", help="顔写真をまとめて登録する")
    p.add_argument("root")
    p.add_argument("person_id")
    p.add_argument("images", nargs="+")

    p = sub.add_parser("faces-report", help="顔素材の検査結果を表示する")
    p.add_argument("root")
    p.add_argument("person_id")

    # ---- フェーズ 2（M5 比較、M6 品質チェック、シーンプロファイル） ----
    p = sub.add_parser("ff-check", help="FaceFusion の場所とバージョンと --help を確かめる")
    p.add_argument("root")

    p = sub.add_parser("assign", help="クリップで置き換える人物を割り当てる（人物ID:基準フレーム:顔の位置）")
    p.add_argument("root")
    p.add_argument("clip_id")
    p.add_argument("items", nargs="+", help="例: p_Aoi:0:0（基準フレームを省くと自動で探す）")
    p.add_argument("--distance", type=float, default=None, help="似ている度合いの基準（初期値 0.3）")

    p = sub.add_parser("ff-run", help="FaceFusion を 1 つの設定で実行する")
    p.add_argument("root")
    p.add_argument("clip_id")
    p.add_argument("--model", help="顔交換モデル（省くと設定 ff_models の全部）")

    p = sub.add_parser("find-vm", help="renders/visomaster/ の出力を探して登録する")
    p.add_argument("root")
    p.add_argument("clip_id", nargs="?")

    p = sub.add_parser("register", help="出力の動画をクリップの結果として登録する")
    p.add_argument("root")
    p.add_argument("clip_id")
    p.add_argument("file")
    p.add_argument("--label", default="")
    p.add_argument("--tool", default="visomaster")

    p = sub.add_parser("compare", help="FaceFusion の実行から比較動画の作成までを一度に行う")
    p.add_argument("root")
    p.add_argument("clip_id")
    p.add_argument("--no-facefusion", action="store_true", help="FaceFusion は実行せず、ある結果だけで比べる")
    p.add_argument("--renders", nargs="*", help="並べる出力の ID（省くと自動）")

    p = sub.add_parser("qc", help="出力の品質チェックをしてレポートを作る")
    p.add_argument("root")
    p.add_argument("render_id")

    p = sub.add_parser("qc-calibrate", help="よい出力の品質チェック結果から基準値を決める")
    p.add_argument("root")
    p.add_argument("qc_ids", nargs="+")
    p.add_argument("--apply", action="store_true", help="案をそのまま設定に反映する")

    p = sub.add_parser("profiles", help="シーンプロファイルの記録を Markdown で表示し export/ に保存する")
    p.add_argument("root")

    p = sub.add_parser("dfm-collect", help="品質チェックで問題がなかったフレームから DFM の学習用素材を集める（M8）")
    p.add_argument("root")
    p.add_argument("person_id")
    p.add_argument("qc_ids", nargs="+")
    p.add_argument("--no-dst", action="store_true", help="置き換える前の顔（data_dst）は集めない")

    args = ap.parse_args(argv)

    def say(i, n, text=""):
        print(f"\r{text} {i}/{n}" if n else f"\r{text}", end="", file=sys.stderr)

    from .project import Project

    if args.cmd == "new":
        proj = Project.create(args.root, args.name)
        _print({"root": str(proj.root), "name": proj.name})
    elif args.cmd == "info":
        proj = Project.open(args.root)
        d = proj.data
        _print({"name": d["name"], "sources": [(s["id"], s["file"]) for s in d["sources"]],
                "persons": [(p["id"], p["name"], len(p["images"])) for p in d["persons"]],
                "clips": [(c["id"], c["file"]) for c in d["clips"]], "tools": d["tools"]})
    elif args.cmd == "probe":
        from .media import check_guide, probe

        info = probe(args.video)
        _print({"media": info.to_dict(), "warnings": [w.to_dict() for w in check_guide(info)]})
    elif args.cmd == "import-video":
        from .media import import_source

        proj = Project.open(args.root)
        rec = import_source(proj, args.video, consent_confirmed=args.i_confirm_consent, copy=not args.no_copy,
                            progress=lambda m: print(m, file=sys.stderr))
        _print(rec)
    elif args.cmd == "convert":
        from .media import convert_source

        proj = Project.open(args.root)
        _print(convert_source(proj, args.source_id, to_cfr=args.cfr, to_sdr=args.sdr))
    elif args.cmd == "scan":
        from .clips import scan_face_events

        proj = Project.open(args.root)
        res = scan_face_events(proj, args.source_id,
                               progress=lambda i, n: print(f"\r{i}/{n}", end="", file=sys.stderr))
        print(file=sys.stderr)
        _print(res["segments"])
    elif args.cmd == "cut":
        from .clips import cut_clip

        proj = Project.open(args.root)
        _print(cut_clip(proj, args.source_id, args.start_frame, args.end_frame, fmt_key=args.format,
                        note=args.note))
    elif args.cmd == "add-person":
        from .face_assets import add_person

        proj = Project.open(args.root)
        _print(add_person(proj, args.name, consent_confirmed=args.i_confirm_ai_generated))
    elif args.cmd == "add-faces":
        from .face_assets import add_images

        proj = Project.open(args.root)
        files: list[str] = []
        for pat in args.images:
            files.extend(sorted(glob.glob(pat)) or [pat])
        add_images(proj, args.person_id, [Path(f) for f in files],
                   progress=lambda i, n, name: print(f"{i}/{n} {name}", file=sys.stderr))
        _print(proj.person(args.person_id)["summary"])
    elif args.cmd == "faces-report":
        from .face_assets import issue_texts

        proj = Project.open(args.root)
        person = proj.person(args.person_id)
        s = proj.settings
        _print({"summary": person["summary"],
                "images": [{"file": i["file"], "angle": i["analysis"].get("face", {}).get("yaw_label"),
                            "issues": issue_texts(i["analysis"], s)} for i in person["images"]]})
    elif args.cmd == "ff-check":
        from .facefusion import check_install

        proj = Project.open(args.root)
        info = check_install(proj)
        proj.save()
        _print({"version": info.version, "facefusion": proj.data["tools"]["facefusion"],
                "face_swapper_models": info.choices.get("--face-swapper-model", []),
                "execution_providers": info.choices.get("--execution-providers", [])})
    elif args.cmd == "assign":
        from .facefusion import set_assignments

        proj = Project.open(args.root)
        items = []
        for it in args.items:
            parts = it.split(":")
            a = {"person_id": parts[0], "reference_frame": int(parts[1]) if len(parts) > 1 and parts[1] else None,
                 "reference_face_position": int(parts[2]) if len(parts) > 2 and parts[2] else 0}
            a["reference_face_distance"] = args.distance if args.distance is not None else \
                proj.settings["ff_reference_face_distance"]
            items.append(a)
        set_assignments(proj, args.clip_id, items)
        _print(proj.clip(args.clip_id)["assignments"])
    elif args.cmd == "ff-run":
        from .facefusion import default_variants, run_variant

        proj = Project.open(args.root)
        variants = default_variants(proj.settings)
        if args.model:
            variants = [{"name": f"ff_{args.model}", "face_swapper_model": args.model}]
        out = []
        for v in variants:
            rec = run_variant(proj, args.clip_id, v, progress=say)
            proj.save()
            out.append(rec)
        print(file=sys.stderr)
        _print(out)
    elif args.cmd == "find-vm":
        from .facefusion import sync_visomaster

        proj = Project.open(args.root)
        recs = sync_visomaster(proj, args.clip_id)
        proj.save()
        _print(recs)
    elif args.cmd == "register":
        from .facefusion import register_render

        proj = Project.open(args.root)
        rec = register_render(proj, args.clip_id, args.file, tool=args.tool, label=args.label)
        proj.save()
        _print(rec)
    elif args.cmd == "compare":
        from .compare import make_comparison, run_one_button

        proj = Project.open(args.root)
        if args.renders:
            res = {"comparison": make_comparison(proj, args.clip_id, render_ids=args.renders, progress=say)}
        else:
            res = run_one_button(proj, args.clip_id, run_facefusion=not args.no_facefusion, progress=say)
        proj.save()
        print(file=sys.stderr)
        _print(res)
    elif args.cmd == "qc":
        from .qc import run_qc

        proj = Project.open(args.root)
        rec = run_qc(proj, args.render_id, progress=say)
        proj.save()
        print(file=sys.stderr)
        _print(rec)
    elif args.cmd == "qc-calibrate":
        from .qc import apply_thresholds, propose_thresholds

        proj = Project.open(args.root)
        props = propose_thresholds(proj, args.qc_ids)
        if args.apply:
            apply_thresholds(proj, props, args.qc_ids)
        _print({"applied": args.apply, "proposals": props})
    elif args.cmd == "profiles":
        from .profiles import export_markdown

        proj = Project.open(args.root)
        md = export_markdown(proj)
        out = proj.root / "export" / "scene_profiles.md"
        out.write_text(md, encoding="utf-8")
        print(md)
    elif args.cmd == "dfm-collect":
        from .dfm import collect

        proj = Project.open(args.root)
        rec = collect(proj, args.person_id, args.qc_ids, include_dst=not args.no_dst, progress=say)
        proj.save()
        print(file=sys.stderr)
        _print(rec)
    return 0


if __name__ == "__main__":
    sys.exit(main())

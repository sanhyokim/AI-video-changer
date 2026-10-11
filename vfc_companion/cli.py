"""コマンドラインでの操作（AI エージェントや一括処理向け）。

例:
  python -m vfc_companion.cli new D:\\vfc\\dance01 --name ダンス1
  python -m vfc_companion.cli probe D:\\撮影\\IMG_0001.MOV
  python -m vfc_companion.cli import-video D:\\vfc\\dance01 D:\\撮影\\IMG_0001.MOV --i-confirm-consent
  python -m vfc_companion.cli scan D:\\vfc\\dance01 src_xxxx
  python -m vfc_companion.cli cut D:\\vfc\\dance01 src_xxxx 600 1200
  python -m vfc_companion.cli add-person D:\\vfc\\dance01 "Aoi" --i-confirm-ai-generated
  python -m vfc_companion.cli add-faces D:\\vfc\\dance01 p_Aoi D:\\faces\\aoi\\*.png
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

    args = ap.parse_args(argv)

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
    return 0


if __name__ == "__main__":
    sys.exit(main())

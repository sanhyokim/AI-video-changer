"""テスト用の偽の FaceFusion。本物の 3.9.1 の --help を返し、headless-run では入力をそのまま出力に写す。

環境変数 FAKE_FF_MODE で動きを変える: ok（初期値） / block（コンテンツ判定で止まる：終了コード 3） / fail
"""
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HELP = HERE.parent / "data" / "ff_help_3.9.1.txt"


def main() -> int:
    args = sys.argv[1:]
    if args == ["--version"]:
        print("FaceFusion 3.9.1")
        return 0
    if args[:1] == ["headless-run"] and "--help" in args:
        print(HELP.read_text(encoding="utf-8"))
        return 0
    if args[:1] != ["headless-run"]:
        return 2
    log = os.environ.get("FAKE_FF_ARGS_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(" ".join(args) + "\n")
    mode = os.environ.get("FAKE_FF_MODE", "ok")
    if mode == "block":
        print("[FACEFUSION.CORE] analysing", flush=True)
        return 3
    if mode == "fail":
        print("something broke", flush=True)
        return 1
    target = args[args.index("--target-path") + 1]
    output = args[args.index("--output-path") + 1]
    for src in args[args.index("--source-paths") + 1:]:
        if src.startswith("--"):
            break
        assert os.path.isfile(src), src
    for i in range(1, 4):
        sys.stdout.write(f"\rprocessing: {i * 33}%|###| {i * 10}/30 [00:01<00:00]")
        sys.stdout.flush()
    print()
    shutil.copyfile(target, output)
    print("[FACEFUSION.CORE] processing to video succeed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""M5: 比較用の FaceFusion を CLI（headless-run）で動かす（仕様 11.3 M5、6.6）。

- 引数は、実行する前に必ず `headless-run --help` の出力と照合する（仕様 M5「--help で確認してから使う」）。
  ヘルプにない引数や選択肢があれば、実行せずにエラーにする。
- FaceFusion のコンテンツ判定で止まった場合は、エラーとして記録し、そのクリップは比較から外す。
  同じクリップを FaceFusion で処理し直すこともしない（仕様 S4。判定を回避しない）。
- 確かめたこと（FaceFusion 3.9.1 のソース）: facefusion/workflows/to_video.py の analyse_video() は、
  コンテンツ判定に当たると終了コード 3 を返す。
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .project import Project, ProjectError, new_id
from .util import now_iso, popen

FF_ENTRY = "facefusion.py"
EXIT_CONTENT_FILTER = 3
HELP_CACHE = "renders/facefusion/_help.txt"

STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_BLOCKED = "blocked_by_content_filter"
STATUS_CANCELLED = "cancelled"

STATUS_TEXT = {
    STATUS_DONE: "完了",
    STATUS_FAILED: "失敗",
    STATUS_BLOCKED: "コンテンツ判定で停止（比較から除外）",
    STATUS_CANCELLED: "中止",
}


class FaceFusionError(RuntimeError):
    pass


class ContentFilterStop(FaceFusionError):
    """FaceFusion のコンテンツ判定で止まった。"""


# =====================================================================
# 場所と起動方法
# =====================================================================
@dataclass
class Install:
    dir: Path
    python_cmd: list[str]
    env: dict[str, str] = field(default_factory=dict)

    @property
    def entry(self) -> Path:
        return self.dir / FF_ENTRY

    def command(self, *args: str) -> list[str]:
        return [*self.python_cmd, FF_ENTRY, *args]


def _home() -> Path:
    return Path(os.environ.get("USERPROFILE") or Path.home())


def candidate_dirs() -> list[Path]:
    """よくあるインストール先。見つからなければ設定で指定してもらう。"""
    h = _home()
    out = []
    env = os.environ.get("VFC_FACEFUSION_DIR")
    if env:
        out.append(Path(env))
    for p in (h / "facefusion", h / "FaceFusion", h / "Documents" / "facefusion", h / "Desktop" / "facefusion",
              Path("C:/facefusion"), Path("C:/FaceFusion"), Path("D:/facefusion")):
        out.append(p)
        out.append(p / "facefusion")
    return out


def find_dir(setting: str) -> Path | None:
    cands = [Path(setting)] if setting else candidate_dirs()
    for d in cands:
        d = d.expanduser()
        if d.is_file() and d.name == FF_ENTRY:
            d = d.parent
        if (d / FF_ENTRY).is_file():
            return d.resolve()
    return None


def candidate_pythons(ff_dir: Path | None) -> list[Path]:
    """conda の facefusion 環境や、FaceFusion のフォルダの仮想環境にある Python。"""
    h = _home()
    exe = "python.exe" if os.name == "nt" else "python"
    sub = "" if os.name == "nt" else "bin"
    roots = []
    for base in ("miniconda3", "anaconda3", "Miniconda3", "Anaconda3", "miniforge3", "mambaforge"):
        roots.append(h / base / "envs" / "facefusion")
        roots.append(Path("C:/ProgramData") / base / "envs" / "facefusion")
    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix:
        roots.append(Path(conda_prefix).parent / "facefusion")
    if ff_dir:
        for name in (".venv", "venv", "env"):
            roots.append(ff_dir / name)
        roots.append(ff_dir.parent / "envs" / "facefusion")
    out = []
    for r in roots:
        if os.name == "nt":
            out += [r / exe, r / "Scripts" / exe]
        else:
            out.append(r / sub / exe)
    return out


def conda_env(python_exe: Path) -> dict[str, str]:
    """conda activate の代わりに、環境の DLL の場所を PATH に足す。

    conda の環境の python.exe を直接起動すると、CUDA などの DLL（環境の Library\\bin）が
    見つからないことがある。FaceFusion 自身も CONDA_PREFIX を見て PATH を足す（facefusion/conda.py）。
    """
    env = dict(os.environ)
    if os.name == "nt":
        prefix = python_exe.parent if python_exe.parent.name.lower() != "scripts" else python_exe.parent.parent
        extra = [prefix, prefix / "Library" / "mingw-w64" / "bin", prefix / "Library" / "usr" / "bin",
                 prefix / "Library" / "bin", prefix / "Scripts"]
    else:
        prefix = python_exe.parent.parent
        extra = [prefix / "bin"]
    if (prefix / "conda-meta").is_dir():
        env["CONDA_PREFIX"] = str(prefix)
        env["CONDA_DEFAULT_ENV"] = prefix.name
    env["PATH"] = os.pathsep.join([str(p) for p in extra if p.exists()] + [env.get("PATH", "")])
    return env


def locate(settings: dict[str, Any]) -> Install:
    ff_dir = find_dir(str(settings.get("facefusion_dir") or ""))
    if ff_dir is None:
        raise FaceFusionError(
            "FaceFusion の場所が見つかりません。facefusion.py のあるフォルダを設定（facefusion_dir）で指定してください。")
    py = str(settings.get("facefusion_python") or "").strip()
    env = dict(os.environ)
    if py:
        p = Path(py.strip('"'))
        if p.is_file():
            cmd = [str(p)]
            env = conda_env(p)
        else:
            cmd = shlex.split(py, posix=(os.name != "nt"))
            cmd = [c.strip('"') for c in cmd]
    else:
        found = next((p for p in candidate_pythons(ff_dir) if p.is_file()), None)
        if found is None:
            raise FaceFusionError(
                "FaceFusion を動かす Python が見つかりません。設定 facefusion_python に、conda の facefusion 環境の "
                "python.exe のパス（例: C:\\Users\\user\\miniconda3\\envs\\facefusion\\python.exe）か、"
                "「conda run --no-capture-output -n facefusion python」を入れてください。")
        cmd = [str(found)]
        env = conda_env(found)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.setdefault("COLUMNS", "200")
    return Install(dir=ff_dir, python_cmd=cmd, env=env)


def _run_ff(inst: Install, *args: str, timeout: float = 300) -> subprocess.CompletedProcess:
    return subprocess.run(inst.command(*args), cwd=str(inst.dir), env=inst.env, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=timeout,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0)


# =====================================================================
# --help の読み取りと照合
# =====================================================================
@dataclass
class HelpInfo:
    text: str
    version: str = ""
    flags: set[str] = field(default_factory=set)
    choices: dict[str, list[str]] = field(default_factory=dict)
    ranges: dict[str, tuple[float, float]] = field(default_factory=dict)

    def has(self, flag: str) -> bool:
        return flag in self.flags


_FLAG = re.compile(r"(?<![\w-])(--[a-z0-9][a-z0-9-]*)")
_USAGE_OPT = re.compile(r"\[(--[a-z0-9-]+)\s+(\{[^}]*\}|\[[^\]]*\]|[^\[\]]+?)\]")
_RANGE = re.compile(r"^\[?(-?[\d.]+)\.\.(-?[\d.]+):[\d.]+\]?$")


def parse_help(text: str, version: str = "") -> HelpInfo:
    """argparse の出力から、引数と選択肢と範囲を読み取る。

    使い方の行（usage）の [--flag {a,b,c}] と、説明の「(choices: a, b, ...)」の両方を読む。
    説明の折り返しで「upper-\\n face」のように語が割れるので、つなぎ直してから読む。
    """
    info = HelpInfo(text=text, version=version)
    info.flags = set(_FLAG.findall(text))
    usage, _, body = text.partition("\noptions:")
    usage = " ".join(usage.split())
    for flag, meta in _USAGE_OPT.findall(usage):
        meta = meta.strip()
        if meta.startswith("{") and meta.endswith("}"):
            info.choices[flag] = [c for c in meta[1:-1].split(",") if c]
        else:
            m = _RANGE.match(meta)
            if m:
                info.ranges[flag] = (float(m.group(1)), float(m.group(2)))
    # 説明の部分を引数ごとに分ける
    blocks: dict[str, str] = {}
    current = None
    for line in body.splitlines():
        stripped = line.strip()
        m = re.match(r"^\s{2}(?:-\w,\s)?(--[a-z0-9-]+)", line)
        if m:
            current = m.group(1)
            blocks[current] = stripped
        elif current and stripped:
            blocks[current] += " " + stripped
        elif not stripped:
            current = None
    for flag, block in blocks.items():
        joined = re.sub(r"-\s+", "-", " ".join(block.split()))
        m = re.search(r"\(choices:\s*([^)]*)\)", joined)
        if m and flag not in info.choices:
            vals = [v.strip() for v in m.group(1).split(",")]
            info.choices[flag] = [v for v in vals if v and v != "..."]
    return info


def validate_args(args: list[str], info: HelpInfo) -> list[str]:
    """ヘルプにない引数、選択肢にない値、範囲外の値を見つける。"""
    problems = []
    i = 0
    while i < len(args):
        a = args[i]
        if not a.startswith("--"):
            i += 1
            continue
        vals = []
        j = i + 1
        while j < len(args) and not args[j].startswith("--"):
            vals.append(args[j])
            j += 1
        if not info.has(a):
            problems.append(f"{a} は、この FaceFusion のヘルプにありません")
        elif a in info.choices:
            # FaceFusion の説明は「(choices: cpu, cuda, ...)」の形で、使える値がすべて並ぶ
            listed = info.choices[a]
            for v in vals:
                if v not in listed:
                    problems.append(f"{a} {v}：選べる値は {', '.join(listed)}")
        elif a in info.ranges:
            lo, hi = info.ranges[a]
            for v in vals:
                try:
                    if not lo <= float(v) <= hi:
                        problems.append(f"{a} {v}：範囲は {lo:g}〜{hi:g}")
                except ValueError:
                    problems.append(f"{a} {v}：数値ではありません")
        i = j
    return problems


def probe(inst: Install, cache_path: Path | None = None) -> HelpInfo:
    """`--version` と `headless-run --help` を実行して読み取る。"""
    try:
        v = _run_ff(inst, "--version", timeout=180)
    except FileNotFoundError as e:
        raise FaceFusionError(f"FaceFusion を起動できません: {e}") from e
    vtext = v.stdout.decode("utf-8", "replace").strip()
    if v.returncode != 0 or "FaceFusion" not in vtext:
        raise FaceFusionError("FaceFusion を起動できませんでした。\n\n" + vtext[-2000:])
    h = _run_ff(inst, "headless-run", "--help", timeout=300)
    htext = h.stdout.decode("utf-8", "replace")
    if h.returncode != 0 or "--face-swapper-model" not in htext:
        raise FaceFusionError("headless-run --help を読めませんでした。\n\n" + htext[-2000:])
    version = vtext.splitlines()[-1].replace("FaceFusion", "").strip()
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(f"# FaceFusion {version} ({now_iso()})\n" + htext, encoding="utf-8")
    return parse_help(htext, version)


def check_install(project: Project) -> HelpInfo:
    """FaceFusion を確かめ、バージョンをプロジェクトに記録する。"""
    inst = locate(project.settings)
    info = probe(inst, project.root / HELP_CACHE)
    tools = project.data["tools"].setdefault("facefusion", {})
    tools.update({"version": info.version, "dir": str(inst.dir), "python": " ".join(inst.python_cmd),
                  "checked_at": now_iso()})
    project.log("facefusion_checked", {"version": info.version, "dir": str(inst.dir)})
    return info


def resolve_providers(setting: str, info: HelpInfo) -> list[str]:
    setting = (setting or "auto").strip()
    if setting != "auto":
        return [p.strip() for p in setting.replace(",", " ").split() if p.strip()]
    listed = info.choices.get("--execution-providers", [])
    return ["cuda"] if "cuda" in listed else (listed[:1] or ["cpu"])


# =====================================================================
# 置き換える人物の割り当て（複数人のクリップ）
# =====================================================================
def default_variants(settings: dict[str, Any]) -> list[dict[str, str]]:
    models = [m.strip() for m in str(settings["ff_models"]).split(",") if m.strip()]
    return [{"name": f"ff_{m}", "face_swapper_model": m} for m in models]


def find_reference_frame(project: Project, clip: dict[str, Any], max_frames: int | None = None) -> int | None:
    """クリップの中で、最初に顔が見つかるフレーム。"""
    from .faces import detect
    from .frames import iter_frames
    from .media import probe as probe_media

    path = project.abs(clip["file"])
    mi = probe_media(path, project.data.get("settings"), measure_luma=False)
    step = max(1, int(round(mi.fps / 10)))
    for n, img in iter_frames(path, mi.fps, mi.display_width, mi.display_height, step=step, max_width=1280,
                              max_frames=max_frames):
        if detect(img, 0.5):
            return n
    return None


def reference_faces(project: Project, clip: dict[str, Any], frame: int, order: str = "large-small",
                    max_width: int = 1280) -> tuple[Any, list[dict[str, Any]]]:
    """基準フレームの画像と、FaceFusion と同じ順に並べた顔。リストの番号が --reference-face-position になる。"""
    from .faces import detect
    from .frames import read_frame
    from .media import probe as probe_media
    from .qc import sort_by_order

    path = project.abs(clip["file"])
    mi = probe_media(path, project.data.get("settings"), measure_luma=False)
    img = read_frame(path, frame, mi.fps, mi.display_width, mi.display_height, max_width=max_width)
    if img is None:
        raise ProjectError(f"フレーム {frame} を読めませんでした。")
    faces = [{"bbox": f.bbox, "score": f.det_score} for f in detect(img, 0.5)]
    return img, sort_by_order(faces, order)


def clip_assignments(project: Project, clip: dict[str, Any]) -> list[dict[str, Any]]:
    """クリップで置き換える人物の一覧。登録がなく、人物が 1 人だけなら自動で決める。"""
    s = project.settings
    explicit = clip.get("assignments") or []
    if explicit:
        return explicit
    persons = project.data["persons"]
    if len(persons) != 1:
        raise ProjectError("このクリップで置き換える人物が決まっていません。比較の画面で人物を割り当ててください。")
    return [{"person_id": persons[0]["id"], "reference_frame": None, "reference_face_position": 0,
             "reference_face_distance": float(s["ff_reference_face_distance"]),
             "face_selector_order": s["ff_face_selector_order"], "face_selector_gender": "", "auto": True}]


def set_assignments(project: Project, clip_id: str, assignments: list[dict[str, Any]]) -> None:
    clip = project.clip(clip_id)
    clean = []
    for a in assignments:
        project.person(a["person_id"])
        clean.append({
            "person_id": a["person_id"],
            "reference_frame": None if a.get("reference_frame") is None else int(a["reference_frame"]),
            "reference_face_position": int(a.get("reference_face_position", 0)),
            "reference_face_distance": float(a.get("reference_face_distance", 0.3)),
            "face_selector_order": a.get("face_selector_order") or "large-small",
            "face_selector_gender": a.get("face_selector_gender") or "",
        })
    clip["assignments"] = clean
    project.log("clip_assignments_set", {"clip_id": clip_id, "persons": [a["person_id"] for a in clean]})


def person_images(project: Project, person_id: str) -> list[Path]:
    """FaceFusion に渡す顔写真。検査で顔が見つからない・複数写っている写真は外す。"""
    person = project.person(person_id)
    out = []
    for img in person["images"]:
        issues = img.get("analysis", {}).get("issues", [])
        if "no_face" in issues or "multi_face" in issues or "unreadable" in issues:
            continue
        out.append(project.abs(img["file"]))
    if not out:
        raise ProjectError(f"{person['name']} に使える顔写真がありません。顔素材の画面で写真を追加してください。")
    return out


# =====================================================================
# 実行
# =====================================================================
def build_args(settings: dict[str, Any], info: HelpInfo, variant: dict[str, str], assignment: dict[str, Any],
               sources: list[Path], target: Path, output: Path) -> list[str]:
    s = settings
    procs = [p.strip() for p in str(s["ff_processors"]).split(",") if p.strip()]
    args = ["--source-paths", *map(str, sources), "--target-path", str(target), "--output-path", str(output),
            "--processors", *procs,
            "--face-swapper-model", variant["face_swapper_model"],
            "--face-landmarker-model", str(s["ff_face_landmarker_model"]),
            "--face-selector-mode", "reference",
            "--reference-frame-number", str(int(assignment.get("reference_frame") or 0)),
            "--reference-face-position", str(int(assignment.get("reference_face_position") or 0)),
            "--reference-face-distance", f"{float(assignment.get('reference_face_distance', 0.3)):g}",
            "--face-selector-order", assignment.get("face_selector_order") or "large-small"]
    if assignment.get("face_selector_gender"):
        args += ["--face-selector-gender", assignment["face_selector_gender"]]
    if s.get("ff_pixel_boost"):
        args += ["--face-swapper-pixel-boost", str(s["ff_pixel_boost"])]
    if "expression_restorer" in procs:
        args += ["--expression-restorer-factor", str(int(s["ff_expression_restorer_factor"]))]
    if "face_enhancer" in procs:
        args += ["--face-enhancer-model", str(s["ff_face_enhancer_model"]),
                 "--face-enhancer-blend", str(int(s["ff_face_enhancer_blend"]))]
    args += ["--execution-providers", *resolve_providers(str(s["ff_execution_providers"]), info),
             "--output-video-quality", str(int(s["ff_output_video_quality"])),
             "--log-level", "info"]
    return args


_PROGRESS = re.compile(r"(\d+)/(\d+)\s*\[")


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        try:
            os.killpg(proc.pid, 9)
        except OSError:
            proc.kill()
    proc.wait()


def run_headless(inst: Install, args: list[str], log_path: Path, *,
                 progress: Callable[[int, int, str], None] | None = None,
                 should_stop: Callable[[], bool] | None = None, label: str = "") -> int:
    """headless-run を実行し、出力をログに書く。戻り値は終了コード。中止したら -1。"""
    cmd = inst.command("headless-run", *args)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    kw: dict[str, Any] = {"cwd": str(inst.dir), "env": inst.env, "stdout": subprocess.PIPE,
                          "stderr": subprocess.STDOUT}
    if os.name != "nt":
        kw["start_new_session"] = True
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(f"$ {subprocess.list2cmdline(cmd)}\n")
        log.flush()
        proc = popen(cmd, **kw)
        buf = b""
        try:
            while True:
                if should_stop and should_stop():
                    _kill_tree(proc)
                    log.write("\n[中止しました]\n")
                    return -1
                chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
                if not chunk:
                    break
                buf += chunk
                parts = re.split(rb"[\r\n]", buf)
                buf = parts.pop()
                for p in parts:
                    line = p.decode("utf-8", "replace").strip()
                    if not line:
                        continue
                    log.write(line + "\n")
                    m = _PROGRESS.search(line)
                    if m and progress:
                        progress(int(m.group(1)), int(m.group(2)), f"{label} {line[:80]}")
            if buf:
                log.write(buf.decode("utf-8", "replace") + "\n")
            rc = proc.wait()
        finally:
            if proc.poll() is None:
                _kill_tree(proc)
        log.write(f"[終了コード {rc}]\n")
    return rc


def _ascii_stage(paths: list[Path], stage: Path) -> list[Path]:
    """Windows で日本語のパスを避けるため、ASCII だけの作業フォルダにリンクかコピーを置く。"""
    stage.mkdir(parents=True, exist_ok=True)
    out = []
    for i, p in enumerate(paths):
        dest = stage / f"{i:03d}{p.suffix.lower()}"
        if not dest.exists():
            try:
                os.link(p, dest)
            except OSError:
                shutil.copy2(p, dest)
        out.append(dest)
    return out


def _needs_stage(paths: list[Path]) -> bool:
    return any(not str(p).isascii() for p in paths)


def staging_root() -> Path:
    import tempfile

    base = Path(os.environ.get("VFC_STAGE_DIR") or tempfile.gettempdir())
    if not str(base).isascii():
        base = Path("C:/vfc_tmp") if os.name == "nt" else Path("/tmp/vfc_tmp")
    return base / "vfc_ff"


def run_variant(project: Project, clip_id: str, variant: dict[str, str], *, info: HelpInfo | None = None,
                inst: Install | None = None, progress: Callable[[int, int, str], None] | None = None,
                should_stop: Callable[[], bool] | None = None, force_stage: bool = False) -> dict[str, Any]:
    """1 つの設定で FaceFusion を実行し、renders に記録する。2 人以上なら順番に重ねて処理する。"""
    clip = project.clip(clip_id)
    if clip.get("facefusion_blocked"):
        raise ContentFilterStop(
            "このクリップは以前 FaceFusion のコンテンツ判定で止まったため、FaceFusion では処理しません（仕様 S4）。")
    s = project.settings
    inst = inst or locate(s)
    info = info or probe(inst, project.root / HELP_CACHE)
    assigns = clip_assignments(project, clip)
    say = progress or (lambda *_a: None)

    for a in assigns:
        if a.get("reference_frame") is None:
            say(0, 0, "基準の顔を探しています")
            ref = find_reference_frame(project, clip)
            if ref is None:
                raise ProjectError("クリップの中に顔が見つかりません。")
            a["reference_frame"] = ref

    rid = new_id("r")
    out_dir = project.root / "renders" / "facefusion" / clip_id
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / f"{rid}_{variant['face_swapper_model']}.mp4"
    log_path = out_dir / f"{rid}.log"
    target = project.abs(clip["file"])
    stage_dir = staging_root() / rid
    use_stage = force_stage or _needs_stage([target, final, inst.dir])
    rec: dict[str, Any] = {
        "id": rid, "tool": "facefusion", "clip_id": clip_id, "variant": variant,
        "label": f"FaceFusion {variant['face_swapper_model']}",
        "assignments": [dict(a) for a in assigns], "file": project.rel(final), "log": project.rel(log_path),
        "facefusion_version": info.version, "status": None, "started_at": now_iso(), "commands": [],
    }
    steps = []
    try:
        current = target
        for k, a in enumerate(assigns):
            sources = person_images(project, a["person_id"])
            is_last = k == len(assigns) - 1
            out = final if is_last else out_dir / f"{rid}_step{k + 1}.mp4"
            run_target, run_out, run_sources = current, out, sources
            if use_stage:
                run_target = _ascii_stage([current], stage_dir / f"t{k}")[0]
                run_sources = _ascii_stage(sources, stage_dir / f"s{k}")
                run_out = stage_dir / f"out{k}.mp4"
            args = build_args(s, info, variant, a, run_sources, run_target, run_out)
            problems = validate_args(args, info)
            if problems:
                raise FaceFusionError("FaceFusion のヘルプと合わない引数があるため、実行しませんでした。\n\n"
                                      + "\n".join(problems))
            rec["commands"].append(inst.command("headless-run", *args))
            label = f"{variant['face_swapper_model']}（{k + 1}/{len(assigns)} 人目）"
            say(0, 0, f"FaceFusion を実行しています {label}")
            rc = run_headless(inst, args, log_path, progress=say, should_stop=should_stop, label=label)
            if rc == -1:
                rec["status"] = STATUS_CANCELLED
                break
            if rc == EXIT_CONTENT_FILTER:
                rec["status"] = STATUS_BLOCKED
                rec["error"] = ("FaceFusion のコンテンツ判定で処理が止まりました。仕様 S4 に従い、判定を回避せず、"
                                "このクリップは FaceFusion の比較から外します。")
                clip["facefusion_blocked"] = {"at": now_iso(), "render_id": rid}
                break
            if rc != 0 or not run_out.exists():
                tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]
                rec["status"] = STATUS_FAILED
                rec["error"] = f"FaceFusion が失敗しました（終了コード {rc}）。\n" + "\n".join(tail)
                break
            if use_stage:
                shutil.move(str(run_out), str(out))
            steps.append(out)
            current = out
        else:
            rec["status"] = STATUS_DONE
    finally:
        rec["finished_at"] = now_iso()
        if use_stage:
            shutil.rmtree(stage_dir, ignore_errors=True)
        for p in steps[:-1] if rec["status"] == STATUS_DONE else []:
            p.unlink(missing_ok=True)
        if rec["status"] is None:
            rec["status"] = STATUS_FAILED
            rec.setdefault("error", "途中で例外が起きました")
        project.data.setdefault("renders", []).append(rec)
        project.log("facefusion_run", {"id": rid, "clip_id": clip_id, "model": variant["face_swapper_model"],
                                       "status": rec["status"]})
    return rec


# =====================================================================
# VisoMaster Fusion の出力を見つける
# =====================================================================
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}


def find_visomaster_outputs(project: Project, clip_id: str | None = None) -> list[dict[str, Any]]:
    """renders/visomaster/ に置かれた動画を探し、どのクリップの出力かを推定する。

    書き出しのバージョン（add_render_version）で作ったフォルダに置いたものは、そのバージョンのクリップとみなす。
    それ以外は、ファイル名にクリップの ID か、クリップのファイル名（拡張子なし）が含まれていれば一致とみなす。
    名前で決まらないものは、フレーム数が同じクリップを候補にする。
    """
    from .media import count_frames

    root = project.root / "renders" / "visomaster"
    known = {r["file"]: r for r in project.data.get("renders", []) if r.get("tool") == "visomaster"}
    clips = project.data["clips"] if clip_id is None else [project.clip(clip_id)]
    clip_ids = {c["id"] for c in clips}
    version_dirs = {v["folders"]["visomaster"]: v["clip_id"] for v in project.data.get("render_versions", [])
                    if v.get("clip_id") and v.get("folders", {}).get("visomaster")}
    out = []
    for f in sorted(root.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in VIDEO_EXTS:
            continue
        rel = project.rel(f)
        if rel in known and known[rel].get("clip_id"):
            if clip_id is None or known[rel]["clip_id"] == clip_id:
                out.append({"file": rel, "clip_id": known[rel]["clip_id"], "match": "registered",
                            "render_id": known[rel]["id"]})
            continue
        name = f.stem.lower()
        hit = None
        vclip = version_dirs.get(project.rel(f.parent))
        if vclip:
            if vclip in clip_ids:
                out.append({"file": rel, "clip_id": vclip, "match": "version", "render_id": None})
            continue
        for c in clips:
            if c["id"].lower() in name or Path(c["file"]).stem.lower() in name:
                hit = (c["id"], "name")
                break
        if hit is None:
            n = count_frames(f)
            for c in clips:
                if n is not None and n == c["frame_count"]:
                    hit = (c["id"], "frame_count")
                    break
        if hit:
            out.append({"file": rel, "clip_id": hit[0], "match": hit[1], "render_id": None})
    return out


def register_render(project: Project, clip_id: str, path: str | Path, *, tool: str = "visomaster",
                    label: str = "", note: str = "") -> dict[str, Any]:
    """VisoMaster Fusion などの出力を、クリップの結果として登録する。"""
    project.clip(clip_id)
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(p)
    rel = project.rel(p)
    for r in project.data.setdefault("renders", []):
        if r["file"] == rel:
            r["clip_id"] = clip_id
            if label:
                r["label"] = label
            project.log("render_updated", {"id": r["id"], "clip_id": clip_id})
            return r
    if not label:
        label = f"VisoMaster {p.stem}" if tool == "visomaster" else p.stem
    rec = {"id": new_id("r"), "tool": tool, "clip_id": clip_id, "file": rel, "label": label,
           "status": STATUS_DONE, "registered_at": now_iso(), "note": note}
    project.data["renders"].append(rec)
    project.log("render_registered", {"id": rec["id"], "clip_id": clip_id, "tool": tool, "file": rel})
    return rec


def sync_visomaster(project: Project, clip_id: str | None = None) -> list[dict[str, Any]]:
    """見つけた VisoMaster Fusion の出力を、まだ登録していなければ登録する。"""
    recs = []
    for hit in find_visomaster_outputs(project, clip_id):
        if hit["render_id"]:
            recs.append(next(r for r in project.data["renders"] if r["id"] == hit["render_id"]))
        else:
            r = register_render(project, hit["clip_id"], project.abs(hit["file"]), tool="visomaster")
            r["match"] = hit["match"]
            recs.append(r)
    return recs


def renders_for_clip(project: Project, clip_id: str, *, usable_only: bool = True) -> list[dict[str, Any]]:
    out = []
    for r in project.data.get("renders", []):
        if r.get("clip_id") != clip_id:
            continue
        if usable_only and (r.get("status") != STATUS_DONE or not project.abs(r["file"]).exists()):
            continue
        out.append(r)
    return out


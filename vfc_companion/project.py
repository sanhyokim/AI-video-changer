"""M1: プロジェクト管理（仕様 11.2、11.3 M1）。

project.json にプロジェクトの設定、使ったツールのバージョン、素材、顔素材、
テスト用クリップ、処理の履歴を記録する。パスはすべてプロジェクトのフォルダからの
相対パスで保存するので、フォルダごと移動しても開ける。
"""

from __future__ import annotations

import platform
import uuid
from pathlib import Path
from typing import Any

from . import SPEC_VERSION, __version__
from .config import merged_settings
from .consent import ConsentLog
from .util import now_iso, python_version, read_json, tool_version, write_json_atomic

PROJECT_FILE = "project.json"
CONSENT_FILE = "consent_log.json"
SCHEMA_VERSION = 1

# 仕様 11.2 のフォルダ構成
FOLDERS = [
    "sources",
    "faces",
    "clips",
    "renders/visomaster",
    "renders/facefusion",
    "compare",
    "qc",
    "dfm_dataset",
    "export",
]


class ProjectError(RuntimeError):
    pass


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _detect_tools() -> dict[str, Any]:
    try:
        import insightface  # noqa: F401
        insightface_ver = getattr(insightface, "__version__", "unknown")
    except Exception:
        insightface_ver = None
    try:
        import onnxruntime
        ort_ver = onnxruntime.__version__
    except Exception:
        ort_ver = None
    return {
        "companion": __version__,
        "python": python_version(),
        "os": platform.platform(),
        "ffmpeg": tool_version("ffmpeg"),
        "ffprobe": tool_version("ffprobe"),
        "insightface": insightface_ver,
        "onnxruntime": ort_ver,
        "face_model": "buffalo_l",
        "detected_at": now_iso(),
    }


class Project:
    def __init__(self, root: Path, data: dict[str, Any]):
        self.root = Path(root).resolve()
        self.data = data
        self.consent = ConsentLog(self.root / CONSENT_FILE)

    # ---------- 作成と読み込み ----------
    @classmethod
    def create(cls, root: str | Path, name: str | None = None) -> "Project":
        root = Path(root)
        if (root / PROJECT_FILE).exists():
            raise ProjectError(f"すでにプロジェクトがあります: {root / PROJECT_FILE}")
        root.mkdir(parents=True, exist_ok=True)
        for sub in FOLDERS:
            (root / sub).mkdir(parents=True, exist_ok=True)
        ts = now_iso()
        data: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "spec_version": SPEC_VERSION,
            "name": name or root.name,
            "created_at": ts,
            "updated_at": ts,
            "settings": {},
            "tools": {
                "detected": _detect_tools(),
                # ユーザーが手で記入する欄（VisoMaster Fusion の内部は読まない）
                "visomaster_fusion": {"version": "", "swapper_model": "", "notes": ""},
                "facefusion": {"version": "", "notes": ""},
                "davinci_resolve": {"version": "", "notes": ""},
            },
            "sources": [],
            "persons": [],
            "clips": [],
            "render_versions": [],
            "renders": [],
            "comparisons": [],
            "qc_reports": [],
            "scene_profiles": {},
            "dfm_sets": [],
            "history": [],
        }
        proj = cls(root, data)
        proj.log("project_created", {"name": data["name"]})
        return proj

    @classmethod
    def open(cls, root: str | Path) -> "Project":
        root = Path(root)
        if root.is_file() and root.name == PROJECT_FILE:
            root = root.parent
        pf = root / PROJECT_FILE
        if not pf.exists():
            raise ProjectError(f"project.json が見つかりません: {root}")
        data = read_json(pf)
        if data.get("schema_version", 0) > SCHEMA_VERSION:
            raise ProjectError("このプロジェクトは新しい版のツールで作られています。ツールを更新してください。")
        # 足りないフォルダを作り直す（手で消した場合など）
        for sub in FOLDERS:
            (root / sub).mkdir(parents=True, exist_ok=True)
        for key in ("sources", "persons", "clips", "render_versions", "renders", "comparisons", "qc_reports",
                    "dfm_sets", "history"):
            data.setdefault(key, [])
        data.setdefault("settings", {})
        data.setdefault("scene_profiles", {})
        return cls(root, data)

    # ---------- 保存と記録 ----------
    def save(self) -> None:
        self.data["updated_at"] = now_iso()
        write_json_atomic(self.root / PROJECT_FILE, self.data)

    def log(self, action: str, detail: dict[str, Any] | None = None, *, save: bool = True) -> None:
        self.data["history"].append({"at": now_iso(), "action": action, "detail": detail or {}})
        if save:
            self.save()

    def refresh_tool_versions(self) -> None:
        self.data["tools"]["detected"] = _detect_tools()
        self.log("tool_versions_refreshed")

    def set_manual_tool_version(self, tool: str, **fields: str) -> None:
        entry = self.data["tools"].setdefault(tool, {})
        entry.update(fields)
        self.log("tool_version_set", {"tool": tool, **fields})

    # ---------- パス ----------
    @property
    def name(self) -> str:
        return self.data["name"]

    @property
    def settings(self) -> dict[str, Any]:
        return merged_settings(self.data.get("settings"))

    def set_setting(self, key: str, value: Any) -> None:
        self.data["settings"][key] = value
        self.log("setting_changed", {"key": key, "value": value})

    def abs(self, rel: str | Path) -> Path:
        return (self.root / rel).resolve()

    def rel(self, path: str | Path) -> str:
        p = Path(path).resolve()
        try:
            return p.relative_to(self.root).as_posix()
        except ValueError:
            return str(p)

    # ---------- 検索 ----------
    def source(self, source_id: str) -> dict[str, Any]:
        for s in self.data["sources"]:
            if s["id"] == source_id:
                return s
        raise ProjectError(f"素材が見つかりません: {source_id}")

    def person(self, person_id: str) -> dict[str, Any]:
        for p in self.data["persons"]:
            if p["id"] == person_id:
                return p
        raise ProjectError(f"人物が見つかりません: {person_id}")

    def clip(self, clip_id: str) -> dict[str, Any]:
        for c in self.data["clips"]:
            if c["id"] == clip_id:
                return c
        raise ProjectError(f"クリップが見つかりません: {clip_id}")

    def render(self, render_id: str) -> dict[str, Any]:
        for r in self.data["renders"]:
            if r["id"] == render_id:
                return r
        raise ProjectError(f"出力が見つかりません: {render_id}")

    # ---------- 書き出しのバージョン（仕様 11.2 の最後の段落） ----------
    def add_render_version(self, *, source_id: str | None = None, clip_id: str | None = None,
                           person_ids: list[str] | None = None, settings_note: str = "") -> dict[str, Any]:
        """1 本の元動画から違う顔のバージョンを作るときの組み合わせを記録し、出力先を用意する。"""
        if not source_id and not clip_id:
            raise ProjectError("source_id か clip_id を指定してください")
        if clip_id:
            self.clip(clip_id)
        if source_id:
            self.source(source_id)
        for pid in person_ids or []:
            self.person(pid)
        vid = new_id("v")
        folders = {}
        for tool in ("visomaster", "facefusion"):
            d = self.root / "renders" / tool / vid
            d.mkdir(parents=True, exist_ok=True)
            folders[tool] = self.rel(d)
        rec = {
            "id": vid,
            "source_id": source_id,
            "clip_id": clip_id,
            "person_ids": person_ids or [],
            "settings_note": settings_note,
            "folders": folders,
            "created_at": now_iso(),
        }
        self.data["render_versions"].append(rec)
        self.log("render_version_added", {"id": vid})
        return rec

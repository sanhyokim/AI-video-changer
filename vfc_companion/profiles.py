"""シーンプロファイル（仕様 10 章、フェーズ 2 の「プロファイルごとの設定を決める」）。

VisoMaster Fusion の設定は、このツールからは操作しない（仕様 8 章、11.4）。ここでは、
比較動画と品質レポートを見てユーザーが決めた設定を、プロファイルごとに記録するだけにする。

項目名は VisoMaster Fusion v3.12.3 のソース（app/ui/widgets/*_layout_data.py の "label"）から取った。
実際の画面の表記と一致するかは未確認（C5）。
"""

from __future__ import annotations

from typing import Any

from .project import Project, ProjectError
from .util import now_iso

# 10.1 の基本設定。値は仕様の推奨。（初期値）はソースで見た VisoMaster Fusion の初期値。
BASE_SETTINGS: list[tuple[str, str]] = [
    ("Swapper Model", "AlphaFace"),
    ("Swapper Resolution", "調整は 256、本番は 512"),
    ("Secondary Swapper", "混ぜない場合と比べる（Blend Amount を変えて比べる）"),
    ("Enable Landmark Detection", "オン（初期値はオフ）"),
    ("Landmark Detect Model", "tufa98（傾く）／orformer98（隠れる）。初期値は 203"),
    ("Enable Face Tracking (ByteTrack)", "オン（初期値はオフ）"),
    ("Track Buffer (Frames)", "動きの多い動画では長め（初期値 30）"),
    ("Enable Face Expression Restorer", "オン（初期値はオフ）。Mode を Advanced にして目・眉・唇を戻す"),
    ("Restore Eyes / Restore Mouth", "オン"),
    ("Mouth Fit & Align", "オン"),
    ("Occlusion Mask / DFL XSeg Mask / Face Parser Mask", "オン。横顔には Profile Angle Mask"),
    ("Transfer Texture / AutoColor Transfer", "オン（肌の質感と照明のなじみ）"),
    ("Enable Face Restorer", "使うなら Blend を弱く（強いと表情と汗が消える）"),
    ("Number of Streams (High VRAM usage)", "1 から（VRAM 10GB）"),
]

PROFILES: list[dict[str, Any]] = [
    {"id": "P1", "name": "表情が激しい", "example": "力む、苦しむ、大笑い、叫ぶ",
     "focus": "表情を戻す機能を強める。口元・目元の復元をオン。顔の補正は弱め",
     "settings": [("Enable Face Expression Restorer", "オン、Mode: Advanced"),
                  ("Expression Factor（目・眉・唇）", "1.0 から上げて比べる"),
                  ("Restore Eyes / Restore Mouth", "オン"),
                  ("Enable Face Restorer", "オフか、Blend を弱く")]},
    {"id": "P2", "name": "全身・速い動き", "example": "ダンス、スポーツ、引きの画",
     "focus": "512 モード、Track Buffer を長め、横顔用マスク、AutoColor。仕上げで顔にブレを足す",
     "settings": [("Swapper Resolution", "512"), ("Track Buffer (Frames)", "30 より長く"),
                  ("Profile Angle Mask", "オン"), ("AutoColor Transfer", "オン")]},
    {"id": "P3", "name": "顔が隠れる", "example": "腕、道具、髪が顔にかかる",
     "focus": "orformer98、遮蔽物マスク＋XSeg＋顔のパーツ分け",
     "settings": [("Landmark Detect Model", "orformer98"), ("Occlusion Mask", "オン"),
                  ("DFL XSeg Mask", "オン"), ("Face Parser Mask", "オン")]},
    {"id": "P4", "name": "顔が大きく傾く", "example": "のけぞる、うつむく、寝転ぶ",
     "focus": "tufa98。斜め、見上げ、見下ろしの顔素材を増やす",
     "settings": [("Landmark Detect Model", "tufa98"), ("顔素材", "見上げ・見下ろし・斜めを追加")]},
    {"id": "P5", "name": "会話・正面", "example": "話す、説明する",
     "focus": "標準設定。Mouth Fit & Align",
     "settings": [("Mouth Fit & Align", "オン")]},
    {"id": "P6", "name": "複数人", "example": "2 人以上が映る",
     "focus": "人物ごとの割り当て、ByteTrack、入れ替わる場面の確認",
     "settings": [("Enable Face Tracking (ByteTrack)", "オン"), ("Similarity Threshold", "似た 2 人なら厳しく（初期値 60）")]},
    {"id": "P7", "name": "照明が変わる", "example": "ステージ照明、カラーライト、屋外と屋内の切り替え",
     "focus": "AutoColor、Texture Transfer。仕上げで色合わせ",
     "settings": [("AutoColor Transfer", "オン"), ("Transfer Texture", "オン")]},
]


def profile_def(pid: str) -> dict[str, Any]:
    for p in PROFILES:
        if p["id"] == pid:
            return p
    raise ProjectError(f"シーンプロファイルが見つかりません: {pid}")


def get_profile(project: Project, pid: str) -> dict[str, Any]:
    """記録済みの内容。まだなければ空の記録を返す（保存はしない）。"""
    profile_def(pid)
    rec = project.data.setdefault("scene_profiles", {}).get(pid)
    return rec or {"settings": [], "notes": "", "evidence": [], "decided_at": None, "updated_at": None}


def save_profile(project: Project, pid: str, *, settings: list[tuple[str, str]] | list[list[str]], notes: str = "",
                 evidence: list[str] | None = None, decided: bool = False) -> dict[str, Any]:
    """プロファイルの設定を記録する。evidence には根拠にした比較や品質チェックの ID を入れる。"""
    profile_def(pid)
    known = {c["id"] for c in project.data.get("comparisons", [])} | {q["id"] for q in project.data.get("qc_reports", [])}
    ev = list(evidence or [])
    for e in ev:
        if e not in known:
            raise ProjectError(f"根拠の ID が見つかりません: {e}")
    rec = get_profile(project, pid)
    rec.update({"settings": [[str(k), str(v)] for k, v in settings if str(k).strip()], "notes": notes,
                "evidence": ev, "updated_at": now_iso()})
    if decided:
        rec["decided_at"] = now_iso()
    project.data["scene_profiles"][pid] = rec
    project.log("scene_profile_saved", {"profile": pid, "decided": decided, "evidence": ev})
    return rec


def export_markdown(project: Project) -> str:
    """決めた設定を、VisoMaster Fusion の横に置いて見られるメモにする。"""
    lines = [f"# シーンプロファイル（{project.name}）", "",
             "VisoMaster Fusion で、場面ごとにこの設定を使います。項目名はソースから取ったもので、画面の表記は未確認です。", ""]
    for p in PROFILES:
        rec = get_profile(project, p["id"])
        state = f"決定 {rec['decided_at'][:10]}" if rec.get("decided_at") else "未決定"
        lines += [f"## {p['id']} {p['name']}（{state}）", f"例：{p['example']}。重点：{p['focus']}", ""]
        rows = rec["settings"] or [[k, v + "（仕様の推奨）"] for k, v in p["settings"]]
        lines += ["| 項目 | 値 |", "|---|---|"] + [f"| {k} | {v} |" for k, v in rows]
        if rec.get("notes"):
            lines += ["", rec["notes"]]
        if rec.get("evidence"):
            lines += ["", "根拠：" + "、".join(rec["evidence"])]
        lines.append("")
    return "\n".join(lines)

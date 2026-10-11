"""素材の確認記録（仕様 S1、S2）。consent_log.json に追記だけする。"""

from __future__ import annotations

import getpass
import uuid
from pathlib import Path

from .util import now_iso, read_json, write_json_atomic

# 確認文。画面にもこの文をそのまま出し、記録にも残す。
STATEMENT_SOURCE_VIDEO = (
    "この動画には私本人だけが映っている、または映っている全員から顔の置き換えについて"
    "了承を得ています。ネット上の他人の動画ではありません。（S2）"
)
STATEMENT_FACE_PERSON = (
    "この人物の顔素材は、AIで作った実在しない人物のものです。実在の人物（芸能人、知人など）の"
    "顔や、実在の人物に似せた顔ではありません。（S1）"
)

KIND_SOURCE_VIDEO = "source_video"
KIND_FACE_PERSON = "face_person"
KIND_FACE_IMAGE = "face_image"


class ConsentRequired(RuntimeError):
    """確認なしで取り込もうとしたときのエラー。"""


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


class ConsentLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.exists():
            write_json_atomic(self.path, {"version": 1, "records": []})

    def records(self) -> list[dict]:
        return read_json(self.path, {"records": []}).get("records", [])

    def add(self, kind: str, statement: str, *, target: str, sha256: str | None = None,
            extra: dict | None = None) -> dict:
        data = read_json(self.path, {"version": 1, "records": []})
        rec = {
            "id": "c_" + uuid.uuid4().hex[:12],
            "kind": kind,
            "statement": statement,
            "confirmed": True,
            "target": target,
            "sha256": sha256,
            "confirmed_at": now_iso(),
            "confirmed_by": _user(),
        }
        if extra:
            rec["extra"] = extra
        data.setdefault("records", []).append(rec)
        write_json_atomic(self.path, data)
        return rec

    def find(self, record_id: str) -> dict | None:
        for r in self.records():
            if r.get("id") == record_id:
                return r
        return None

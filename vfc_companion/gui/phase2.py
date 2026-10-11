"""フェーズ 2 の画面（比較、品質チェック、シーン別の設定）。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QImage, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton,
                               QScrollArea, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from .. import compare as cmpmod
from .. import facefusion as ff
from .. import profiles as prof
from .. import qc as qcmod
from .common import bgr_to_qimage, error_box, fmt_time, label
from .viewer import FrameView, PairFetcher

if TYPE_CHECKING:
    from .app import MainWindow

ORDERS = ["large-small", "small-large", "left-right", "right-left", "top-bottom", "bottom-top",
          "best-worst", "worst-best"]
ORDER_LABELS = {"large-small": "大きい順", "small-large": "小さい順", "left-right": "左から", "right-left": "右から",
                "top-bottom": "上から", "bottom-top": "下から", "best-worst": "検出の確かさ順",
                "worst-best": "検出の確かさの逆順"}
CHECK_STATES = [("未確認", None), ("問題なし", True), ("問題あり", False)]


def _ro(text: Any) -> QTableWidgetItem:
    it = QTableWidgetItem("" if text is None else str(text))
    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    return it


def _open(path: Path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def _table(headers: list[str], stretch: int) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    hh = t.horizontalHeader()
    for i in range(len(headers)):
        hh.setSectionResizeMode(i, QHeaderView.Stretch if i == stretch else QHeaderView.ResizeToContents)
    t.verticalHeader().setVisible(False)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    return t


def _scroll(w: QWidget) -> QScrollArea:
    s = QScrollArea()
    s.setWidgetResizable(True)
    s.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    s.setWidget(w)
    return s


def _render_label(r: dict[str, Any]) -> str:
    return r.get("label") or r["id"]


def _clip_combo_fill(combo: QComboBox, project, keep: str | None) -> None:
    combo.blockSignals(True)
    combo.clear()
    for c in project.data["clips"]:
        mark = "⛔ " if c.get("facefusion_blocked") else ""
        combo.addItem(f"{mark}{Path(c['file']).name}（{c['frame_count']} フレーム）", c["id"])
    combo.blockSignals(False)
    if combo.count():
        combo.setCurrentIndex(max(0, combo.findData(keep)) if keep else combo.count() - 1)


# =====================================================================
# 比較（M5）
# =====================================================================
class CompareTab(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.clip_id: str | None = None
        self.ref_faces: list[dict[str, Any]] = []
        self.ref_frame_shown: int | None = None

        root = QHBoxLayout(self)
        split = QSplitter()
        root.addWidget(split)

        # ---- 左：FaceFusion、クリップ、人物の割り当て、実行 ----
        left = QWidget()
        lv = QVBoxLayout(left)
        g = QGroupBox("FaceFusion（比べる相手として使う）")
        f = QFormLayout(g)
        self.ff_dir = QLineEdit()
        self.ff_dir.setPlaceholderText(r"空なら自動で探す（例: C:\Users\user\facefusion）")
        b = QPushButton("参照…")
        b.clicked.connect(self._pick_dir)
        row = QHBoxLayout()
        row.addWidget(self.ff_dir, 1)
        row.addWidget(b)
        f.addRow("フォルダ", row)
        self.ff_python = QLineEdit()
        self.ff_python.setPlaceholderText(r"空なら自動で探す（例: C:\Users\user\miniconda3\envs\facefusion\python.exe）")
        b2 = QPushButton("参照…")
        b2.clicked.connect(self._pick_python)
        row2 = QHBoxLayout()
        row2.addWidget(self.ff_python, 1)
        row2.addWidget(b2)
        f.addRow("Python", row2)
        self.ff_models = QLineEdit()
        self.ff_models.setPlaceholderText("例: alphaface_256,hyperswap_1a_256")
        f.addRow("比べるモデル", self.ff_models)
        bc = QPushButton("保存して確認")
        bc.clicked.connect(self._check_ff)
        f.addRow("", bc)
        self.ff_status = label("")
        f.addRow(self.ff_status)
        lv.addWidget(g)

        crow = QHBoxLayout()
        crow.addWidget(QLabel("クリップ"))
        self.clip_combo = QComboBox()
        self.clip_combo.currentIndexChanged.connect(self._clip_changed)
        crow.addWidget(self.clip_combo, 1)
        lv.addLayout(crow)
        self.blocked = label("", "bad")
        lv.addWidget(self.blocked)
        self.chk_ff = QCheckBox("FaceFusion を実行する（同じ設定の結果があれば再利用）")
        self.chk_ff.setChecked(True)
        lv.addWidget(self.chk_ff)
        self.btn_run = QPushButton("比較を作る（FaceFusion の実行 → VisoMaster の出力探し → 並べる）")
        self.btn_run.setObjectName("primary")
        self.btn_run.clicked.connect(self._run)
        lv.addWidget(self.btn_run)

        g2 = QGroupBox("置き換える人物（2 人以上映るクリップでは必須）")
        v2 = QVBoxLayout(g2)
        self.ref_view = FrameView()
        self.ref_view.setMinimumSize(360, 200)
        v2.addWidget(self.ref_view, 1)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("基準フレーム"))
        self.ref_spin = QSpinBox()
        self.ref_spin.setRange(0, 0)
        r1.addWidget(self.ref_spin)
        self.order_combo = QComboBox()
        for o in ORDERS:
            self.order_combo.addItem(ORDER_LABELS[o], o)
        r1.addWidget(QLabel("番号の付け方"))
        r1.addWidget(self.order_combo)
        bshow = QPushButton("顔を表示")
        bshow.clicked.connect(lambda: self._show_faces(self.ref_spin.value()))
        bfind = QPushButton("顔のある最初のフレーム")
        bfind.clicked.connect(self._find_ref)
        r1.addWidget(bshow)
        r1.addWidget(bfind)
        r1.addStretch(1)
        v2.addLayout(r1)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("顔の番号"))
        self.pos_spin = QSpinBox()
        self.pos_spin.setRange(0, 0)
        r2.addWidget(self.pos_spin)
        r2.addWidget(QLabel("を"))
        self.person_combo = QComboBox()
        r2.addWidget(self.person_combo, 1)
        bassign = QPushButton("に置き換える")
        bassign.clicked.connect(self._assign)
        r2.addWidget(bassign)
        v2.addLayout(r2)
        self.assign_table = _table(["人物", "基準フレーム", "顔の番号", "番号の付け方"], 0)
        self.assign_table.setMaximumHeight(130)
        v2.addWidget(self.assign_table)
        r3 = QHBoxLayout()
        bdel = QPushButton("選んだ行を外す")
        bdel.clicked.connect(self._unassign)
        r3.addWidget(bdel)
        self.assign_note = label("")
        r3.addWidget(self.assign_note, 1)
        v2.addLayout(r3)
        lv.addWidget(g2, 1)
        split.addWidget(_scroll(left))

        # ---- 右：結果と比較 ----
        right = QWidget()
        rv = QVBoxLayout(right)
        g3 = QGroupBox("このクリップの結果")
        v3 = QVBoxLayout(g3)
        self.vm_hint = label("")
        v3.addWidget(self.vm_hint)
        r4 = QHBoxLayout()
        for text, fn in (("保存先のフォルダを開く", self._open_vm_dir), ("VisoMaster の出力を探す", self._sync_vm),
                         ("動画を指定して登録…", self._register)):
            bb = QPushButton(text)
            bb.clicked.connect(fn)
            r4.addWidget(bb)
        r4.addStretch(1)
        v3.addLayout(r4)
        self.render_table = _table(["結果", "状態", "作成", "ファイル"], 0)
        self.render_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.render_table.cellDoubleClicked.connect(self._render_open)
        v3.addWidget(self.render_table, 1)
        r5 = QHBoxLayout()
        bsel = QPushButton("選んだ結果で比較を作る（最大 4 つ）")
        bsel.clicked.connect(self._compare_selected)
        blog = QPushButton("ログを開く")
        blog.clicked.connect(self._open_log)
        r5.addWidget(bsel)
        r5.addWidget(blog)
        r5.addStretch(1)
        v3.addLayout(r5)
        rv.addWidget(g3, 1)

        g4 = QGroupBox("比較")
        v4 = QVBoxLayout(g4)
        self.cmp_table = _table(["作成", "並べたもの"], 1)
        self.cmp_table.setMaximumHeight(130)
        self.cmp_table.currentCellChanged.connect(lambda r, *_: self._cmp_selected(r))
        v4.addWidget(self.cmp_table)
        self.cmp_view = FrameView()
        self.cmp_view.setMinimumSize(480, 220)
        v4.addWidget(self.cmp_view, 2)
        r6 = QHBoxLayout()
        r6.addWidget(QLabel("比較画像"))
        self.cmp_frames = QComboBox()
        self.cmp_frames.currentIndexChanged.connect(self._cmp_image)
        r6.addWidget(self.cmp_frames, 1)
        bvid = QPushButton("比較動画を開く")
        bvid.setObjectName("primary")
        bvid.clicked.connect(self._cmp_open_video)
        bdir = QPushButton("フォルダを開く")
        bdir.clicked.connect(self._cmp_open_dir)
        r6.addWidget(bvid)
        r6.addWidget(bdir)
        v4.addLayout(r6)
        rv.addWidget(g4, 2)
        split.addWidget(right)
        split.setSizes([620, 700])

    # ---------- 読み込み ----------
    def reload(self) -> None:
        p = self.win.project
        s = p.settings
        self.ff_dir.setText(str(s["facefusion_dir"]))
        self.ff_python.setText(str(s["facefusion_python"]))
        self.ff_models.setText(str(s["ff_models"]))
        t = p.data["tools"].get("facefusion", {})
        if t.get("version"):
            self.ff_status.setText(f"FaceFusion {t['version']} を確認済み（{t.get('checked_at', '')[:16].replace('T', ' ')}）"
                                   f"<br><span style='color:#9aa0aa'>{t.get('python', '')}</span>")
        else:
            self.ff_status.setText("まだ確認していません。「保存して確認」を押してください。")
        self.order_combo.setCurrentIndex(max(0, self.order_combo.findData(s["ff_face_selector_order"])))
        self.person_combo.clear()
        for per in p.data["persons"]:
            self.person_combo.addItem(per["name"], per["id"])
        self.vm_hint.setText(
            "VisoMaster Fusion で書き出した動画は、次のフォルダに<b>クリップのファイル名を含む名前</b>で保存すると"
            f"自動で見つかります。<br><span style='color:#9aa0aa'>{p.root / 'renders' / 'visomaster'}</span>")
        _clip_combo_fill(self.clip_combo, p, self.clip_id)
        self._clip_changed(self.clip_combo.currentIndex())

    def _clip(self) -> dict[str, Any] | None:
        if not self.clip_id:
            return None
        return self.win.project.clip(self.clip_id)

    def _clip_changed(self, idx: int) -> None:
        cid = self.clip_combo.itemData(idx) if idx >= 0 else None
        if cid != self.clip_id:
            self.ref_faces = []
            self.ref_frame_shown = None
            self.ref_view.set_image(None)
        self.clip_id = cid
        clip = self._clip()
        self.btn_run.setEnabled(clip is not None)
        if not clip:
            self.blocked.setText("テスト用クリップの画面でクリップを切り出してください。")
            self.assign_table.setRowCount(0)
            self.render_table.setRowCount(0)
            self.cmp_table.setRowCount(0)
            return
        self.blocked.setText("このクリップは FaceFusion のコンテンツ判定で止まりました。FaceFusion の比較からは外し、"
                             "VisoMaster Fusion の結果だけを並べます（仕様 S4）。" if clip.get("facefusion_blocked") else "")
        self.ref_spin.setRange(0, max(0, clip["frame_count"] - 1))
        self._fill_assignments()
        self._fill_renders()
        self._fill_comparisons()

    def _fill_assignments(self) -> None:
        p, clip = self.win.project, self._clip()
        rows = clip.get("assignments") or []
        self.assign_table.setRowCount(len(rows))
        for r, a in enumerate(rows):
            self.assign_table.setItem(r, 0, _ro(p.person(a["person_id"])["name"]))
            self.assign_table.setItem(r, 1, _ro(a["reference_frame"]))
            self.assign_table.setItem(r, 2, _ro(a["reference_face_position"]))
            self.assign_table.setItem(r, 3, _ro(ORDER_LABELS.get(a["face_selector_order"], a["face_selector_order"])))
        n = len(p.data["persons"])
        if rows:
            self.assign_note.setText(f"{len(rows)} 人を置き換えます（1 人ずつ順に処理）。")
        elif n == 1:
            self.assign_note.setText("人物が 1 人だけなので、割り当てがなければ、顔のある最初のフレームのいちばん大きい顔を"
                                     "置き換えます。")
        else:
            self.assign_note.setText("基準フレームの顔に番号が付きます。番号ごとに置き換える人物を決めてください。")

    def _fill_renders(self) -> None:
        p = self.win.project
        rows = [r for r in p.data.get("renders", []) if r.get("clip_id") == self.clip_id]
        self.render_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            exists = p.abs(r["file"]).exists()
            it = _ro(_render_label(r))
            it.setData(Qt.UserRole, r["id"])
            self.render_table.setItem(i, 0, it)
            st = ff.STATUS_TEXT.get(r.get("status", ""), r.get("status", ""))
            if r.get("status") == ff.STATUS_DONE and not exists:
                st = "ファイルがありません"
            sit = _ro(st)
            if r.get("status") != ff.STATUS_DONE or not exists:
                sit.setForeground(Qt.yellow)
            self.render_table.setItem(i, 1, sit)
            when = (r.get("finished_at") or r.get("started_at") or r.get("registered_at") or "")[:16].replace("T", " ")
            self.render_table.setItem(i, 2, _ro(when))
            fit = _ro(Path(r["file"]).name)
            fit.setToolTip(r["file"])
            self.render_table.setItem(i, 3, fit)

    def _fill_comparisons(self) -> None:
        rows = [c for c in reversed(self.win.project.data.get("comparisons", [])) if c["clip_id"] == self.clip_id]
        self.cmp_rows = rows
        self.cmp_table.setRowCount(len(rows))
        for i, c in enumerate(rows):
            self.cmp_table.setItem(i, 0, _ro(c["created_at"][:16].replace("T", " ")))
            self.cmp_table.setItem(i, 1, _ro("　｜　".join(pn["label"] for pn in c["panels"])))
        if rows:
            self.cmp_table.setCurrentCell(0, 0)
            self._cmp_selected(0)
        else:
            self.cmp_frames.clear()
            self.cmp_view.set_image(None)

    # ---------- FaceFusion ----------
    def _pick_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "FaceFusion のフォルダ（facefusion.py がある場所）を選ぶ")
        if d:
            self.ff_dir.setText(d)

    def _pick_python(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "FaceFusion の環境の python を選ぶ", "",
                                              "Python (python.exe python python3);;すべて (*)")
        if path:
            self.ff_python.setText(path)

    def _save_ff_settings(self) -> None:
        p = self.win.project
        for key, w in (("facefusion_dir", self.ff_dir), ("facefusion_python", self.ff_python),
                       ("ff_models", self.ff_models)):
            val = w.text().strip()
            if val != str(p.settings[key]):
                p.set_setting(key, val)

    def _check_ff(self) -> None:
        self._save_ff_settings()

        def job(progress):
            progress(0, 0, "FaceFusion を確かめています（初回は時間がかかることがあります）")
            info = ff.check_install(self.win.project)
            models = [m.strip() for m in str(self.win.project.settings["ff_models"]).split(",") if m.strip()]
            bad = [m for m in models if m not in info.choices.get("--face-swapper-model", [])]
            return info, bad

        def done(res):
            info, bad = res
            self.win.reload_all()
            msg = (f"FaceFusion {info.version} を確認しました。使える実行環境："
                   f"{', '.join(info.choices.get('--execution-providers', [])) or '不明'}")
            if bad:
                error_box(self, "モデル名を確認してください", msg + "\n\nFaceFusion にないモデル名：" + ", ".join(bad))
            else:
                self.win.statusBar().showMessage(msg, 10000)

        self.win.run_job(job, "FaceFusion を確かめています", on_done=done)

    # ---------- 人物の割り当て ----------
    def _find_ref(self) -> None:
        clip = self._clip()
        if not clip:
            return

        def job(progress):
            progress(0, 0, "顔のあるフレームを探しています（初回はモデルを読み込みます）")
            return ff.find_reference_frame(self.win.project, clip)

        def done(n):
            if n is None:
                error_box(self, "顔が見つかりません", "このクリップには顔が見つかりませんでした。")
                return
            self.ref_spin.setValue(n)
            self._show_faces(n)

        self.win.run_job(job, "顔を探しています", on_done=done)

    def _show_faces(self, frame: int) -> None:
        import cv2

        clip = self._clip()
        if not clip:
            return
        order = self.order_combo.currentData()

        def job(progress):
            progress(0, 0, "顔を探しています（初回はモデルを読み込みます）")
            img, faces = ff.reference_faces(self.win.project, clip, frame, order)
            for i, fc in enumerate(faces):
                x1, y1, x2, y2 = (int(v) for v in fc["bbox"])
                cv2.rectangle(img, (x1, y1), (x2, y2), (60, 200, 255), 2)
                txt = str(i)
                fs = 1.0
                (tw, th), _b = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, fs, 2)
                cv2.rectangle(img, (x1, max(0, y1 - th - 10)), (x1 + tw + 10, y1), (60, 200, 255), -1)
                cv2.putText(img, txt, (x1 + 5, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 0, 0), 2, cv2.LINE_AA)
            return bgr_to_qimage(img), faces

        def done(res):
            qimg, faces = res
            self.ref_faces = faces
            self.ref_frame_shown = frame
            self.ref_view.set_image(qimg)
            self.pos_spin.setRange(0, max(0, len(faces) - 1))
            self.win.statusBar().showMessage(f"フレーム {frame}：顔 {len(faces)} 個（番号は "
                                             f"{ORDER_LABELS[order]}）", 8000)

        self.win.run_job(job, "顔を探しています", on_done=done)

    def _assign(self) -> None:
        clip = self._clip()
        if not clip:
            return
        if self.ref_frame_shown is None or not self.ref_faces:
            error_box(self, "先に顔を表示してください", "基準フレームを選んで「顔を表示」を押すと、顔に番号が付きます。")
            return
        pid = self.person_combo.currentData()
        if not pid:
            error_box(self, "人物がいません", "顔素材の画面で AI 人物を追加してください。")
            return
        rows = [a for a in (clip.get("assignments") or []) if a["person_id"] != pid]
        pos = self.pos_spin.value()
        rows = [a for a in rows if not (a["reference_frame"] == self.ref_frame_shown
                                        and a["reference_face_position"] == pos)]
        rows.append({"person_id": pid, "reference_frame": self.ref_frame_shown, "reference_face_position": pos,
                     "reference_face_distance": float(self.win.project.settings["ff_reference_face_distance"]),
                     "face_selector_order": self.order_combo.currentData(), "face_selector_gender": ""})
        ff.set_assignments(self.win.project, clip["id"], rows)
        self._fill_assignments()

    def _unassign(self) -> None:
        clip = self._clip()
        sel = sorted({i.row() for i in self.assign_table.selectedIndexes()})
        if not clip or not sel:
            return
        rows = [a for i, a in enumerate(clip.get("assignments") or []) if i not in sel]
        ff.set_assignments(self.win.project, clip["id"], rows)
        self._fill_assignments()

    # ---------- 実行 ----------
    def _run(self) -> None:
        clip = self._clip()
        if not clip:
            return
        self._save_ff_settings()
        cid, run_ff = clip["id"], self.chk_ff.isChecked()

        def job(progress):
            return cmpmod.run_one_button(self.win.project, cid, run_facefusion=run_ff, progress=progress,
                                         should_stop=lambda: self.win.cancel_requested)

        def done(res):
            self.win.project.save()
            self.win.reload_all()
            notes = list(res.get("skipped", []))
            if res.get("comparison"):
                self.win.statusBar().showMessage("比較を作りました", 8000)
            elif self.win.cancel_requested:
                notes.insert(0, "中止しました。")
            if notes:
                QMessageBox.information(self, "比較", "\n".join(notes))

        self.win.run_job(job, "比較を作っています", on_done=done, cancellable=True)

    def _selected_render_ids(self) -> list[str]:
        rows = sorted({i.row() for i in self.render_table.selectedIndexes()})
        return [self.render_table.item(r, 0).data(Qt.UserRole) for r in rows]

    def _compare_selected(self) -> None:
        ids = self._selected_render_ids()
        if not ids or not self.clip_id:
            error_box(self, "結果を選んでください", "並べたい結果を表で選んでください（Ctrl を押しながらで複数）。")
            return
        cid = self.clip_id

        def job(progress):
            rec = cmpmod.make_comparison(self.win.project, cid, render_ids=ids, progress=progress)
            self.win.project.save()
            return rec

        self.win.run_job(job, "比較を作っています", on_done=lambda _r: self.win.reload_all())

    # ---------- VisoMaster の出力 ----------
    def _open_vm_dir(self) -> None:
        d = self.win.project.root / "renders" / "visomaster"
        d.mkdir(parents=True, exist_ok=True)
        _open(d)

    def _sync_vm(self) -> None:
        recs = ff.sync_visomaster(self.win.project)
        self.win.project.save()
        self.win.reload_all()
        self.win.statusBar().showMessage(f"VisoMaster Fusion の出力 {len(recs)} 本を登録済みです", 8000)

    def _register(self) -> None:
        clip = self._clip()
        if not clip:
            return
        path, _ = QFileDialog.getOpenFileName(self, "VisoMaster Fusion で書き出した動画を選ぶ", "",
                                              "動画 (*.mp4 *.mov *.mkv *.avi *.m4v *.webm);;すべて (*)")
        if not path:
            return
        name, ok = QInputDialog.getText(self, "表示名", "比較動画に表示する名前（例: VisoMaster 512 表情オン）",
                                        text=f"VisoMaster {Path(path).stem}")
        if not ok:
            return
        p, cid = self.win.project, clip["id"]

        def job(progress):
            src = Path(path).resolve()
            dest = src
            try:
                src.relative_to(p.root.resolve())
            except ValueError:
                progress(0, 0, "プロジェクトのフォルダに写しています")
                d = p.root / "renders" / "visomaster"
                d.mkdir(parents=True, exist_ok=True)
                dest = d / f"{cid}_{src.name}"
                k = 1
                while dest.exists():
                    dest = d / f"{cid}_{src.stem}_{k}{src.suffix}"
                    k += 1
                shutil.copy2(src, dest)
            return ff.register_render(p, cid, dest, tool="visomaster", label=name.strip())

        self.win.run_job(job, "登録しています", on_done=lambda _r: self.win.reload_all())

    def _render_open(self, row: int, _col: int) -> None:
        rid = self.render_table.item(row, 0).data(Qt.UserRole)
        r = self.win.project.render(rid)
        f = self.win.project.abs(r["file"])
        _open(f if f.exists() else f.parent)

    def _open_log(self) -> None:
        ids = self._selected_render_ids()
        if not ids:
            return
        r = self.win.project.render(ids[0])
        if r.get("log"):
            _open(self.win.project.abs(r["log"]))
        else:
            error_box(self, "ログはありません", "FaceFusion で作った結果だけにログがあります。")

    # ---------- 比較の表示 ----------
    def _cmp_selected(self, row: int) -> None:
        self.cmp_frames.blockSignals(True)
        self.cmp_frames.clear()
        if 0 <= row < len(getattr(self, "cmp_rows", [])):
            c = self.cmp_rows[row]
            for img in c["images"]:
                self.cmp_frames.addItem(Path(img).stem.replace("frame_", "フレーム "), img)
        self.cmp_frames.blockSignals(False)
        self._cmp_image(0)

    def _cmp_image(self, idx: int) -> None:
        path = self.cmp_frames.itemData(idx) if idx >= 0 else None
        if not path:
            self.cmp_view.set_image(None)
            return
        img = QImage(str(self.win.project.abs(path)))
        self.cmp_view.set_image(None if img.isNull() else img)

    def _cur_cmp(self) -> dict[str, Any] | None:
        row = self.cmp_table.currentRow()
        rows = getattr(self, "cmp_rows", [])
        return rows[row] if 0 <= row < len(rows) else None

    def _cmp_open_video(self) -> None:
        c = self._cur_cmp()
        if c:
            _open(self.win.project.abs(c["video"]))

    def _cmp_open_dir(self) -> None:
        c = self._cur_cmp()
        if c:
            _open(self.win.project.abs(c["video"]).parent)


# =====================================================================
# 品質チェック（M6）
# =====================================================================
class QcTab(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.clip_id: str | None = None
        self.qc_id: str | None = None
        self.metrics: dict[str, Any] | None = None
        self.paths: list[str] = []
        self.pos = 0
        self.max_frame = 0
        self.fetcher = PairFetcher()
        self.fetcher.ready.connect(self._on_frame)
        self.fetcher.start()
        self._seek_timer = QTimer(self)
        self._seek_timer.setSingleShot(True)
        self._seek_timer.setInterval(40)
        self._seek_timer.timeout.connect(lambda: self.paths and self.fetcher.request(self.paths, self.pos))

        root = QHBoxLayout(self)
        split = QSplitter()
        root.addWidget(split)

        # ---- 左：対象と履歴 ----
        left = QWidget()
        lv = QVBoxLayout(left)
        crow = QHBoxLayout()
        crow.addWidget(QLabel("クリップ"))
        self.clip_combo = QComboBox()
        self.clip_combo.currentIndexChanged.connect(self._clip_changed)
        crow.addWidget(self.clip_combo, 1)
        lv.addLayout(crow)
        lv.addWidget(QLabel("調べる出力"))
        self.render_list = QListWidget()
        self.render_list.currentRowChanged.connect(self._render_changed)
        lv.addWidget(self.render_list, 1)
        self.btn_run = QPushButton("品質チェックを実行")
        self.btn_run.setObjectName("primary")
        self.btn_run.clicked.connect(self._run)
        lv.addWidget(self.btn_run)
        lv.addWidget(QLabel("この出力の品質チェック"))
        self.hist = _table(["日時", "判定", "問題の割合", "区間", "良い"], 0)
        self.hist.currentCellChanged.connect(lambda r, *_: self._hist_selected(r))
        lv.addWidget(self.hist, 1)
        bcal = QPushButton("良い出力として基準値を決める…")
        bcal.clicked.connect(self._calibrate)
        lv.addWidget(bcal)
        lv.addWidget(label("基準値は、良いと判断した出力の値から決めます（仕様 M6）。決めたあと実行し直すと新しい基準で判定されます。"))
        split.addWidget(left)

        # ---- 右：結果 ----
        right = QWidget()
        rv = QVBoxLayout(right)
        top = QHBoxLayout()
        self.summary = label("")
        self.summary.setStyleSheet("font-size: 15px; font-weight: bold;")
        top.addWidget(self.summary, 1)
        brep = QPushButton("レポートを開く（ブラウザ）")
        brep.setObjectName("primary")
        brep.clicked.connect(self._open_report)
        bdir = QPushButton("フォルダを開く")
        bdir.clicked.connect(self._open_dir)
        top.addWidget(brep)
        top.addWidget(bdir)
        rv.addLayout(top)
        self.notes = label("", "warn")
        rv.addWidget(self.notes)
        self.iv_table = _table(["#", "クリップ内の時刻", "クリップのフレーム", "元の動画のフレーム", "理由"], 4)
        self.iv_table.currentCellChanged.connect(lambda r, *_: self._iv_selected(r))
        rv.addWidget(self.iv_table, 1)
        self.view = FrameView()
        rv.addWidget(self.view, 2)
        ctl = QHBoxLayout()
        bp = QPushButton("◀ 1f")
        bp.clicked.connect(lambda: self.seek(self.pos - 1))
        bn = QPushButton("1f ▶")
        bn.clicked.connect(lambda: self.seek(self.pos + 1))
        self.frame_spin = QSpinBox()
        self.frame_spin.setRange(0, 0)
        self.frame_spin.setKeyboardTracking(False)
        self.frame_spin.valueChanged.connect(self.seek)
        self.pos_label = QLabel("")
        for w in (bp, bn, QLabel("フレーム"), self.frame_spin, self.pos_label):
            ctl.addWidget(w)
        ctl.addStretch(1)
        ctl.addWidget(label("左：元のクリップ　右：出力", wrap=False))
        rv.addLayout(ctl)

        g = QGroupBox("目視のチェック（仕様 12.1。等倍と 0.5 倍で再生して確認）")
        gf = QGridLayout(g)
        gf.setColumnStretch(0, 1)
        gf.setColumnStretch(2, 1)
        self.checks: dict[str, QComboBox] = {}
        for i, (key, text) in enumerate(qcmod.VISUAL_CHECKS):
            cb = QComboBox()
            for t, _v in CHECK_STATES:
                cb.addItem(t)
            cb.setFixedWidth(120)
            cb.currentIndexChanged.connect(lambda idx, k=key: self._check_changed(k, idx))
            self.checks[key] = cb
            gf.addWidget(label(text), i // 2, (i % 2) * 2)
            gf.addWidget(cb, i // 2, (i % 2) * 2 + 1)
        rv.addWidget(g)
        split.addWidget(_scroll(right))
        split.setSizes([360, 960])

        for key, fn in (("Left", lambda: self.seek(self.pos - 1)), ("Right", lambda: self.seek(self.pos + 1))):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)

    # ---------- 読み込み ----------
    def reload(self) -> None:
        _clip_combo_fill(self.clip_combo, self.win.project, self.clip_id)
        self._clip_changed(self.clip_combo.currentIndex())

    def _clip_changed(self, idx: int) -> None:
        self.clip_id = self.clip_combo.itemData(idx) if idx >= 0 else None
        keep = self.render_list.currentItem().data(Qt.UserRole) if self.render_list.currentItem() else None
        self.render_list.blockSignals(True)
        self.render_list.clear()
        if self.clip_id:
            p = self.win.project
            last = {}
            for q in p.data.get("qc_reports", []):
                last[q["render_id"]] = q
            for r in ff.renders_for_clip(p, self.clip_id):
                q = last.get(r["id"])
                mark = "" if not q else ("✓ " if q["pass"] else "⚠ ")
                it = QListWidgetItem(f"{mark}{_render_label(r)}")
                it.setData(Qt.UserRole, r["id"])
                self.render_list.addItem(it)
        self.render_list.blockSignals(False)
        row = 0
        for i in range(self.render_list.count()):
            if self.render_list.item(i).data(Qt.UserRole) == keep:
                row = i
        if self.render_list.count():
            self.render_list.setCurrentRow(row)
        self._render_changed(self.render_list.currentRow())

    def _render_id(self) -> str | None:
        it = self.render_list.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _render_changed(self, _row: int) -> None:
        rid = self._render_id()
        self.btn_run.setEnabled(rid is not None)
        rows = [q for q in reversed(self.win.project.data.get("qc_reports", [])) if q["render_id"] == rid] if rid else []
        self.hist_rows = rows
        self.hist.blockSignals(True)
        self.hist.setRowCount(len(rows))
        for i, q in enumerate(rows):
            self.hist.setItem(i, 0, _ro(q["created_at"][:16].replace("T", " ")))
            verdict = "判定不可" if q["pass"] is None else ("目安内" if q["pass"] else "要確認")
            it = _ro(verdict)
            it.setForeground(Qt.green if q["pass"] else Qt.yellow)
            self.hist.setItem(i, 1, it)
            self.hist.setItem(i, 2, _ro("—" if q["ratio"] is None else f"{q['ratio'] * 100:.2f}%"))
            self.hist.setItem(i, 3, _ro(q["intervals"]))
            self.hist.setItem(i, 4, _ro("✓" if q.get("judged_good") else ""))
        self.hist.blockSignals(False)
        if rows:
            self.hist.setCurrentCell(0, 0)
        self._hist_selected(0 if rows else -1)

    def _hist_selected(self, row: int) -> None:
        rows = getattr(self, "hist_rows", [])
        q = rows[row] if 0 <= row < len(rows) else None
        self.qc_id = q["id"] if q else None
        self.metrics = None
        for cb in self.checks.values():
            cb.blockSignals(True)
            cb.setCurrentIndex(0)
            cb.blockSignals(False)
        rid = self._render_id()
        p = self.win.project
        self.paths = []
        if rid:
            r = p.render(rid)
            clip = p.clip(r["clip_id"])
            self.paths = [str(p.abs(clip["file"])), str(p.abs(r["file"]))]
            self.max_frame = max(0, clip["frame_count"] - 1)
            self.frame_spin.blockSignals(True)
            self.frame_spin.setRange(0, self.max_frame)
            self.frame_spin.blockSignals(False)
        if not q:
            self.summary.setText("まだ品質チェックをしていません。" if rid else "調べる出力がありません。比較の画面で結果を登録してください。")
            self.notes.setText("")
            self.iv_table.setRowCount(0)
            if self.paths:
                self.seek(0, force=True)
            return
        try:
            self.metrics = qcmod.load_metrics(p, q["id"])
        except Exception as e:  # noqa: BLE001
            self.summary.setText(f"結果を読めませんでした：{e}")
            return
        m = self.metrics
        thr = float(m["thresholds"]["qc_pass_ratio"]) * 100
        if m["pass"] is None:
            head = "⚠ 判定できません（置き換える顔が見つかりません）"
            color = "#f0b54a"
        elif m["pass"]:
            head = "✓ 自動チェックは合格の目安内"
            color = "#6fcf8f"
        else:
            head = "⚠ 自動チェックで要確認"
            color = "#ef6b6b"
        ratio = "—" if m["ratio"] is None else f"{m['ratio'] * 100:.2f}%"
        self.summary.setText(f"<span style='color:{color}'>{head}</span>　問題のフレーム {ratio}（目安 {thr:g}% 以内）"
                             f"　区間 {len(m['intervals'])}")
        self.notes.setText("　".join(m.get("notes", [])))
        ivs = m["intervals"]
        self.iv_table.blockSignals(True)
        self.iv_table.setRowCount(len(ivs))
        for i, iv in enumerate(ivs):
            self.iv_table.setItem(i, 0, _ro(i + 1))
            self.iv_table.setItem(i, 1, _ro(f"{fmt_time(iv['start_time'])}〜{fmt_time(iv['end_time'])}"))
            self.iv_table.setItem(i, 2, _ro(f"{iv['start_frame']}〜{iv['end_frame']}"))
            self.iv_table.setItem(i, 3, _ro(f"{iv['source_start_frame']}〜{iv['source_end_frame']}"))
            self.iv_table.setItem(i, 4, _ro(qcmod.reasons_text(iv["reasons"])))
        self.iv_table.blockSignals(False)
        vc = q.get("visual_checks", {})
        for key, cb in self.checks.items():
            ok = vc.get(key, {}).get("ok")
            idx = next(i for i, (_t, v) in enumerate(CHECK_STATES) if v is ok)
            cb.blockSignals(True)
            cb.setCurrentIndex(idx)
            cb.blockSignals(False)
        if ivs:
            self.iv_table.setCurrentCell(0, 0)
            self._iv_selected(0)
        else:
            self.seek(0, force=True)

    def _iv_selected(self, row: int) -> None:
        if not self.metrics or not (0 <= row < len(self.metrics["intervals"])):
            return
        self.seek(self.metrics["intervals"][row]["worst_frame"], force=True)

    # ---------- プレビュー ----------
    def seek(self, frame: int, force: bool = False) -> None:
        if not self.paths:
            return
        frame = max(0, min(self.max_frame, int(frame)))
        if frame == self.pos and not force:
            return
        self.pos = frame
        self.frame_spin.blockSignals(True)
        self.frame_spin.setValue(frame)
        self.frame_spin.blockSignals(False)
        text = ""
        if self.metrics:
            rb = self.metrics["reasons_by_frame"].get(str(frame)) or self.metrics["reasons_by_frame"].get(frame)
            text = ("⚠ " + qcmod.reasons_text(rb)) if rb else ""
            if not rb and self.metrics.get("step", 1) > 1 and frame % self.metrics["step"]:
                text = f"（{self.metrics['step']} フレームおきに調べています）"
        self.pos_label.setText(text)
        self._seek_timer.start()

    def _on_frame(self, frame: int, img) -> None:
        if frame == self.pos:
            self.view.set_image(img)

    # ---------- 処理 ----------
    def _run(self) -> None:
        rid = self._render_id()
        if not rid:
            return

        def job(progress):
            rec = qcmod.run_qc(self.win.project, rid, progress=progress, should_stop=lambda: self.win.cancel_requested)
            self.win.project.save()
            return rec

        def done(_rec):
            self.win.reload_all()

        self.win.run_job(job, "品質チェックをしています（初回はモデルを読み込みます）", on_done=done, cancellable=True)

    def _check_changed(self, key: str, idx: int) -> None:
        if not self.qc_id:
            return
        qcmod.set_visual_check(self.win.project, self.qc_id, key, CHECK_STATES[idx][1])

    def _open_report(self) -> None:
        if self.qc_id:
            _open(self.win.project.abs(qcmod.report_record(self.win.project, self.qc_id)["report"]))

    def _open_dir(self) -> None:
        if self.qc_id:
            _open(self.win.project.abs(qcmod.report_record(self.win.project, self.qc_id)["report"]).parent)

    def _calibrate(self) -> None:
        if not self.win.project.data.get("qc_reports"):
            error_box(self, "品質チェックの結果がありません", "先に、良いと思う出力で品質チェックを実行してください。")
            return
        dlg = CalibrationDialog(self, self.win.project, self.qc_id)
        if dlg.exec() == QDialog.Accepted:
            self.win.reload_all()

    def shutdown(self) -> None:
        self.fetcher.stop()


class CalibrationDialog(QDialog):
    """「良い」と判断した出力の品質チェック結果から、基準値を決める（仕様 M6）。"""

    def __init__(self, parent: QWidget, project, preselect: str | None):
        super().__init__(parent)
        self.project = project
        self.proposals: dict[str, dict[str, Any]] = {}
        self.setWindowTitle("基準値を決める")
        self.resize(760, 560)
        v = QVBoxLayout(self)
        s = project.settings
        v.addWidget(label(
            "目視で「良い」と判断した出力を選んでください。その出力の値の分布から基準値を決めます。"
            f"下限は下位 {s['qc_calibration_percentile']:g}% の値から {s['qc_calibration_margin']:g} 引いた値、"
            f"上限は上位 {s['qc_calibration_percentile']:g}% の値に {s['qc_calibration_margin']:g} 足した値です。"))
        self.list = QListWidget()
        renders = {r["id"]: r for r in project.data.get("renders", [])}
        for q in reversed(project.data.get("qc_reports", [])):
            r = renders.get(q["render_id"], {})
            good = "（良い出力）" if q.get("judged_good") else ""
            it = QListWidgetItem(f"{q['created_at'][:16].replace('T', ' ')}　{r.get('label', q['render_id'])}{good}")
            it.setData(Qt.UserRole, q["id"])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if q["id"] == preselect else Qt.Unchecked)
            self.list.addItem(it)
        self.list.itemChanged.connect(lambda _i: self._propose())
        v.addWidget(self.list, 1)
        self.table = _table(["基準値", "今の値", "案", "元にした値の数"], 0)
        v.addWidget(self.table, 1)
        self.note = label("")
        v.addWidget(self.note)
        bb = QDialogButtonBox()
        self.ok = bb.addButton("この値にする", QDialogButtonBox.AcceptRole)
        bb.addButton("やめる", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self._apply)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self._propose()

    def selected(self) -> list[str]:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]

    def _propose(self) -> None:
        ids = self.selected()
        self.proposals = qcmod.propose_thresholds(self.project, ids) if ids else {}
        self.table.setRowCount(len(self.proposals))
        for r, (k, d) in enumerate(self.proposals.items()):
            self.table.setItem(r, 0, _ro(k))
            self.table.setItem(r, 1, _ro(d["current"]))
            self.table.setItem(r, 2, _ro(d["proposed"]))
            self.table.setItem(r, 3, _ro(d["samples"]))
        self.ok.setEnabled(bool(self.proposals))
        self.note.setText("" if ids else "出力を 1 つ以上選んでください。")

    def _apply(self) -> None:
        ids = self.selected()
        if not self.proposals:
            return
        qcmod.apply_thresholds(self.project, self.proposals, ids)
        self.project.save()
        self.accept()


# =====================================================================
# シーン別の設定（仕様 10 章）
# =====================================================================
class ProfilesTab(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.pid: str | None = None
        h = QHBoxLayout(self)
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._show)
        left.addWidget(self.list, 1)
        bexp = QPushButton("メモを書き出す（Markdown）")
        bexp.clicked.connect(self._export)
        left.addWidget(bexp)
        left.addWidget(label("書き出したメモを VisoMaster Fusion の横に置いて、場面ごとに設定を合わせます。"
                             "このツールは VisoMaster Fusion の設定を直接は変えません（仕様 11.4）。"))
        lw = QWidget()
        lw.setLayout(left)
        lw.setMaximumWidth(320)
        h.addWidget(lw)

        right = QWidget()
        rv = QVBoxLayout(right)
        self.title = label("")
        self.title.setStyleSheet("font-size: 16px; font-weight: bold;")
        rv.addWidget(self.title)
        self.desc = label("")
        rv.addWidget(self.desc)
        g = QGroupBox("この場面で使う VisoMaster Fusion の設定（項目名はソースから。画面の表記は未確認）")
        gv = QVBoxLayout(g)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["項目", "値"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().resizeSection(0, 320)
        self.table.verticalHeader().setVisible(False)
        gv.addWidget(self.table, 1)
        r = QHBoxLayout()
        badd = QPushButton("行を追加")
        badd.clicked.connect(self._add_row)
        bbase = QPushButton("基本設定から追加…")
        bbase.clicked.connect(self._add_base)
        bdel = QPushButton("選んだ行を消す")
        bdel.clicked.connect(self._del_rows)
        for b in (badd, bbase, bdel):
            r.addWidget(b)
        r.addStretch(1)
        gv.addLayout(r)
        rv.addWidget(g, 2)
        rv.addWidget(QLabel("メモ（なぜこの値にしたか、気づいたこと）"))
        self.notes = QPlainTextEdit()
        self.notes.setMaximumHeight(110)
        rv.addWidget(self.notes)
        rv.addWidget(QLabel("根拠にした比較と品質チェック（チェックを付ける）"))
        self.evidence = QListWidget()
        rv.addWidget(self.evidence, 1)
        br = QHBoxLayout()
        self.state = label("")
        br.addWidget(self.state, 1)
        bsave = QPushButton("保存")
        bsave.clicked.connect(lambda: self._save(False))
        bdec = QPushButton("この設定に決める")
        bdec.setObjectName("primary")
        bdec.clicked.connect(lambda: self._save(True))
        br.addWidget(bsave)
        br.addWidget(bdec)
        rv.addLayout(br)
        h.addWidget(right, 1)

    def reload(self) -> None:
        cur = max(0, self.list.currentRow())
        self.list.blockSignals(True)
        self.list.clear()
        for p in prof.PROFILES:
            rec = prof.get_profile(self.win.project, p["id"])
            state = "決定" if rec.get("decided_at") else ("記録あり" if rec.get("updated_at") else "未決定")
            it = QListWidgetItem(f"{p['id']} {p['name']}　（{state}）")
            it.setData(Qt.UserRole, p["id"])
            self.list.addItem(it)
        self.list.blockSignals(False)
        self.list.setCurrentRow(cur)
        self._show(cur)

    def _show(self, row: int) -> None:
        if row < 0:
            return
        p = prof.PROFILES[row]
        self.pid = p["id"]
        rec = prof.get_profile(self.win.project, p["id"])
        self.title.setText(f"{p['id']} {p['name']}")
        self.desc.setText(f"例：{p['example']}<br>重点：{p['focus']}")
        rows = rec["settings"] or [[k, v] for k, v in p["settings"]]
        self.table.setRowCount(len(rows))
        for i, (k, val) in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(k))
            self.table.setItem(i, 1, QTableWidgetItem(val))
        self.notes.setPlainText(rec.get("notes", ""))
        self.evidence.clear()
        proj = self.win.project
        clips = {c["id"]: Path(c["file"]).name for c in proj.data["clips"]}
        renders = {r["id"]: _render_label(r) for r in proj.data.get("renders", [])}
        for c in reversed(proj.data.get("comparisons", [])):
            it = QListWidgetItem(f"比較　{c['created_at'][:16].replace('T', ' ')}　{clips.get(c['clip_id'], '')}　"
                                 + "｜".join(pn["label"] for pn in c["panels"]))
            it.setData(Qt.UserRole, c["id"])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if c["id"] in rec.get("evidence", []) else Qt.Unchecked)
            self.evidence.addItem(it)
        for q in reversed(proj.data.get("qc_reports", [])):
            verdict = "判定不可" if q["pass"] is None else ("目安内" if q["pass"] else "要確認")
            it = QListWidgetItem(f"品質チェック　{q['created_at'][:16].replace('T', ' ')}　"
                                 f"{renders.get(q['render_id'], '')}　{verdict}")
            it.setData(Qt.UserRole, q["id"])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if q["id"] in rec.get("evidence", []) else Qt.Unchecked)
            self.evidence.addItem(it)
        if rec.get("decided_at"):
            self.state.setText(f"決定 {rec['decided_at'][:16].replace('T', ' ')}")
        elif rec.get("updated_at"):
            self.state.setText(f"保存 {rec['updated_at'][:16].replace('T', ' ')}（未決定）")
        else:
            self.state.setText("仕様の推奨を表示しています（未保存）")

    def _add_row(self) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(""))
        self.table.setItem(r, 1, QTableWidgetItem(""))
        self.table.editItem(self.table.item(r, 0))

    def _add_base(self) -> None:
        items = [f"{k}　→　{v}" for k, v in prof.BASE_SETTINGS]
        choice, ok = QInputDialog.getItem(self, "基本設定から追加", "項目（仕様 10.1 の推奨）", items, 0, False)
        if not ok:
            return
        k, v = prof.BASE_SETTINGS[items.index(choice)]
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(k))
        self.table.setItem(r, 1, QTableWidgetItem(v))

    def _del_rows(self) -> None:
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)

    def _rows(self) -> list[tuple[str, str]]:
        out = []
        for r in range(self.table.rowCount()):
            k = self.table.item(r, 0)
            v = self.table.item(r, 1)
            out.append((k.text() if k else "", v.text() if v else ""))
        return out

    def _save(self, decided: bool) -> None:
        if not self.pid:
            return
        ev = [self.evidence.item(i).data(Qt.UserRole) for i in range(self.evidence.count())
              if self.evidence.item(i).checkState() == Qt.Checked]
        if decided and not ev and QMessageBox.question(
                self, "根拠がありません", "根拠にした比較や品質チェックが選ばれていません。このまま決めますか？") != QMessageBox.Yes:
            return
        try:
            prof.save_profile(self.win.project, self.pid, settings=self._rows(), notes=self.notes.toPlainText(),
                              evidence=ev, decided=decided)
        except Exception as e:  # noqa: BLE001
            error_box(self, "保存できませんでした", str(e))
            return
        self.reload()
        self.win.statusBar().showMessage("保存しました", 5000)

    def _export(self) -> None:
        p = self.win.project
        out = p.root / "export" / "scene_profiles.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(prof.export_markdown(p), encoding="utf-8")
        p.log("scene_profiles_exported", {"file": p.rel(out)})
        self.win.statusBar().showMessage(f"書き出しました: {out}", 10000)
        _open(out.parent)

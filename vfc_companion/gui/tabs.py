"""各画面（プロジェクト、素材、テスト用クリップ、顔素材、撮影ガイド）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFormLayout, QGridLayout,
                               QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QSpinBox, QSplitter,
                               QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget)

from ..clips import CLIP_FORMATS, EVENT_LABELS, cut_clip, load_scan, scan_face_events
from ..config import DEFAULT_SETTINGS
from ..face_assets import (IMAGE_EXTS, add_images, add_person, issue_texts, reanalyze_person, remove_image)
from ..faces import PITCH_BINS, YAW_BINS
from ..media import MediaInfo, check_guide, convert_source, import_source, probe
from .common import error_box, fmt_time, label, thumb
from .dialogs import ImportDialog, PersonDialog, media_form, warnings_widget
from .viewer import SEGMENT_COLORS, FrameFetcher, FrameView, PlaybackThread, Timeline

if TYPE_CHECKING:
    from .app import MainWindow


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            _clear(item.layout())


def _ro_item(text) -> QTableWidgetItem:
    it = QTableWidgetItem("" if text is None else str(text))
    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    return it


# =====================================================================
# プロジェクト（M1）
# =====================================================================
class ProjectTab(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        self.title = label("", wrap=False)
        self.title.setStyleSheet("font-size: 18px; font-weight: bold;")
        top.addWidget(self.title, 1)
        b = QPushButton("フォルダを開く")
        b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.win.project.root))))
        top.addWidget(b)
        v.addLayout(top)
        self.path = label("")
        v.addWidget(self.path)

        split = QSplitter()
        v.addWidget(split, 1)

        left = QWidget()
        lv = QVBoxLayout(left)
        g = QGroupBox("ツールのバージョン（自動で検出）")
        gl = QVBoxLayout(g)
        self.detected = QTextBrowser()
        self.detected.setMaximumHeight(170)
        gl.addWidget(self.detected)
        rb = QPushButton("検出し直す")
        rb.clicked.connect(self._refresh_tools)
        gl.addWidget(rb)
        lv.addWidget(g)

        g2 = QGroupBox("ツールのバージョン（手で記入）")
        f = QFormLayout(g2)
        self.manual: dict[str, QLineEdit] = {}
        for key, title, ph in (("visomaster_fusion", "VisoMaster Fusion", "例: 3.12.3（AlphaFace）"),
                               ("facefusion", "FaceFusion", "例: 3.9.1"),
                               ("davinci_resolve", "DaVinci Resolve", "例: 20.x")):
            e = QLineEdit()
            e.setPlaceholderText(ph)
            self.manual[key] = e
            f.addRow(title, e)
        sb = QPushButton("保存")
        sb.clicked.connect(self._save_manual)
        f.addRow("", sb)
        lv.addWidget(g2)

        g3 = QGroupBox("判定の基準値（初期値は【推測】を含む。変えると以後の検査に使われる）")
        g3l = QVBoxLayout(g3)
        self.settings = QTableWidget(0, 2)
        self.settings.setHorizontalHeaderLabels(["項目", "値"])
        self.settings.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.settings.verticalHeader().setVisible(False)
        self.settings.itemChanged.connect(self._setting_changed)
        g3l.addWidget(self.settings)
        lv.addWidget(g3, 1)
        split.addWidget(left)

        g4 = QGroupBox("処理の履歴")
        g4l = QVBoxLayout(g4)
        self.history = QTableWidget(0, 3)
        self.history.setHorizontalHeaderLabels(["日時", "処理", "内容"])
        self.history.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.history.verticalHeader().setVisible(False)
        self.history.setEditTriggers(QAbstractItemView.NoEditTriggers)
        g4l.addWidget(self.history)
        split.addWidget(g4)
        split.setSizes([480, 520])
        self._loading = False

    def reload(self) -> None:
        p = self.win.project
        self.title.setText(p.name)
        self.path.setText(str(p.root))
        det = p.data["tools"].get("detected", {})
        self.detected.setPlainText("\n".join(f"{k}: {v}" for k, v in det.items()))
        for key, e in self.manual.items():
            e.setText(p.data["tools"].get(key, {}).get("version", ""))
        self._loading = True
        s = p.settings
        self.settings.setRowCount(len(s))
        for r, (k, val) in enumerate(s.items()):
            self.settings.setItem(r, 0, _ro_item(k))
            self.settings.setItem(r, 1, QTableWidgetItem(str(val)))
        self._loading = False
        hist = list(reversed(p.data["history"]))[:300]
        self.history.setRowCount(len(hist))
        for r, h in enumerate(hist):
            self.history.setItem(r, 0, _ro_item(h["at"].replace("T", " ")[:19]))
            self.history.setItem(r, 1, _ro_item(h["action"]))
            self.history.setItem(r, 2, _ro_item(json.dumps(h.get("detail", {}), ensure_ascii=False)))

    def _refresh_tools(self) -> None:
        self.win.project.refresh_tool_versions()
        self.reload()

    def _save_manual(self) -> None:
        for key, e in self.manual.items():
            self.win.project.set_manual_tool_version(key, version=e.text().strip())
        self.reload()

    def _setting_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != 1:
            return
        key = self.settings.item(item.row(), 0).text()
        default = DEFAULT_SETTINGS[key]
        text = item.text().strip()
        try:
            if isinstance(default, bool):
                val = text.lower() in ("1", "true", "yes", "on")
            elif isinstance(default, int):
                val = int(float(text))
            elif isinstance(default, float):
                val = float(text)
            else:
                if key == "clip_format" and text not in CLIP_FORMATS:
                    raise ValueError("選べる形式: " + ", ".join(CLIP_FORMATS))
                val = text
        except ValueError as e:
            error_box(self, "値が正しくありません", f"{key}: {e}")
            self.reload()
            return
        self.win.project.set_setting(key, val)
        self.reload()


# =====================================================================
# 素材（M2）
# =====================================================================
class MediaTab(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        h = QHBoxLayout(self)
        left = QVBoxLayout()
        b = QPushButton("動画を取り込む…")
        b.setObjectName("primary")
        b.clicked.connect(self._choose)
        left.addWidget(b)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._show)
        left.addWidget(self.list, 1)
        lw = QWidget()
        lw.setLayout(left)
        lw.setMaximumWidth(340)
        h.addWidget(lw)

        self.detail_area = QScrollArea()
        self.detail_area.setWidgetResizable(True)
        self.detail = QWidget()
        self.dv = QVBoxLayout(self.detail)
        self.detail_area.setWidget(self.detail)
        h.addWidget(self.detail_area, 1)

    def reload(self) -> None:
        cur = self.list.currentRow()
        self.list.clear()
        for s in self.win.project.data["sources"]:
            n = len(s["warnings"])
            mark = "⚠ " if any(w["code"] in ("vfr", "hdr") for w in s["warnings"]) else ""
            it = QListWidgetItem(f"{mark}{Path(s['file']).name}  （警告 {n}）")
            it.setData(Qt.UserRole, s["id"])
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(min(max(cur, 0), self.list.count() - 1))
        else:
            _clear(self.dv)
            self.dv.addWidget(label("動画を取り込むと、ここに情報と警告が表示されます。"))
            self.dv.addStretch(1)

    def _choose(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "動画を選ぶ", "",
                                              "動画 (*.mp4 *.mov *.mkv *.avi *.m4v *.webm *.mts *.m2ts *.mxf);;すべて (*)")
        if not path:
            return
        settings = self.win.project.data.get("settings")

        def job(progress):
            progress(0, 0, "動画を調べています")
            info = probe(path, settings)
            return info, check_guide(info, settings)

        self.win.run_job(job, "動画を調べています", on_done=lambda res: self._confirm(path, *res))

    def _confirm(self, path: str, info: MediaInfo, warnings) -> None:
        dlg = ImportDialog(self, info, warnings)
        if dlg.exec() != ImportDialog.Accepted:
            return
        copy = dlg.copy.isChecked()

        def job(progress):
            return import_source(self.win.project, path, consent_confirmed=True, copy=copy, info=info,
                                 progress=lambda m: progress(0, 0, m))

        self.win.run_job(job, "取り込んでいます", on_done=lambda _r: self.win.reload_all(select_source=-1))

    def _show(self, row: int) -> None:
        _clear(self.dv)
        if row < 0:
            return
        s = self.win.project.data["sources"][row]
        info = MediaInfo.from_dict(s["media"])
        self.dv.addWidget(label(f"<b>{Path(s['file']).name}</b>　<span style='color:#9aa0aa'>{s['id']}</span>"))
        if s.get("derived_from"):
            self.dv.addWidget(label(f"{s['derived_from']} から変換したもの（{s.get('note', '')}）"))
        self.dv.addWidget(media_form(info))
        self.dv.addWidget(label("<b>撮影ガイドとの照合</b>"))
        self.dv.addWidget(warnings_widget(s["warnings"]))
        codes = {w["code"] for w in s["warnings"]}
        row_btn = QHBoxLayout()
        for text, cfr, sdr, need in (("固定フレームレート（CFR）に変換", True, False, "vfr"),
                                     ("SDR に変換", False, True, "hdr"),
                                     ("CFR と SDR に変換", True, True, "vfr+hdr")):
            b = QPushButton(text)
            b.setEnabled(all(n in codes for n in need.split("+")))
            b.clicked.connect(lambda _=False, sid=s["id"], c=cfr, d=sdr: self._convert(sid, c, d))
            row_btn.addWidget(b)
        row_btn.addStretch(1)
        self.dv.addLayout(row_btn)
        c = self.win.project.consent.find(s["consent_id"])
        if c:
            self.dv.addWidget(label(f"<span style='color:#9aa0aa'>確認記録 {c['id']}：{c['confirmed_at']} "
                                    f"{c['confirmed_by']}</span>"))
        self.dv.addStretch(1)

    def _convert(self, sid: str, cfr: bool, sdr: bool) -> None:
        def job(progress):
            progress(0, 0, "変換しています（長い動画は時間がかかります）")
            return convert_source(self.win.project, sid, to_cfr=cfr, to_sdr=sdr)

        self.win.run_job(job, "変換しています", on_done=lambda _r: self.win.reload_all(select_source=-1))


# =====================================================================
# テスト用クリップ（M3）
# =====================================================================
class ClipTab(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.info: MediaInfo | None = None
        self.source_id: str | None = None
        self.pos = 0
        self.in_f: int | None = None
        self.out_f: int | None = None
        self.play: PlaybackThread | None = None
        self.fetcher = FrameFetcher()
        self.fetcher.ready.connect(self._on_frame)
        self.fetcher.start()
        self._seek_timer = QTimer(self)
        self._seek_timer.setSingleShot(True)
        self._seek_timer.setInterval(40)
        self._seek_timer.timeout.connect(self._fetch)

        root = QHBoxLayout(self)
        split = QSplitter()
        root.addWidget(split)

        # ---- 左：プレビューとタイムライン ----
        left = QWidget()
        lv = QVBoxLayout(left)
        top = QHBoxLayout()
        top.addWidget(QLabel("元の動画"))
        self.source_combo = QComboBox()
        self.source_combo.currentIndexChanged.connect(self._source_changed)
        top.addWidget(self.source_combo, 1)
        lv.addLayout(top)
        self.view = FrameView()
        lv.addWidget(self.view, 1)
        self.timeline = Timeline()
        self.timeline.seek.connect(self.seek)
        lv.addWidget(self.timeline)
        ctl = QHBoxLayout()
        self.btn_prev = QPushButton("◀ 1f")
        self.btn_prev.clicked.connect(lambda: self.seek(self.pos - 1))
        self.btn_play = QPushButton("▶ 再生")
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_next = QPushButton("1f ▶")
        self.btn_next.clicked.connect(lambda: self.seek(self.pos + 1))
        self.frame_spin = QSpinBox()
        self.frame_spin.setRange(0, 0)
        self.frame_spin.setKeyboardTracking(False)
        self.frame_spin.valueChanged.connect(self.seek)
        self.time_label = QLabel("00:00:00.000")
        for wdg in (self.btn_prev, self.btn_play, self.btn_next, QLabel("フレーム"), self.frame_spin, self.time_label):
            ctl.addWidget(wdg)
        ctl.addStretch(1)
        b_in = QPushButton("開始点 [I]")
        b_in.clicked.connect(self.mark_in)
        b_out = QPushButton("終了点 [O]")
        b_out.clicked.connect(self.mark_out)
        ctl.addWidget(b_in)
        ctl.addWidget(b_out)
        lv.addLayout(ctl)
        self.range_label = label("範囲：未設定")
        lv.addWidget(self.range_label)
        split.addWidget(left)

        # ---- 右：区間の候補、切り出し、クリップ一覧 ----
        right = QWidget()
        rv = QVBoxLayout(right)
        g = QGroupBox("顔の区間の候補（9.3 のテスト条件を含むクリップ選び用）")
        gv = QVBoxLayout(g)
        hb = QHBoxLayout()
        self.btn_scan = QPushButton("顔の区間を解析")
        self.btn_scan.clicked.connect(self._scan)
        hb.addWidget(self.btn_scan)
        legend = " ".join(f"<span style='color:{SEGMENT_COLORS[k].name()}'>■</span>{v}"
                          for k, v in EVENT_LABELS.items())
        hb.addWidget(label(legend), 1)
        gv.addLayout(hb)
        self.seg_table = QTableWidget(0, 3)
        self.seg_table.setHorizontalHeaderLabels(["種類", "開始", "終了"])
        self.seg_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.seg_table.verticalHeader().setVisible(False)
        self.seg_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.seg_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.seg_table.cellClicked.connect(self._seg_clicked)
        gv.addWidget(self.seg_table)
        gv.addWidget(label("行をクリックすると、その区間の前後 1 秒を含めて範囲に設定します。"))
        rv.addWidget(g, 1)

        g2 = QGroupBox("切り出し")
        f = QFormLayout(g2)
        self.fmt_combo = QComboBox()
        for k, d in CLIP_FORMATS.items():
            self.fmt_combo.addItem(d["label"], k)
        f.addRow("形式", self.fmt_combo)
        f.addRow("", label("VisoMaster Fusion で読めるかは実機で確認してください（C10）。"))
        self.note = QLineEdit()
        self.note.setPlaceholderText("例: 回転で顔が消える場面")
        f.addRow("メモ", self.note)
        self.btn_cut = QPushButton("切り出す")
        self.btn_cut.setObjectName("primary")
        self.btn_cut.clicked.connect(self._cut)
        f.addRow("", self.btn_cut)
        rv.addWidget(g2)

        g3 = QGroupBox("切り出したクリップ")
        g3v = QVBoxLayout(g3)
        self.clip_table = QTableWidget(0, 4)
        self.clip_table.setHorizontalHeaderLabels(["ファイル", "元の動画での範囲", "フレーム数", "メモ"])
        self.clip_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.clip_table.verticalHeader().setVisible(False)
        self.clip_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.clip_table.cellDoubleClicked.connect(self._clip_open)
        g3v.addWidget(self.clip_table)
        g3v.addWidget(label("ダブルクリックでファイルの場所を開きます。"))
        rv.addWidget(g3, 1)
        split.addWidget(right)
        split.setSizes([760, 440])

        for key, fn in (("I", self.mark_in), ("O", self.mark_out), ("Left", lambda: self.seek(self.pos - 1)),
                        ("Right", lambda: self.seek(self.pos + 1)), ("Space", self.toggle_play),
                        ("Shift+Left", lambda: self.seek(self.pos - 10)),
                        ("Shift+Right", lambda: self.seek(self.pos + 10))):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)

    # ---------- 読み込み ----------
    def reload(self) -> None:
        p = self.win.project
        keep = self.source_id
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        for s in p.data["sources"]:
            self.source_combo.addItem(f"{Path(s['file']).name}（{s['id']}）", s["id"])
        self.source_combo.blockSignals(False)
        idx = max(0, self.source_combo.findData(keep)) if keep else 0
        if self.source_combo.count():
            self.source_combo.setCurrentIndex(idx)
            self._source_changed(idx)
        fmt = p.settings["clip_format"]
        self.fmt_combo.setCurrentIndex(max(0, self.fmt_combo.findData(fmt)))
        self._reload_clips()

    def _reload_clips(self) -> None:
        clips = self.win.project.data["clips"]
        self.clip_table.setRowCount(len(clips))
        for r, c in enumerate(clips):
            self.clip_table.setItem(r, 0, _ro_item(Path(c["file"]).name))
            self.clip_table.setItem(r, 1, _ro_item(
                f"{c['source_id']}  {c['start_frame']}〜{c['end_frame'] - 1}f "
                f"（{fmt_time(c['start_time'])}〜{fmt_time(c['end_time'])}）"))
            self.clip_table.setItem(r, 2, _ro_item(c["frame_count"]))
            self.clip_table.setItem(r, 3, _ro_item(c.get("note", "")))

    def _source_changed(self, idx: int) -> None:
        self.stop_play()
        sid = self.source_combo.itemData(idx)
        if not sid:
            return
        if sid != self.source_id:
            self.in_f = self.out_f = None
            self.pos = 0
        self.source_id = sid
        src = self.win.project.source(sid)
        self.info = MediaInfo.from_dict(src["media"])
        self.path = str(self.win.project.abs(src["file"]))
        self.frame_spin.blockSignals(True)
        self.frame_spin.setRange(0, max(0, self.info.frame_count - 1))
        self.frame_spin.blockSignals(False)
        scan = load_scan(self.win.project, sid)
        segs = scan["segments"] if scan else []
        self._fill_segments(segs)
        self.timeline.set_state(total=self.info.frame_count, pos=self.pos, in_f=self.in_f, out_f=self.out_f,
                                segments=segs)
        self.seek(self.pos, force=True)

    def _fill_segments(self, segs: list[dict]) -> None:
        self.segments = segs
        self.seg_table.setRowCount(len(segs))
        for r, s in enumerate(segs):
            it = _ro_item(s["label"])
            it.setForeground(SEGMENT_COLORS.get(s["kind"]))
            self.seg_table.setItem(r, 0, it)
            self.seg_table.setItem(r, 1, _ro_item(fmt_time(s["start_time"])))
            self.seg_table.setItem(r, 2, _ro_item(fmt_time(s["end_time"])))

    # ---------- 移動と表示 ----------
    def seek(self, frame: int, force: bool = False) -> None:
        if not self.info:
            return
        frame = max(0, min(self.info.frame_count - 1, int(frame)))
        if frame == self.pos and not force:
            return
        self.stop_play()
        self.pos = frame
        self._update_pos_widgets()
        self._seek_timer.start()

    def _update_pos_widgets(self) -> None:
        self.frame_spin.blockSignals(True)
        self.frame_spin.setValue(self.pos)
        self.frame_spin.blockSignals(False)
        self.time_label.setText(fmt_time(self.pos / self.info.fps))
        self.timeline.set_state(pos=self.pos)

    def _fetch(self) -> None:
        if self.info:
            self.fetcher.request(self.path, self.pos, self.info.fps, self.info.display_width,
                                 self.info.display_height)

    def _on_frame(self, frame: int, img) -> None:
        if frame == self.pos:
            self.view.set_image(img)

    def toggle_play(self) -> None:
        if self.play:
            self.stop_play()
            return
        if not self.info:
            return
        end = self.out_f if (self.out_f and self.out_f > self.pos) else self.info.frame_count
        self.play = PlaybackThread(self.path, self.pos, self.info.fps, self.info.display_width,
                                   self.info.display_height, end)
        self.play.frame.connect(self._on_play_frame)
        self.play.finished.connect(self._play_finished)
        self.play.start()
        self.btn_play.setText("■ 停止")

    def _on_play_frame(self, frame: int, img) -> None:
        self.pos = frame
        self._update_pos_widgets()
        self.view.set_image(img)

    def _play_finished(self) -> None:
        self.play = None
        self.btn_play.setText("▶ 再生")

    def stop_play(self) -> None:
        if self.play:
            self.play.stop()
            self.play = None
            self.btn_play.setText("▶ 再生")

    # ---------- 範囲 ----------
    def mark_in(self) -> None:
        self.in_f = self.pos
        if self.out_f is not None and self.out_f <= self.in_f:
            self.out_f = None
        self._update_range()

    def mark_out(self) -> None:
        self.out_f = self.pos + 1  # 今のフレームまで含める
        if self.in_f is not None and self.in_f >= self.out_f:
            self.in_f = None
        self._update_range()

    def _update_range(self) -> None:
        self.timeline.set_state(in_f=self.in_f, out_f=self.out_f)
        if self.in_f is None or self.out_f is None or not self.info:
            self.range_label.setText(f"範囲：開始 {self.in_f if self.in_f is not None else '未設定'}"
                                     f" / 終了 {self.out_f - 1 if self.out_f is not None else '未設定'}")
            return
        n = self.out_f - self.in_f
        sec = n / self.info.fps
        hint = "" if 5 <= sec <= 10 else "　（テスト用クリップの目安は 5〜10 秒）"
        self.range_label.setText(f"範囲：{self.in_f}〜{self.out_f - 1} フレーム（{n} フレーム、{sec:.2f} 秒）{hint}")

    def _seg_clicked(self, row: int, _col: int) -> None:
        s = self.segments[row]
        pad = int(round(self.info.fps))
        self.in_f = max(0, s["start_frame"] - pad)
        self.out_f = min(self.info.frame_count, s["end_frame"] + pad)
        self._update_range()
        self.seek(s["start_frame"])

    # ---------- 処理 ----------
    def _scan(self) -> None:
        if not self.source_id:
            return
        sid = self.source_id
        self.stop_play()

        def job(progress):
            return scan_face_events(self.win.project, sid, progress=lambda i, n: progress(i, n, "顔を解析しています"),
                                    should_stop=lambda: self.win.cancel_requested)

        def done(res):
            if res["source_id"] == self.source_id:
                self._fill_segments(res["segments"])
                self.timeline.set_state(segments=res["segments"])
            self.win.reload_all()

        self.win.run_job(job, "顔の区間を解析しています（初回はモデルを読み込みます）", on_done=done, cancellable=True)

    def _cut(self) -> None:
        if self.in_f is None or self.out_f is None:
            error_box(self, "範囲が未設定です", "開始点 [I] と終了点 [O] を設定してください。")
            return
        sid, a, b = self.source_id, self.in_f, self.out_f
        fmt, note = self.fmt_combo.currentData(), self.note.text().strip()
        self.stop_play()

        def job(progress):
            progress(0, 0, "切り出しています")
            return cut_clip(self.win.project, sid, a, b, fmt_key=fmt, note=note)

        def done(rec):
            self.win.reload_all()
            if rec.get("warning"):
                error_box(self, "確認してください", rec["warning"])
            else:
                self.win.statusBar().showMessage(f"切り出しました: {rec['file']}", 8000)

        self.win.run_job(job, "切り出しています", on_done=done)

    def _clip_open(self, row: int, _col: int) -> None:
        c = self.win.project.data["clips"][row]
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.win.project.abs(c["file"]).parent)))

    def shutdown(self) -> None:
        self.stop_play()
        self.fetcher.stop()


# =====================================================================
# 顔素材（M4）
# =====================================================================
class FacesTab(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        h = QHBoxLayout(self)
        left = QVBoxLayout()
        b = QPushButton("AI 人物を追加…")
        b.setObjectName("primary")
        b.clicked.connect(self._add_person)
        left.addWidget(b)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._show)
        left.addWidget(self.list, 1)
        lw = QWidget()
        lw.setLayout(left)
        lw.setMaximumWidth(280)
        h.addWidget(lw)

        right = QVBoxLayout()
        bar = QHBoxLayout()
        self.title = label("", wrap=False)
        self.title.setStyleSheet("font-size: 16px; font-weight: bold;")
        bar.addWidget(self.title, 1)
        self.btn_add = QPushButton("写真をまとめて追加…")
        self.btn_add.clicked.connect(self._add_images)
        self.btn_re = QPushButton("検査し直す")
        self.btn_re.clicked.connect(self._reanalyze)
        self.btn_del = QPushButton("選んだ写真を外す")
        self.btn_del.clicked.connect(self._remove)
        for wdg in (self.btn_add, self.btn_re, self.btn_del):
            bar.addWidget(wdg)
        right.addLayout(bar)

        self.cov_box = QGroupBox("角度ごとの枚数（「左」「右」は画面から見た向き）")
        self.cov = QGridLayout(self.cov_box)
        right.addWidget(self.cov_box)
        self.shortage = label("")
        right.addWidget(self.shortage)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["写真", "ファイル", "向き", "左右/上下/傾き", "顔の大きさ",
                                              "似ている度合い", "検査結果"])
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setIconSize(QSize(72, 72))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        right.addWidget(self.table, 1)
        right.addWidget(label("似ている度合いは、その写真とほかの写真の平均とのコサイン類似度（ArcFace）。"
                              "メガネの判定は簡易なもので、誤判定がありえます。上下の向きの符号は未確認です"
                              "（上向きの写真が「下向き」と出たら、基準値 pitch_up_sign を -1 に）。"))
        rw = QWidget()
        rw.setLayout(right)
        h.addWidget(rw, 1)

    def reload(self) -> None:
        cur = self.list.currentRow()
        self.list.clear()
        for p in self.win.project.data["persons"]:
            s = p.get("summary") or {}
            it = QListWidgetItem(f"{p['name']}　{s.get('usable', 0)}/{s.get('total', 0)} 枚")
            it.setData(Qt.UserRole, p["id"])
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(min(max(cur, 0), self.list.count() - 1))
        else:
            self._show(-1)

    def _person(self):
        row = self.list.currentRow()
        if row < 0:
            return None
        return self.win.project.data["persons"][row]

    def _show(self, row: int) -> None:
        p = self._person() if row >= 0 else None
        for b in (self.btn_add, self.btn_re, self.btn_del):
            b.setEnabled(p is not None)
        _clear(self.cov)
        if p is None:
            self.title.setText("AI 人物を追加してください")
            self.shortage.setText("")
            self.table.setRowCount(0)
            return
        s = p.get("summary") or {}
        target = self.win.project.settings["target_images_per_person"]
        self.title.setText(f"{p['name']}（{p['id']}）　{s.get('usable', 0)} 枚使用可 / 目安 {target} 枚")
        yc, pc = s.get("yaw_counts", {}), s.get("pitch_counts", {})
        for col, (k, lbl, *_r) in enumerate(YAW_BINS):
            self.cov.addWidget(QLabel(lbl), 0, col, Qt.AlignCenter)
            n = yc.get(k, 0)
            self.cov.addWidget(label(str(n), "bad" if n == 0 else None), 1, col, Qt.AlignCenter)
        for col, (k, lbl, *_r) in enumerate(PITCH_BINS):
            self.cov.addWidget(QLabel(lbl), 2, col, Qt.AlignCenter)
            n = pc.get(k, 0)
            self.cov.addWidget(label(str(n), "bad" if n == 0 and k != "level" else None), 3, col, Qt.AlignCenter)
        short = s.get("shortages", [])
        sim = s.get("similarity_min")
        txt = ("不足：" + "、".join(short)) if short else "角度の偏りは見つかりませんでした。"
        if sim is not None:
            txt += f"　似ている度合い 最小 {sim:.2f} / 平均 {s.get('similarity_mean', 0):.2f}"
        self.shortage.setText(txt)
        self.shortage.setObjectName("warn" if short else "ok")
        self.shortage.style().polish(self.shortage)

        settings = self.win.project.settings
        imgs = p["images"]
        self.table.setRowCount(len(imgs))
        for r, img in enumerate(imgs):
            a = img["analysis"]
            f = a.get("face") or {}
            ic = QTableWidgetItem()
            ic.setIcon(QIcon(thumb(str(self.win.project.abs(img["file"])), 72)))
            ic.setData(Qt.UserRole, img["id"])
            self.table.setItem(r, 0, ic)
            self.table.setItem(r, 1, _ro_item(img["original_name"]))
            self.table.setItem(r, 2, _ro_item(f"{f.get('yaw_label', '')} / {f.get('pitch_label', '')}" if f else "—"))
            self.table.setItem(r, 3, _ro_item(f"{f['yaw']:+.0f}° / {f['pitch']:+.0f}° / {f['roll']:+.0f}°" if f else "—"))
            self.table.setItem(r, 4, _ro_item(f"{f['face_px']:.0f}px" if f else "—"))
            sv = a.get("similarity_to_others")
            self.table.setItem(r, 5, _ro_item("—" if sv is None else f"{sv:.2f}"))
            issues = issue_texts(a, settings)
            it = _ro_item("、".join(issues) if issues else "OK")
            it.setForeground(Qt.yellow if issues else Qt.green)
            self.table.setItem(r, 6, it)
            self.table.setRowHeight(r, 78)

    def _add_person(self) -> None:
        dlg = PersonDialog(self)
        if dlg.exec() != PersonDialog.Accepted:
            return
        try:
            add_person(self.win.project, dlg.name.text().strip(), consent_confirmed=True, note=dlg.note.text().strip())
        except Exception as e:  # noqa: BLE001
            error_box(self, "追加できませんでした", str(e))
            return
        self.win.reload_all()
        self.list.setCurrentRow(self.list.count() - 1)

    def _add_images(self) -> None:
        p = self._person()
        if not p:
            return
        pats = " ".join("*" + e for e in sorted(IMAGE_EXTS))
        files, _ = QFileDialog.getOpenFileNames(self, "顔写真を選ぶ（複数可）", "", f"画像 ({pats})")
        if not files:
            return
        pid = p["id"]

        def job(progress):
            return add_images(self.win.project, pid, files,
                              progress=lambda i, n, name: progress(i, n, f"検査しています {name}"))

        self.win.run_job(job, "顔写真を検査しています（初回はモデルを読み込みます）",
                         on_done=lambda _r: self.win.reload_all())

    def _reanalyze(self) -> None:
        p = self._person()
        if not p:
            return
        pid = p["id"]

        def job(progress):
            progress(0, 0, "検査し直しています")
            return reanalyze_person(self.win.project, pid)

        self.win.run_job(job, "検査し直しています", on_done=lambda _r: self.win.reload_all())

    def _remove(self) -> None:
        p = self._person()
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not p or not rows:
            return
        if QMessageBox.question(self, "写真を外す", f"{len(rows)} 枚をこの人物から外し、ファイルを削除します。") \
                != QMessageBox.Yes:
            return
        ids = [self.table.item(r, 0).data(Qt.UserRole) for r in rows]
        for iid in ids:
            remove_image(self.win.project, p["id"], iid)
        self.win.reload_all()


# =====================================================================
# 撮影ガイドと利用条件（9.1、9.2、3 章）
# =====================================================================
GUIDE_HTML = """
<h2>撮影ガイド（仕様 9.1）</h2>
<ul>
<li>4K・60fps で撮る。全身が映る場合は特に 4K が必須</li>
<li>シャッタースピードを速めにする（目安は 1/120 秒以上）</li>
<li>顔に均一に光を当てる。強い影や逆光は避ける</li>
<li>髪は顔にかからないようにまとめる</li>
<li>腕や道具で顔が隠れる時間が短くなるカメラ位置にする</li>
<li>回転の多い動きは、正面から撮ったものを本番用にする</li>
<li>可能なら SDR で撮る（VisoMaster Fusion の HDR の扱いは未確認：C9）</li>
</ul>
<h2>AI の顔素材（仕様 9.2）</h2>
<ul>
<li>基準になる正面の顔を 1 枚作り、同じ人物のまま角度や照明を変えた写真を作る</li>
<li>合計 10〜20 枚。動きの多い動画なら 20 枚前後</li>
<li>角度：正面、左右の斜め 45 度、真横に近い横顔、少し上向き、少し下向き、見上げ、見下ろし</li>
<li>照明：明るさと光の向きに幅を持たせる。表情は無表情か軽い笑顔でよい</li>
<li>高解像度にする（顔の部分 512px 以上）。メガネ、フィルター、強い加工、顔にかかる髪は避ける</li>
<li>年齢、肌の色、体つきを動画の人物に合わせる。体が多く映る動画では首や体の肌の色との差に注意</li>
</ul>
<h2>テスト用クリップ（仕様 9.3）</h2>
<ul>
<li>5〜10 秒。表情が激しい、顔が傾く、顔が隠れる、回転で顔が消えて再び見える、全身の引きの画、
速い動き、髪が顔にかかる、を含める</li>
<li>1 回に 1 つの設定だけを変えて比べる</li>
</ul>
<h2>利用条件（仕様 3 章、必須）</h2>
<ul>
<li><b>S1</b> 置き換え先の顔は、AI で作った実在しない人物に限る。実在の人物や、実在の人物に似せた顔は使わない</li>
<li><b>S2</b> 元の動画は、本人が映っているもの、または映っている人から了承を得たものに限る</li>
<li><b>S3</b> 公開する動画には、各プラットフォームのルールに従って「AI で加工・改変した」表示をする</li>
<li><b>S4</b> ツールの安全機能（FaceFusion のコンテンツ判定など）を無効にしたり回避したりしない</li>
</ul>
"""


class GuideTab(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        v = QVBoxLayout(self)
        t = QTextBrowser()
        t.setHtml(GUIDE_HTML)
        v.addWidget(t)

    def reload(self) -> None:
        pass

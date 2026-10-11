"""メインウィンドウ。"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QApplication, QFileDialog, QInputDialog, QLabel, QMainWindow, QMessageBox,
                               QProgressBar, QPushButton, QStackedWidget, QTabWidget, QVBoxLayout, QWidget)

from .. import SPEC_VERSION, __version__
from ..project import PROJECT_FILE, Project
from .common import STYLE, Worker, error_box, label, start_worker
from .phase2 import CompareTab, DfmTab, ProfilesTab, QcTab
from .tabs import ClipTab, FacesTab, GuideTab, MediaTab, ProjectTab

APP_NAME = "顔置き換えコンパニオン"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.project: Project | None = None
        self.busy = False
        self.cancel_requested = False
        self._worker: Worker | None = None
        self.qsettings = QSettings("vfc", "companion")
        self.setWindowTitle(APP_NAME)
        self.resize(1440, 900)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.stack.addWidget(self._welcome())
        self.tabs = QTabWidget()
        self.project_tab = ProjectTab(self)
        self.media_tab = MediaTab(self)
        self.clip_tab = ClipTab(self)
        self.faces_tab = FacesTab(self)
        self.compare_tab = CompareTab(self)
        self.qc_tab = QcTab(self)
        self.profiles_tab = ProfilesTab(self)
        self.dfm_tab = DfmTab(self)
        self.guide_tab = GuideTab(self)
        for w, t in ((self.project_tab, "プロジェクト"), (self.media_tab, "素材"),
                     (self.clip_tab, "テスト用クリップ"), (self.faces_tab, "顔素材"),
                     (self.compare_tab, "比較"), (self.qc_tab, "品質チェック"),
                     (self.profiles_tab, "シーン別の設定"), (self.dfm_tab, "DFM 学習素材"),
                     (self.guide_tab, "撮影ガイドと利用条件")):
            self.tabs.addTab(w, t)
        self.tabs.currentChanged.connect(lambda _i: self.clip_tab.stop_play())
        self.stack.addWidget(self.tabs)

        self.progress = QProgressBar()
        self.progress.setMaximumWidth(260)
        self.progress.setVisible(False)
        self.cancel_btn = QPushButton("中止")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._cancel)
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().addPermanentWidget(self.cancel_btn)
        self._menus()

    # ---------- 画面 ----------
    def _welcome(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addStretch(1)
        t = QLabel(APP_NAME)
        t.setStyleSheet("font-size: 26px; font-weight: bold;")
        t.setAlignment(Qt.AlignCenter)
        v.addWidget(t)
        sub = label(f"VisoMaster Fusion の前後の作業を自動化するツール（仕様書 v{SPEC_VERSION}、ツール v{__version__}）")
        sub.setAlignment(Qt.AlignCenter)
        v.addWidget(sub)
        for text, fn in (("新しいプロジェクト…", self.new_project), ("プロジェクトを開く…", self.open_project_dialog)):
            b = QPushButton(text)
            b.setObjectName("primary")
            b.setFixedWidth(280)
            b.clicked.connect(fn)
            v.addWidget(b, 0, Qt.AlignCenter)
        recent = self.qsettings.value("recent", [], list) or []
        for r in recent[:5]:
            if (Path(r) / PROJECT_FILE).exists():
                b = QPushButton(f"最近：{r}")
                b.setFixedWidth(520)
                b.clicked.connect(lambda _=False, p=r: self.open_project(p))
                v.addWidget(b, 0, Qt.AlignCenter)
        v.addStretch(2)
        return w

    def _menus(self) -> None:
        m = self.menuBar().addMenu("ファイル")
        for text, fn, key in (("新しいプロジェクト…", self.new_project, "Ctrl+N"),
                              ("プロジェクトを開く…", self.open_project_dialog, "Ctrl+O")):
            a = QAction(text, self)
            a.setShortcut(key)
            a.triggered.connect(fn)
            m.addAction(a)
        m.addSeparator()
        q = QAction("終了", self)
        q.triggered.connect(self.close)
        m.addAction(q)

    # ---------- プロジェクト ----------
    def new_project(self) -> None:
        if self.busy:
            return
        parent = QFileDialog.getExistingDirectory(self, "プロジェクトを作る場所（親フォルダ）を選ぶ")
        if not parent:
            return
        name, ok = QInputDialog.getText(self, "新しいプロジェクト", "プロジェクト名（フォルダ名になります）")
        if not ok or not name.strip():
            return
        try:
            proj = Project.create(Path(parent) / name.strip(), name.strip())
        except Exception as e:  # noqa: BLE001
            error_box(self, "作れませんでした", str(e))
            return
        self._set_project(proj)

    def open_project_dialog(self) -> None:
        if self.busy:
            return
        d = QFileDialog.getExistingDirectory(self, "プロジェクトのフォルダを選ぶ")
        if d:
            self.open_project(d)

    def open_project(self, root: str) -> None:
        try:
            proj = Project.open(root)
        except Exception as e:  # noqa: BLE001
            error_box(self, "開けませんでした", str(e))
            return
        self._set_project(proj)

    def _set_project(self, proj: Project) -> None:
        self.clip_tab.stop_play()
        self.project = proj
        recent = [str(proj.root)] + [r for r in (self.qsettings.value("recent", [], list) or [])
                                     if r != str(proj.root)]
        self.qsettings.setValue("recent", recent[:8])
        self.setWindowTitle(f"{proj.name} — {APP_NAME}")
        self.stack.setCurrentWidget(self.tabs)
        self.clip_tab.source_id = None
        self.reload_all()

    def reload_all(self, select_source: int | None = None) -> None:
        if not self.project:
            return
        for t in (self.project_tab, self.media_tab, self.clip_tab, self.faces_tab, self.compare_tab, self.qc_tab,
                  self.profiles_tab, self.dfm_tab, self.guide_tab):
            t.reload()
        if select_source is not None and self.media_tab.list.count():
            self.media_tab.list.setCurrentRow(select_source % self.media_tab.list.count())

    # ---------- 裏の処理 ----------
    def run_job(self, fn, text: str, on_done=None, cancellable: bool = False) -> None:
        if self.busy:
            QMessageBox.information(self, "処理中です", "ほかの処理が終わるまでお待ちください。")
            return
        self.busy = True
        self.cancel_requested = False
        self.tabs.setEnabled(False)
        self.statusBar().showMessage(text)
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        self.cancel_btn.setVisible(cancellable)
        w = Worker(fn)
        w.signals.progress.connect(self._on_progress)
        w.signals.finished.connect(lambda res: self._job_done(res, on_done))
        w.signals.failed.connect(self._job_failed)
        self._worker = w
        start_worker(w)

    def _on_progress(self, i: int, n: int, text: str) -> None:
        if n > 0:
            self.progress.setRange(0, n)
            self.progress.setValue(i)
        else:
            self.progress.setRange(0, 0)
        if text:
            self.statusBar().showMessage(text)

    def _finish(self) -> None:
        self.busy = False
        self._worker = None
        self.tabs.setEnabled(True)
        self.progress.setVisible(False)
        self.cancel_btn.setVisible(False)

    def _job_done(self, res, on_done) -> None:
        self._finish()
        self.statusBar().showMessage("完了しました", 5000)
        if on_done:
            on_done(res)

    def _job_failed(self, msg: str) -> None:
        self._finish()
        self.statusBar().clearMessage()
        if self.project:
            self.reload_all()
        error_box(self, "処理に失敗しました", msg)

    def _cancel(self) -> None:
        self.cancel_requested = True
        self.statusBar().showMessage("中止しています…")

    def closeEvent(self, e) -> None:  # noqa: N802
        if self.busy and QMessageBox.question(self, "処理中です", "処理中ですが終了しますか？") != QMessageBox.Yes:
            e.ignore()
            return
        self.clip_tab.shutdown()
        self.qc_tab.shutdown()
        super().closeEvent(e)


def main(argv: list[str] | None = None) -> int:
    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName(APP_NAME)
    app.setStyleSheet(STYLE)
    win = MainWindow()
    args = app.arguments()[1:]
    if args and Path(args[0]).exists():
        win.open_project(args[0])
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

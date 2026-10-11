"""GUI の共通部品（裏で処理を走らせる仕組み、見た目、画像の変換）。"""

from __future__ import annotations

import traceback
from typing import Any, Callable

import numpy as np
from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel, QMessageBox, QWidget

STYLE = """
QWidget { background: #1e1f22; color: #dcdfe4; font-size: 13px; }
QTabWidget::pane { border: 1px solid #3a3d42; }
QTabBar::tab { background: #2b2d31; padding: 7px 16px; border: 1px solid #3a3d42; border-bottom: none; }
QTabBar::tab:selected { background: #3a3d42; color: #ffffff; }
QPushButton { background: #3a3d42; border: 1px solid #4a4e55; padding: 5px 12px; border-radius: 3px; }
QPushButton:hover { background: #4a4e55; }
QPushButton:disabled { color: #6c7078; }
QPushButton#primary { background: #2f6fde; border-color: #2f6fde; color: white; }
QPushButton#primary:disabled { background: #2b3a55; color: #8892a6; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextBrowser {
    background: #2b2d31; border: 1px solid #3a3d42; padding: 3px; }
QListWidget, QTableWidget, QTreeWidget { background: #25272b; alternate-background-color: #2a2c30;
    border: 1px solid #3a3d42; }
QHeaderView::section { background: #2b2d31; padding: 4px; border: none; border-right: 1px solid #3a3d42; }
QGroupBox { border: 1px solid #3a3d42; margin-top: 14px; padding-top: 6px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: #9aa0aa; }
QLabel#warn { color: #f0b54a; }
QLabel#bad { color: #ef6b6b; }
QLabel#ok { color: #6fcf8f; }
QSlider::groove:horizontal { height: 6px; background: #3a3d42; }
QSlider::handle:horizontal { width: 12px; margin: -6px 0; background: #dcdfe4; border-radius: 2px; }
"""


class WorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int, str)


class Worker(QRunnable):
    """重い処理を別のスレッドで動かす。fn には progress(i, n, text) が渡される。"""

    def __init__(self, fn: Callable[..., Any], *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = WorkerSignals()
        self.stop_requested = False

    def run(self) -> None:
        try:
            res = self.fn(*self.args, progress=self._progress, **self.kwargs)
        except Exception as e:  # noqa: BLE001
            msg = f"{e}\n\n{traceback.format_exc(limit=4)}"
            self.signals.failed.emit(msg)
        else:
            self.signals.finished.emit(res)

    def _progress(self, i: int, n: int, text: str = "") -> None:
        self.signals.progress.emit(int(i), int(n), text)


def start_worker(worker: Worker) -> None:
    QThreadPool.globalInstance().start(worker)


def bgr_to_qimage(img: np.ndarray) -> QImage:
    h, w = img.shape[:2]
    rgb = np.ascontiguousarray(img[:, :, ::-1])
    q = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888)
    return q.copy()


def error_box(parent: QWidget, title: str, text: str) -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle(title)
    first, _, rest = text.partition("\n\n")
    box.setText(first)
    if rest:
        box.setDetailedText(rest)
    box.exec()


def label(text: str = "", name: str | None = None, wrap: bool = True) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(wrap)
    lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
    if name:
        lbl.setObjectName(name)
    return lbl


def thumb(path: str, size: int = 96) -> QPixmap:
    pm = QPixmap(path)
    if pm.isNull():
        return pm
    return pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def fmt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(int(m), 60)
    return f"{h:02d}:{m:02d}:{s:06.3f}"

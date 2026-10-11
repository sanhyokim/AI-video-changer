"""プレビューとタイムライン（フレーム単位で正確に移動できる）。"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QRectF, QSize, Qt, QThread, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

from ..frames import iter_frames, read_frame
from .common import bgr_to_qimage

PREVIEW_WIDTH = 960

SEGMENT_COLORS = {
    "no_face": QColor("#ef6b6b"),
    "reappear": QColor("#f08c4a"),
    "tilted": QColor("#c58af0"),
    "small": QColor("#4ab4f0"),
    "multi": QColor("#6fcf8f"),
}


class FrameView(QLabel):
    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(480, 270)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("background: #000;")
        self._img: QImage | None = None

    def set_image(self, img: QImage | None) -> None:
        self._img = img
        self._refresh()

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._refresh()

    def _refresh(self) -> None:
        if self._img is None:
            self.clear()
            return
        pm = QPixmap.fromImage(self._img).scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.setPixmap(pm)


class FrameFetcher(QThread):
    """最後に頼まれたフレームだけを読み込む（スライダーを速く動かしても詰まらない）。"""

    ready = Signal(int, QImage)

    def __init__(self):
        super().__init__()
        self._cv = threading.Condition()
        self._req: tuple | None = None
        self._quit = False

    def request(self, path: str, frame: int, fps: float, w: int, h: int) -> None:
        with self._cv:
            self._req = (path, frame, fps, w, h)
            self._cv.notify()

    def stop(self) -> None:
        with self._cv:
            self._quit = True
            self._cv.notify()
        self.wait(3000)

    def run(self) -> None:
        while True:
            with self._cv:
                while self._req is None and not self._quit:
                    self._cv.wait()
                if self._quit:
                    return
                req, self._req = self._req, None
            path, frame, fps, w, h = req
            img = read_frame(path, frame, fps, w, h, max_width=PREVIEW_WIDTH)
            if img is not None:
                self.ready.emit(frame, bgr_to_qimage(img))


class PlaybackThread(QThread):
    """プレビューの再生。元のフレームレートに合わせて順番に読み込む。"""

    frame = Signal(int, QImage)

    def __init__(self, path: str, start: int, fps: float, w: int, h: int, end: int):
        super().__init__()
        self.args = (path, start, fps, w, h, end)
        self._stop = False

    def stop(self) -> None:
        self._stop = True
        self.wait(3000)

    def run(self) -> None:
        path, start, fps, w, h, end = self.args
        t0 = time.perf_counter()
        for n, (idx, img) in enumerate(iter_frames(path, fps, w, h, start_frame=start, max_width=PREVIEW_WIDTH)):
            if self._stop or idx >= end:
                break
            self.frame.emit(idx, bgr_to_qimage(img))
            delay = t0 + (n + 1) / fps - time.perf_counter()
            if delay > 0:
                time.sleep(delay)


class Timeline(QWidget):
    """フレーム位置、切り出す範囲、顔の区間の候補を表示する帯。クリックで移動する。"""

    seek = Signal(int)

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(46)
        self.total = 1
        self.pos = 0
        self.in_f: int | None = None
        self.out_f: int | None = None
        self.segments: list[dict] = []
        self.setMouseTracking(False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(600, 46)

    def set_state(self, total: int | None = None, pos: int | None = None, in_f=..., out_f=...,
                  segments: list[dict] | None = None) -> None:
        if total is not None:
            self.total = max(1, total)
        if pos is not None:
            self.pos = pos
        if in_f is not ...:
            self.in_f = in_f
        if out_f is not ...:
            self.out_f = out_f
        if segments is not None:
            self.segments = segments
        self.update()

    def _x(self, f: float) -> float:
        return f / self.total * (self.width() - 2) + 1

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor("#25272b"))
        # 顔の区間（上の帯）
        lanes = ["no_face", "reappear", "tilted", "small", "multi"]
        lane_h = 4
        for seg in self.segments:
            lane = lanes.index(seg["kind"]) if seg["kind"] in lanes else 0
            x0, x1 = self._x(seg["start_frame"]), self._x(seg["end_frame"])
            p.fillRect(QRectF(x0, 2 + lane * (lane_h + 1), max(1.0, x1 - x0), lane_h),
                       SEGMENT_COLORS.get(seg["kind"], QColor("#888")))
        # 切り出す範囲
        top = 2 + len(lanes) * (lane_h + 1) + 2
        if self.in_f is not None or self.out_f is not None:
            a = self.in_f if self.in_f is not None else 0
            b = self.out_f if self.out_f is not None else self.total
            p.fillRect(QRectF(self._x(a), top, max(1.0, self._x(b) - self._x(a)), h - top - 2),
                       QColor(47, 111, 222, 110))
        # 目盛り
        p.setPen(QPen(QColor("#4a4e55")))
        for i in range(11):
            x = int(self._x(self.total * i / 10))
            p.drawLine(x, h - 6, x, h)
        # 現在位置
        p.setPen(QPen(QColor("#ffffff"), 2))
        x = int(self._x(self.pos))
        p.drawLine(x, 0, x, h)
        p.end()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        self._emit(e.position().x())

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if e.buttons() & Qt.LeftButton:
            self._emit(e.position().x())

    def _emit(self, x: float) -> None:
        f = int(round((x - 1) / max(1, self.width() - 2) * self.total))
        self.seek.emit(max(0, min(self.total - 1, f)))


class PairFetcher(QThread):
    """複数の動画の同じフレームを読み、左右に並べた 1 枚を返す（品質チェックの区間確認用）。"""

    ready = Signal(int, QImage)

    def __init__(self):
        super().__init__()
        self._cv = threading.Condition()
        self._req: tuple | None = None
        self._quit = False
        self._info: dict[str, tuple[float, int, int]] = {}

    def request(self, paths: list[str], frame: int) -> None:
        with self._cv:
            self._req = (list(paths), frame)
            self._cv.notify()

    def stop(self) -> None:
        with self._cv:
            self._quit = True
            self._cv.notify()
        self.wait(3000)

    def _media(self, path: str) -> tuple[float, int, int] | None:
        if path not in self._info:
            from ..media import probe

            try:
                mi = probe(path, None, measure_luma=False)
            except Exception:  # noqa: BLE001
                return None
            self._info[path] = (mi.fps, mi.display_width, mi.display_height)
        return self._info[path]

    def run(self) -> None:
        import cv2
        import numpy as np

        while True:
            with self._cv:
                while self._req is None and not self._quit:
                    self._cv.wait()
                if self._quit:
                    return
                req, self._req = self._req, None
            paths, frame = req
            imgs = []
            for p in paths:
                m = self._media(p)
                img = read_frame(p, frame, m[0], m[1], m[2], max_width=PREVIEW_WIDTH) if m else None
                imgs.append(img)
            h = max((i.shape[0] for i in imgs if i is not None), default=0)
            if not h:
                continue
            tiles = []
            for img in imgs:
                if img is None:
                    tiles.append(np.zeros((h, int(h * 16 / 9), 3), np.uint8))
                    continue
                if img.shape[0] != h:
                    img = cv2.resize(img, (int(round(img.shape[1] * h / img.shape[0])), h))
                tiles.append(img)
            gap = np.zeros((h, 6, 3), np.uint8)
            row = [tiles[0]]
            for t in tiles[1:]:
                row += [gap, t]
            self.ready.emit(frame, bgr_to_qimage(np.hstack(row)))

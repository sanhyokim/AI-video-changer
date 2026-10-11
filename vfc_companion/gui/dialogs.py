"""確認のダイアログ（S1、S2）と動画の情報の表示。"""

from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
                               QPushButton, QVBoxLayout, QWidget)

from ..consent import STATEMENT_FACE_PERSON, STATEMENT_SOURCE_VIDEO
from ..media import MediaInfo, Warning_
from .common import fmt_time, label


def media_form(info: MediaInfo) -> QWidget:
    w = QWidget()
    f = QFormLayout(w)
    f.addRow("解像度", QLabel(f"{info.display_width} x {info.display_height}"
                              + (f"（回転 {info.rotation}°）" if info.rotation else "")))
    f.addRow("フレームレート", QLabel(f"{info.fps:.3f} fps（{info.fps_fraction}）"
                                     + ("　可変の可能性あり" if info.is_vfr else "　固定")))
    f.addRow("長さ", QLabel(f"{fmt_time(info.duration)}（{info.frame_count} フレーム、{info.frame_count_source}）"))
    f.addRow("映像", QLabel(f"{info.codec} / {info.pix_fmt} / {info.bit_depth or '?'}bit"))
    hdr = "HDR" if info.is_hdr else "SDR"
    f.addRow("色", QLabel(f"{hdr}（転送特性 {info.color_transfer or '不明'}、色域 {info.color_primaries or '不明'}）"))
    f.addRow("音声", QLabel(f"{info.audio_codec} {info.audio_sample_rate}Hz {info.audio_channels}ch"
                            if info.has_audio else "なし"))
    f.addRow("明るさ", QLabel("—" if info.mean_luma is None else f"平均 {info.mean_luma:.0f} / 255"))
    if info.timecode:
        f.addRow("タイムコード", QLabel(info.timecode))
    f.addRow("ファイル", label(f"{info.path}（{info.size_bytes / 1e9:.2f} GB）"))
    return w


def warnings_widget(warnings: list[Warning_] | list[dict]) -> QWidget:
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    if not warnings:
        v.addWidget(label("撮影ガイドに合わない点は見つかりませんでした。", "ok"))
    for item in warnings:
        d = item.to_dict() if isinstance(item, Warning_) else item
        v.addWidget(label("⚠ " + d["message"], "warn"))
        if d.get("suggestion"):
            v.addWidget(label("　→ " + d["suggestion"]))
    return w


class ImportDialog(QDialog):
    """動画の情報と警告を見せ、S2 の確認がないと取り込めないようにする。"""

    def __init__(self, parent, info: MediaInfo, warnings: list[Warning_]):
        super().__init__(parent)
        self.setWindowTitle("動画の取り込み")
        self.resize(640, 560)
        v = QVBoxLayout(self)
        v.addWidget(media_form(info))
        v.addWidget(label("<b>撮影ガイドとの照合</b>"))
        v.addWidget(warnings_widget(warnings))
        v.addSpacing(8)
        self.consent = QCheckBox("確認しました")
        v.addWidget(label("<b>素材の確認（必須）</b>"))
        v.addWidget(label(STATEMENT_SOURCE_VIDEO))
        v.addWidget(self.consent)
        self.copy = QCheckBox("プロジェクトの sources フォルダにコピーする（推奨）")
        self.copy.setChecked(True)
        v.addWidget(self.copy)
        bb = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.ok = QPushButton("取り込む")
        self.ok.setObjectName("primary")
        self.ok.setEnabled(False)
        bb.addButton(self.ok, QDialogButtonBox.AcceptRole)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        self.consent.toggled.connect(self.ok.setEnabled)
        v.addWidget(bb)


class PersonDialog(QDialog):
    """AI 人物の登録。S1 の確認がないと登録できない。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("AI 人物の追加")
        self.resize(520, 260)
        v = QVBoxLayout(self)
        f = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("例: Aoi（英数字がフォルダ名に使われます）")
        self.note = QLineEdit()
        self.note.setPlaceholderText("例: 20代女性、ダンス用")
        f.addRow("名前", self.name)
        f.addRow("メモ", self.note)
        v.addLayout(f)
        v.addWidget(label("<b>顔素材の確認（必須）</b>"))
        v.addWidget(label(STATEMENT_FACE_PERSON))
        self.consent = QCheckBox("確認しました")
        v.addWidget(self.consent)
        bb = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.ok = QPushButton("追加する")
        self.ok.setObjectName("primary")
        self.ok.setEnabled(False)
        bb.addButton(self.ok, QDialogButtonBox.AcceptRole)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.consent.toggled.connect(self._update)
        self.name.textChanged.connect(self._update)

    def _update(self) -> None:
        self.ok.setEnabled(self.consent.isChecked() and bool(self.name.text().strip()))

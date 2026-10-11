"""GUI の動作確認（画面を出さない offscreen で動かす）。"""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


def _wait(app, cond, timeout=60.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_gui_flow(tmp_path, media_dir, face_images):
    from PySide6.QtWidgets import QApplication

    from vfc_companion.face_assets import add_images, add_person
    from vfc_companion.gui.app import MainWindow
    from vfc_companion.gui.common import STYLE
    from vfc_companion.media import import_source
    from vfc_companion.project import Project

    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLE)
    proj = Project.create(tmp_path / "gui_proj", "GUI")
    import_source(proj, media_dir / "faces.mp4", consent_confirmed=True)
    p = add_person(proj, "Aoi", consent_confirmed=True)
    add_images(proj, p["id"], [face_images["single_big"], face_images["group"], face_images["single_flip"]])

    win = MainWindow()
    win.show()
    win.open_project(str(proj.root))
    shots = os.environ.get("VFC_SHOTS")

    # プレビューが表示される
    win.tabs.setCurrentWidget(win.clip_tab)
    assert _wait(app, lambda: win.clip_tab.view.pixmap() is not None and not win.clip_tab.view.pixmap().isNull())

    # 顔の区間を解析
    win.clip_tab._scan()
    assert _wait(app, lambda: not win.busy, 180)
    assert win.clip_tab.seg_table.rowCount() >= 1

    # 範囲を決めて切り出す
    win.clip_tab.seek(10)
    win.clip_tab.mark_in()
    win.clip_tab.seek(39)
    win.clip_tab.mark_out()
    assert (win.clip_tab.in_f, win.clip_tab.out_f) == (10, 40)
    win.clip_tab._cut()
    assert _wait(app, lambda: not win.busy, 120)
    assert len(win.project.data["clips"]) == 1
    assert win.project.data["clips"][0]["actual_frame_count"] == 30
    if shots:
        win.grab().save(os.path.join(shots, "clip_tab.png"))

    # 再生してすぐ止める
    win.clip_tab.seek(0)
    win.clip_tab.toggle_play()
    assert _wait(app, lambda: win.clip_tab.pos > 3, 30)
    win.clip_tab.stop_play()

    win.tabs.setCurrentWidget(win.faces_tab)
    app.processEvents()
    assert win.faces_tab.table.rowCount() == 3
    if shots:
        win.grab().save(os.path.join(shots, "faces_tab.png"))
    win.tabs.setCurrentWidget(win.media_tab)
    app.processEvents()
    if shots:
        win.grab().save(os.path.join(shots, "media_tab.png"))
    win.tabs.setCurrentWidget(win.project_tab)
    app.processEvents()
    if shots:
        win.grab().save(os.path.join(shots, "project_tab.png"))
    win.close()


def test_consent_dialogs_block_until_checked(media_dir):
    from PySide6.QtWidgets import QApplication

    from vfc_companion.gui.dialogs import ImportDialog, PersonDialog
    from vfc_companion.media import check_guide, probe

    app = QApplication.instance() or QApplication([])
    info = probe(media_dir / "cfr.mp4")
    d = ImportDialog(None, info, check_guide(info))
    assert not d.ok.isEnabled()
    d.consent.setChecked(True)
    assert d.ok.isEnabled()
    p = PersonDialog(None)
    p.consent.setChecked(True)
    assert not p.ok.isEnabled()  # 名前がない
    p.name.setText("Aoi")
    assert p.ok.isEnabled()
    app.processEvents()

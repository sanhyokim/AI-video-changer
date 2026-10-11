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


def test_gui_phase2_flow(tmp_path, media_dir, face_images, monkeypatch):
    """比較（偽の FaceFusion）→ 品質チェック → 基準値 → シーン別の設定を画面から操作する。"""
    import shutil
    import sys
    from pathlib import Path

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from vfc_companion.clips import cut_clip
    from vfc_companion.face_assets import add_images, add_person
    from vfc_companion.gui.app import MainWindow
    from vfc_companion.gui.common import STYLE
    from vfc_companion.gui.phase2 import CalibrationDialog
    from vfc_companion.media import import_source
    from vfc_companion.project import Project

    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLE)
    monkeypatch.delenv("FAKE_FF_MODE", raising=False)
    proj = Project.create(tmp_path / "画面の確認", "GUI2")
    src = import_source(proj, media_dir / "faces.mp4", consent_confirmed=True)
    clip = cut_clip(proj, src["id"], 0, 90)
    p = add_person(proj, "Aoi", consent_confirmed=True)
    add_images(proj, p["id"], [face_images["single_big"], face_images["single_flip"]])
    proj.set_setting("facefusion_dir", str(Path(__file__).parent / "fake_facefusion"))
    proj.set_setting("facefusion_python", sys.executable)
    proj.set_setting("qc_step", 3)
    # VisoMaster Fusion の出力の代わり（元のクリップをそのまま置く）
    vm = proj.root / "renders" / "visomaster"
    vm.mkdir(parents=True, exist_ok=True)
    shutil.copy(proj.abs(clip["file"]), vm / f"{Path(clip['file']).stem}_vm.mp4")

    win = MainWindow()
    win.show()
    win.open_project(str(proj.root))
    shots = os.environ.get("VFC_SHOTS")

    # 比較：FaceFusion の確認
    ct = win.compare_tab
    win.tabs.setCurrentWidget(ct)
    ct._check_ff()
    assert _wait(app, lambda: not win.busy, 120)
    assert win.project.data["tools"]["facefusion"]["version"] == "3.9.1"

    # 基準フレームの顔を表示して割り当てる
    ct._find_ref()
    assert _wait(app, lambda: not win.busy, 180)
    assert ct.ref_frame_shown == 0 and len(ct.ref_faces) >= 1
    ct.pos_spin.setValue(0)
    ct._assign()
    assert ct.assign_table.rowCount() == 1
    assert win.project.clip(clip["id"])["assignments"][0]["reference_face_position"] == 0

    # ボタン 1 つで比較
    ct._run()
    assert _wait(app, lambda: not win.busy, 300)
    assert ct.render_table.rowCount() == 3            # FaceFusion 2 本 + VisoMaster 1 本
    assert ct.cmp_table.rowCount() == 1
    assert ct.cmp_frames.count() == 5
    assert ct.cmp_view.pixmap() is not None and not ct.cmp_view.pixmap().isNull()
    if shots:
        win.grab().save(os.path.join(shots, "compare_tab.png"))

    # 品質チェック：VisoMaster の出力を選んで実行
    qt = win.qc_tab
    win.tabs.setCurrentWidget(qt)
    app.processEvents()
    labels = [qt.render_list.item(i).text() for i in range(qt.render_list.count())]
    vm_row = next(i for i, t in enumerate(labels) if "VisoMaster" in t)
    qt.render_list.setCurrentRow(vm_row)
    qt._run()
    assert _wait(app, lambda: not win.busy, 300)
    assert qt.hist.rowCount() == 1 and qt.metrics is not None
    assert qt.iv_table.rowCount() >= 1
    assert "要確認" in qt.summary.text()
    qt.iv_table.setCurrentCell(qt.iv_table.rowCount() - 1, 0)
    want = qt.metrics["intervals"][-1]["worst_frame"]
    assert qt.pos == want
    assert _wait(app, lambda: qt.view.pixmap() is not None and not qt.view.pixmap().isNull(), 30)
    qt.checks["expression"].setCurrentIndex(2)
    qid = qt.qc_id
    assert win.project.data["qc_reports"][-1]["visual_checks"]["expression"]["ok"] is False
    if shots:
        win.grab().save(os.path.join(shots, "qc_tab.png"))

    # 基準値を決める
    dlg = CalibrationDialog(qt, win.project, qid)
    assert dlg.selected() == [qid] and dlg.table.rowCount() >= 3 and dlg.ok.isEnabled()
    proposed = dict(dlg.proposals)
    dlg._apply()
    s = win.project.settings
    for k, d in proposed.items():
        assert s[k] == d["proposed"]
    assert win.project.data["qc_reports"][-1]["judged_good"] is True

    # シーン別の設定
    pt = win.profiles_tab
    win.tabs.setCurrentWidget(pt)
    pt.list.setCurrentRow(0)
    app.processEvents()
    assert pt.table.rowCount() == 4                   # P1 の推奨
    pt.table.item(1, 1).setText("1.4")
    pt.notes.setPlainText("力む場面は 1.4 で能面っぽさが減った")
    for i in range(pt.evidence.count()):
        if pt.evidence.item(i).data(Qt.UserRole) == qid:
            pt.evidence.item(i).setCheckState(Qt.Checked)
    pt._save(True)
    rec = win.project.data["scene_profiles"]["P1"]
    assert rec["decided_at"] and rec["evidence"] == [qid] and rec["settings"][1][1] == "1.4"
    assert "決定" in pt.list.item(0).text()
    if shots:
        win.grab().save(os.path.join(shots, "profiles_tab.png"))
    win.close()

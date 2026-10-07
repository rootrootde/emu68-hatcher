"""Layout regressions for visible updates, spacing and palette changes."""


def test_navigation_icons_keep_all_four_tiles_at_retina_scales(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from emu68hatcher.gui.design import navigation_icon
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    for ratio in (1.0, 1.5, 2.0, 3.0):
        icon = navigation_icon("overview", "#56647a", ratio)
        for mode in (QIcon.Mode.Normal, QIcon.Mode.Selected):
            image = icon.pixmap(QSize(22, 22), ratio, mode).toImage()
            width, height = image.width(), image.height()
            assert width == height == round(22 * ratio)
            # The four tiles have a transparent cross between them, at every scale.
            assert all(image.pixelColor(x, height // 2).alpha() == 0 for x in range(width))
            assert all(image.pixelColor(width // 2, y).alpha() == 0 for y in range(height))
            for left, top in ((0, 0), (width // 2, 0), (0, height // 2), (width // 2, height // 2)):
                assert any(
                    image.pixelColor(x, y).alpha() > 0
                    for x in range(left, left + width // 2)
                    for y in range(top, top + height // 2)
                )
    app.processEvents()


def test_work_areas_keep_updates_visible_and_sections_consistent(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("HATCHER_HOME", str(tmp_path))
    from emu68hatcher.gui.design import SECTION_GAP
    from emu68hatcher.gui.main_window import MainWindow
    from emu68hatcher.gui.tabs.start import StartTab
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QApplication, QPushButton, QScrollArea, QTabWidget

    monkeypatch.setattr(StartTab, "check_for_updates", lambda self: None)
    app = QApplication.instance() or QApplication([])
    palette = QPalette(app.palette())
    window = MainWindow()
    try:
        window.resize(800, 600)
        window.show()
        app.processEvents()
        viewport = window.page_widgets["overview"].viewport()
        updates = window.start_tab.updates_group
        assert updates.isVisible()
        assert viewport.rect().contains(updates.mapTo(viewport, QPoint(0, 0)))
        assert viewport.rect().contains(updates.mapTo(viewport, updates.rect().bottomRight()))
        assert window.overview.issues.height() <= 160
        assert all(window.sidebar.item(row).sizeHint().height() >= 48 for row in range(6))
        assert all(not window.sidebar.item(row).icon().isNull() for row in range(6))

        panel = window.kickstart_tab.asset_panel
        assert panel.layout().spacing() == panel.parentWidget().layout().spacing() == SECTION_GAP
        assert not panel.whdload_status.isVisible()
        previous_checks = list(window.kickstart_tab._locale_checks.values())
        window.kickstart_tab.refresh_catalog()
        assert all(check.isHidden() for check in previous_checks)

        for page in ("appearance", "storage"):
            assert not window.pages[page].findChildren(QTabWidget)
        window.navigate("appearance")
        assert window.display_tab.force_hdmi_check.isVisible()
        assert window.kickstart_tab.icon_set_combo.isVisible()
        modes = window.display_tab.hdmi_mode_combo
        modes.setCurrentIndex(modes.findData("Custom"))
        window.display_tab.framethrower_check.setChecked(True)
        app.processEvents()
        assert all(
            scroll.horizontalScrollBar().maximum() == 0
            for scroll in window.display_tab.findChildren(QScrollArea)
        )
        assert not any(
            "advanced" in button.text().lower()
            for button in window.display_tab.findChildren(QPushButton)
        )
        assert not any(
            button.text() in {"Network settings…", "Themes & icons…"}
            for button in window.packages_tab.findChildren(QPushButton)
        )
        window.navigate("storage")
        assert window.ags_tab.details.isHidden()
        app.processEvents()
        checkbox = window.ags_tab.enabled_check
        assert checkbox.height() >= 28
        assert checkbox.parentWidget().rect().contains(checkbox.geometry())
        window.ags_tab.enabled_check.setChecked(True)
        assert not window.ags_tab.details.isHidden()
        window.ags_tab.enabled_check.setChecked(False)
        assert window.storage_controller.layout_message == ""

        dark = QPalette(palette)
        dark.setColor(QPalette.ColorRole.Window, QColor("#20242b"))
        app.setPalette(dark)
        app.processEvents()
        assert "background: #1e222b" in window.centralWidget().styleSheet()
        light = QPalette(palette)
        light.setColor(QPalette.ColorRole.Window, Qt.GlobalColor.white)
        app.setPalette(light)
        app.processEvents()
        assert "background: #f3f5f9" in window.centralWidget().styleSheet()
    finally:
        app.setPalette(palette)
        window.close()
        window.deleteLater()
        app.processEvents()

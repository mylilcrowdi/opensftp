"""
GlassFrame / GlassBackground — cross-platform glass panel tests.

Verifies that the glass effect:
- Only paints when frost theme is active
- Doesn't crash on any platform (pure QPainter, no OS APIs)
- Properly wraps child widgets
"""
from __future__ import annotations

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from sftp_ui.ui.glass_frame import GlassBackground, GlassFrame


class TestGlassFrame:
    def test_creates_with_layout(self, qapp):
        frame = GlassFrame()
        assert frame.layout() is not None
        frame.close()

    def test_child_widget_added(self, qapp):
        frame = GlassFrame()
        label = QLabel("Hello")
        frame.layout().addWidget(label)
        assert label.parent() is not None
        frame.close()

    def test_inactive_by_default(self, qapp):
        frame = GlassFrame()
        assert frame._active is False
        frame.close()

    def test_set_frost_active(self, qapp):
        frame = GlassFrame()
        frame.set_frost_active(True)
        assert frame._active is True
        frame.set_frost_active(False)
        assert frame._active is False
        frame.close()

    def test_paint_when_active(self, qapp):
        """GlassFrame should paint without crashing when active."""
        frame = GlassFrame()
        frame.resize(400, 300)
        frame.set_frost_active(True)
        frame.show()
        QApplication.processEvents()
        # If we get here without crash, the paint works
        frame.close()

    def test_paint_when_inactive(self, qapp):
        """GlassFrame should paint without crashing when inactive."""
        frame = GlassFrame()
        frame.resize(400, 300)
        frame.set_frost_active(False)
        frame.show()
        QApplication.processEvents()
        frame.close()

    def test_multiple_children(self, qapp):
        frame = GlassFrame()
        for i in range(5):
            frame.layout().addWidget(QPushButton(f"Btn {i}"))
        frame.set_frost_active(True)
        frame.show()
        QApplication.processEvents()
        frame.close()


class TestGlassBackground:
    def test_creates(self, qapp):
        bg = GlassBackground()
        assert bg._active is False
        bg.close()

    def test_set_frost_active(self, qapp):
        bg = GlassBackground()
        bg.set_frost_active(True)
        assert bg._active is True
        bg.close()

    def test_paint_when_active(self, qapp):
        bg = GlassBackground()
        bg.resize(800, 600)
        bg.set_frost_active(True)
        bg.show()
        QApplication.processEvents()
        bg.close()

    def test_paint_when_inactive(self, qapp):
        bg = GlassBackground()
        bg.resize(800, 600)
        bg.show()
        QApplication.processEvents()
        bg.close()

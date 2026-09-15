"""Helpers to raise/activate top-level Qt windows so Mason Jar tools come to front.

Used by ``adjust.py`` (PyQt) and ``map.py`` (Napari / Qt) after ``show()``.

Behavior:

- Call ``show()`` if the widget is not yet visible.
- ``raise_()`` and ``activateWindow()`` immediately and again on a couple of
  deferred ``QTimer.singleShot`` ticks so focus runs after the parent event
  loop paints.
- On Windows, also try ``user32.SetForegroundWindow(hwnd)`` (best-effort;
  Windows blocks steal-focus when the user is typing elsewhere). All ctypes
  calls are wrapped in ``try/except`` so a missing import or refused call
  never breaks the tool launch.

No ``WindowStaysOnTopHint`` (intrusive).
"""

from __future__ import annotations

import sys
from typing import Any


def _safe_attempt(callable_obj, *args, **kwargs):
    try:
        callable_obj(*args, **kwargs)
    except Exception:
        pass


def _windows_foreground(widget) -> None:
    """Best-effort SetForegroundWindow for the given widget on Windows."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = int(widget.winId())
        if hwnd == 0:
            return
        try:
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
    except Exception:
        # ctypes may be unavailable, or winId() invalid before show()
        pass


def _bring_to_front_now(widget) -> None:
    if widget is None:
        return
    try:
        if hasattr(widget, "isVisible") and not widget.isVisible():
            widget.show()
    except Exception:
        pass
    _safe_attempt(widget.raise_)
    _safe_attempt(widget.activateWindow)
    _windows_foreground(widget)


def raise_and_activate(widget: Any) -> None:
    """Show, raise, and request keyboard focus for a top-level Qt widget.

    Schedules two deferred retries (0 ms and 200 ms) via ``QTimer.singleShot``
    so focus is reapplied after the host event loop has painted the window.
    """
    if widget is None:
        return

    _bring_to_front_now(widget)

    try:
        from qtpy.QtCore import QTimer
    except Exception:
        return

    def _retry() -> None:
        _bring_to_front_now(widget)

    try:
        QTimer.singleShot(0, _retry)
        QTimer.singleShot(200, _retry)
    except Exception:
        pass


def resolve_napari_qt_window(viewer: Any) -> Any | None:
    """Return Napari's underlying ``QMainWindow`` (``viewer.window._qt_window``)."""
    if viewer is None:
        return None
    try:
        win = getattr(viewer, "window", None)
        if win is not None:
            return getattr(win, "_qt_window", None) or win
    except Exception:
        return None
    return None


def raise_and_activate_napari(viewer: Any) -> None:
    """Resolve Napari viewer's underlying Qt window and call raise_and_activate.

    Napari's main window is ``viewer.window._qt_window`` (see ``map.py``
    ``update_section_header``). Falls back to ``viewer.window`` if needed.
    """
    qt_window = resolve_napari_qt_window(viewer)
    if qt_window is None:
        return
    raise_and_activate(qt_window)


def ensure_qt_dock_visible(dock_widget: Any) -> None:
    """Best-effort show for a Qt dock widget or toolbar (Napari defaults hidden)."""
    if dock_widget is None:
        return
    _safe_attempt(dock_widget.setVisible, True)
    _safe_attempt(dock_widget.show)


def _windows_maximize(hwnd: int) -> None:
    """Best-effort ShowWindow SW_MAXIMIZE on Windows."""
    if sys.platform != "win32" or hwnd == 0:
        return
    try:
        import ctypes

        ctypes.windll.user32.ShowWindow(hwnd, 3)
    except Exception:
        pass


def ensure_napari_tool_docks_visible(viewer: Any) -> None:
    """Show Napari layer list, layer controls, and viewer tool buttons.

    After ``relocate_napari_layer_docks_to_right``, these docks live on the right.
    """
    if viewer is None:
        return
    try:
        win = getattr(viewer, "window", None)
        if win is None:
            return
        qt_viewer = getattr(win, "_qt_viewer", None)
        if qt_viewer is None:
            return
        for attr in ("dockLayerList", "dockLayerControls"):
            ensure_qt_dock_visible(getattr(qt_viewer, attr, None))
        buttons = getattr(qt_viewer, "viewerButtons", None)
        if buttons is not None:
            ensure_qt_dock_visible(buttons)
            parent = buttons.parent()
            if parent is not None:
                ensure_qt_dock_visible(parent)
    except Exception:
        pass


def hide_napari_layer_docks(viewer: Any) -> None:
    """Hide Napari's layer list and layer controls docks (unused during alignment)."""
    if viewer is None:
        return
    try:
        win = getattr(viewer, "window", None)
        qt_viewer = getattr(win, "_qt_viewer", None) if win is not None else None
        if qt_viewer is None:
            return
        for attr in ("dockLayerList", "dockLayerControls"):
            dock = getattr(qt_viewer, attr, None)
            if dock is not None:
                _safe_attempt(dock.setVisible, False)
                _safe_attempt(dock.hide)
    except Exception:
        pass


def arrange_right_docks_split(viewer: Any, top_dock: Any, bottom_dock: Any) -> None:
    """Place the two docks in the right area, split vertically so both stay visible.

    Using a vertical split (instead of tabifying) avoids one dock completely
    hiding the other behind an invisible tab bar.
    """
    if viewer is None:
        return
    try:
        from qtpy.QtCore import Qt

        qt_window = resolve_napari_qt_window(viewer)
        if qt_window is None:
            return
        right = Qt.DockWidgetArea.RightDockWidgetArea
        if top_dock is not None:
            _safe_attempt(qt_window.addDockWidget, right, top_dock)
        if bottom_dock is not None:
            _safe_attempt(qt_window.addDockWidget, right, bottom_dock)
        if top_dock is not None and bottom_dock is not None:
            _safe_attempt(
                qt_window.splitDockWidget,
                top_dock,
                bottom_dock,
                Qt.Orientation.Vertical,
            )
    except Exception:
        pass


def relocate_napari_layer_docks_to_right(viewer: Any) -> None:
    """Move Napari layer list and layer controls from left to right dock area."""
    if viewer is None:
        return
    try:
        from qtpy.QtCore import Qt

        qt_window = resolve_napari_qt_window(viewer)
        win = getattr(viewer, "window", None)
        qt_viewer = getattr(win, "_qt_viewer", None) if win is not None else None
        if qt_window is None or qt_viewer is None:
            return
        right = Qt.DockWidgetArea.RightDockWidgetArea
        for attr in ("dockLayerList", "dockLayerControls"):
            dock = getattr(qt_viewer, attr, None)
            if dock is not None:
                qt_window.addDockWidget(right, dock)
                ensure_qt_dock_visible(dock)
    except Exception:
        pass


def show_napari_maximized_and_activate(viewer: Any) -> None:
    """Maximize Napari's Qt window and bring it to the foreground with retries."""
    qt_window = resolve_napari_qt_window(viewer)
    if qt_window is None:
        return
    try:
        from qtpy.QtCore import Qt
        from qtpy.QtGui import QGuiApplication
        from qtpy.QtWidgets import QApplication

        _safe_attempt(qt_window.showNormal)
        app = QApplication.instance() or QGuiApplication.instance()
        if app is not None:
            screen = app.primaryScreen()
            if screen is not None:
                _safe_attempt(qt_window.setGeometry, screen.availableGeometry())
        _safe_attempt(
            qt_window.setWindowState,
            Qt.WindowState.WindowMaximized,
        )
    except Exception:
        pass
    _safe_attempt(qt_window.showMaximized)
    raise_and_activate(qt_window)
    try:
        from qtpy.QtCore import QTimer

        def _retry() -> None:
            _safe_attempt(qt_window.showMaximized)
            if sys.platform == "win32":
                try:
                    _windows_maximize(int(qt_window.winId()))
                except Exception:
                    pass
            _bring_to_front_now(qt_window)

        QTimer.singleShot(0, _retry)
        QTimer.singleShot(200, _retry)
        QTimer.singleShot(500, _retry)
    except Exception:
        pass


def align_section_heading(text: str) -> Any:
    """Bold section label for scrollable alignment dock panels."""
    from qtpy.QtWidgets import QLabel

    label = QLabel(text)
    font = label.font()
    font.setBold(True)
    label.setFont(font)
    return label


def build_scroll_dock_panel(
    content_widgets,
    *,
    pinned_footer=None,
    margins=(4, 4, 4, 4),
    content_margins=(0, 0, 0, 0),
) -> Any:
    """Build a dock panel with a scrollable body and optional pinned footer row.

    ``content_margins`` applies inside the scroll viewport, useful when controls
    need a visible gap before the vertical scrollbar.
    """
    from qtpy.QtCore import Qt
    from qtpy.QtWidgets import (
        QHBoxLayout,
        QScrollArea,
        QSizePolicy,
        QVBoxLayout,
        QWidget,
    )

    outer = QWidget()
    outer_layout = QVBoxLayout()
    outer_layout.setContentsMargins(*margins)
    outer_layout.setSpacing(6)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)

    inner = QWidget()
    inner_layout = QVBoxLayout()
    inner_layout.setContentsMargins(*content_margins)
    inner_layout.setSpacing(6)
    for widget in content_widgets:
        if widget is not None:
            inner_layout.addWidget(widget)
    inner_layout.addStretch()
    inner.setLayout(inner_layout)
    scroll.setWidget(inner)
    scroll.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
    outer_layout.addWidget(scroll, stretch=1)

    if pinned_footer:
        footer_row = QHBoxLayout()
        footer_row.setContentsMargins(0, 4, 0, 0)
        footer_row.setSpacing(6)
        for widget in pinned_footer:
            if widget is not None:
                footer_row.addWidget(widget)
        footer_widget = QWidget()
        footer_widget.setLayout(footer_row)
        outer_layout.addWidget(footer_widget)

    outer.setLayout(outer_layout)
    return outer


def clamp_qt_window_to_available_screen(
    qt_window: Any,
    *,
    margin: int = 32,
    min_width: int = 900,
    min_height: int = 700,
) -> None:
    """Resize and center a top-level Qt window within the primary screen work area."""
    if qt_window is None:
        return
    try:
        from qtpy.QtGui import QGuiApplication
        from qtpy.QtWidgets import QApplication
    except Exception:
        return

    app = QApplication.instance() or QGuiApplication.instance()
    if app is None:
        return
    screen = app.primaryScreen()
    if screen is None:
        return

    available = screen.availableGeometry()
    max_w = max(min_width, available.width() - margin)
    max_h = max(min_height, available.height() - margin)

    current = qt_window.size()
    target_w = min(max(current.width(), min_width), max_w)
    target_h = min(max(current.height(), min_height), max_h)
    qt_window.resize(target_w, target_h)

    frame = qt_window.frameGeometry()
    frame.moveCenter(available.center())
    qt_window.move(frame.topLeft())

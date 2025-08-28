"""
Auto-Clicker (PySide6 + pynput) — Refined UI
Author: ChatGPT (GPT-5 Thinking)
License: MIT

Highlights:
- Cleaner two-column layout + toolbar and menu
- Quick interval presets + live CPS (clicks/sec) readout
- Dark/Light theme toggle, "Always on top" toggle
- Keyboard shortcuts: F5 Start, F6 Stop, F8 Pick, ESC panic stop
- Settings persistence (window, theme, options)
- Live coordinates HUD overlay
"""

import sys
import time
import platform
import ctypes
from typing import Optional, Tuple

from PySide6.QtCore import (
    Qt, QObject, QThread, Signal, Slot, QTimer, QPoint, QSettings, QSize
)
from PySide6.QtGui import (
    QIcon, QPalette, QColor, QGuiApplication, QCursor, QPainter, QPen, QFont,
    QFontMetrics, QKeySequence, QShortcut, QAction
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QSpinBox, QCheckBox, QComboBox, QGroupBox, QFormLayout,
    QVBoxLayout, QHBoxLayout, QStatusBar, QMessageBox, QRadioButton, QGridLayout,
    QToolBar, QStyle, QMenuBar, QSpacerItem, QSizePolicy
)

# Mouse/Keyboard automation
from pynput.mouse import Controller as MouseController, Button
from pynput import keyboard


# --------------------------- Live Coordinates HUD ---------------------------

class CoordHUD(QWidget):
    """Tiny always-on-top HUD that shows the current global mouse (X,Y)."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pos = QPoint(0, 0)
        self._text = "X: 0  Y: 0"

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.Tool
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self._timer = QTimer(self)
        self._timer.setInterval(33)  # ~30 FPS
        self._timer.timeout.connect(self._tick)

        # Look
        self._font = QFont()
        self._font.setPointSize(9)

    def start(self):
        self._timer.start()
        self.show()

    def stop(self):
        self._timer.stop()
        self.hide()

    def _tick(self):
        self._pos = QCursor.pos()
        self._text = f"X: {self._pos.x()}  Y: {self._pos.y()}"

        # Place the HUD near the cursor, clamped to the screen bounds
        screen = QGuiApplication.screenAt(self._pos) or QGuiApplication.primaryScreen()
        geom = screen.availableGeometry()

        fm = QFontMetrics(self._font)
        pad = 8
        w = fm.horizontalAdvance(self._text) + pad * 2
        h = fm.height() + pad * 2

        # Try to show at cursor + offset
        offset = QPoint(18, 18)
        x = self._pos.x() + offset.x()
        y = self._pos.y() + offset.y()

        # Clamp to screen
        if x + w > geom.right():
            x = geom.right() - w
        if y + h > geom.bottom():
            y = geom.bottom() - h
        if x < geom.left():
            x = geom.left()
        if y < geom.top():
            y = geom.top()

        self.setGeometry(x, y, w, h)
        self.update()

    def paintEvent(self, _ev):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        # Bubble background
        bg = QColor(255, 255, 255, 230)  # almost white
        border = QColor(80, 80, 80, 180)
        painter.setBrush(bg)
        painter.setPen(QPen(border, 1))
        painter.drawRoundedRect(self.rect(), 6, 6)

        # Text
        painter.setFont(self._font)
        painter.setPen(QColor(30, 30, 30))
        painter.drawText(self.rect().adjusted(8, 6, -8, -6),
                         Qt.AlignLeft | Qt.AlignVCenter, self._text)


# --------------------------- Worker (runs in background) ---------------------------

class ClickWorker(QObject):
    """Background clicker worker. Lives in a QThread; communicates via signals."""
    progress = Signal(int)        # clicks performed so far
    status = Signal(str)          # string status message
    finished = Signal()           # done (normally or aborted)
    error = Signal(str)           # error message

    def __init__(self):
        super().__init__()
        self._stop_requested = False
        self.interval_ms = 100
        self.mode = "Left"        # Left | Right | Double (left)
        self.total_clicks: Optional[int] = None  # None for continuous
        self.countdown = True

        # Targeting (resolved before run; for dynamic modes UI pre-resolves to fixed)
        self.target_mode: str = "follow"         # "follow" | "fixed"
        self.target_pos: Optional[Tuple[int, int]] = None  # (x, y) if fixed

        self._mouse = MouseController()
        self._kb_listener = None

    def configure(self, interval_ms: int, mode: str,
                  total_clicks: Optional[int], countdown: bool,
                  target_mode: str, target_pos: Optional[Tuple[int, int]]):
        self.interval_ms = max(1, int(interval_ms))
        self.mode = mode
        self.total_clicks = total_clicks  # None means continuous
        self.countdown = countdown
        self.target_mode = target_mode
        self.target_pos = target_pos

    def request_stop(self):
        self._stop_requested = True

    def _start_keyboard_listener(self):
        # Global ESC key to stop safely.
        def on_press(key):
            try:
                if key == keyboard.Key.esc:
                    self.request_stop()
                    self.status.emit("Panic stop requested (ESC).")
            except Exception:
                pass

        self._kb_listener = keyboard.Listener(on_press=on_press)
        self._kb_listener.daemon = True
        self._kb_listener.start()

    def _stop_keyboard_listener(self):
        try:
            if self._kb_listener:
                self._kb_listener.stop()
        except Exception:
            pass
        self._kb_listener = None

    def _move_to_target_if_needed(self):
        if self.target_mode == "fixed" and self.target_pos is not None:
            try:
                self._mouse.position = self.target_pos
            except Exception as e:
                raise RuntimeError(f"Failed to move to target {self.target_pos}: {e}") from e

    def _click_once(self, remaining: int) -> int:
        """
        Perform one 'action' based on mode.
        Returns how many physical clicks were actually produced (1 or 2).
        We ensure we never exceed 'remaining'.
        """
        try:
            if self.mode == "Left":
                self._mouse.click(Button.left, 1)
                return 1
            elif self.mode == "Right":
                self._mouse.click(Button.right, 1)
                return 1
            elif self.mode == "Double (left)":
                clicks = 2 if remaining >= 2 else 1
                self._mouse.click(Button.left, clicks)
                return clicks
            else:
                self._mouse.click(Button.left, 1)
                return 1
        except Exception as e:
            raise RuntimeError(str(e)) from e

    @Slot()
    def run(self):
        """Main worker loop."""
        self._stop_requested = False

        try:
            # Safety countdown
            if self.countdown:
                for i in (3, 2, 1):
                    if self._stop_requested:
                        self.status.emit("Start aborted.")
                        self.finished.emit()
                        return
                    self.status.emit(f"Starting in {i}… (press ESC to cancel)")
                    time.sleep(1)

            self._start_keyboard_listener()
            self.status.emit("Running (ESC to stop)…")

            performed = 0
            interval_s = max(0.001, self.interval_ms / 1000.0)

            while not self._stop_requested:
                remaining = (self.total_clicks - performed) if self.total_clicks is not None else None
                if remaining is not None and remaining <= 0:
                    break

                # Move to fixed target if requested
                self._move_to_target_if_needed()

                produced = self._click_once(remaining if remaining is not None else 2)
                performed += produced
                self.progress.emit(performed)

                # Sleep with small checks to remain responsive to stop requests
                target = time.perf_counter() + interval_s
                while not self._stop_requested:
                    now = time.perf_counter()
                    if now >= target:
                        break
                    time.sleep(min(0.01, target - now))

            if self._stop_requested:
                self.status.emit("Stopped by user.")
            else:
                self.status.emit("Completed.")
        except RuntimeError as e:
            self.error.emit(f"Automation error: {e}")
        except Exception as e:
            self.error.emit(f"Unexpected error: {e}")
        finally:
            self._stop_keyboard_listener()
            self.finished.emit()


# --------------------------- Main Window (UI) ---------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        # Settings (persist between runs)
        self.settings = QSettings("ChatGPT", "AutoClicker")

        self.setWindowTitle("Auto-Clicker")
        self.resize(760, 560)
        self._apply_light_palette()  # default; may be overridden by settings below
        self.setWindowIcon(self._fallback_icon())

        # Read persisted UI prefs
        self._is_dark = self.settings.value("ui/dark", False, type=bool)
        self._always_on_top = self.settings.value("ui/always_on_top", False, type=bool)

        if self._is_dark:
            self._apply_dark_palette()

        if self._always_on_top:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)

        # ------------- Controls -------------
        # Timing
        self.interval_box = QSpinBox()
        self.interval_box.setRange(1, 600_000)  # 1 ms .. 10 minutes
        self.interval_box.setValue(self.settings.value("click/interval_ms", 100, type=int))
        self.interval_box.setSuffix(" ms")
        self.interval_box.setToolTip("Delay between clicks")
        self.interval_box.valueChanged.connect(self._update_cps)

        # Quick presets
        self.preset_box = QComboBox()
        self.preset_box.addItems([
            "1 ms", "5 ms", "10 ms", "50 ms", "100 ms", "250 ms",
            "500 ms", "1 s", "2 s", "5 s"
        ])
        self.preset_box.setToolTip("Quick interval preset")
        self.preset_box.currentIndexChanged.connect(self._apply_preset_interval)

        # CPS readout
        self.cps_lbl = QLabel("")
        self.cps_lbl.setToolTip("Effective clicks per second")
        self._update_cps(self.interval_box.value())

        # Click mode
        self.mode_box = QComboBox()
        self.mode_box.addItems(["Left", "Right", "Double (left)"])
        self.mode_box.setCurrentText(self.settings.value("click/mode", "Left"))
        self.mode_box.setToolTip("Type of click to perform")

        # Count / continuous
        self.count_box = QSpinBox()
        self.count_box.setRange(1, 1_000_000)
        self.count_box.setValue(self.settings.value("click/count", 100, type=int))
        self.count_box.setToolTip("How many physical clicks to send")
        self.continuous_chk = QCheckBox("Continuous (ignore count)")
        self.continuous_chk.setChecked(self.settings.value("click/continuous", False, type=bool))
        self.continuous_chk.setToolTip("Run until you press Stop or ESC")
        self.continuous_chk.stateChanged.connect(self._on_continuous_changed)
        self._on_continuous_changed(0)

        self.countdown_chk = QCheckBox("3-second countdown before start")
        self.countdown_chk.setChecked(self.settings.value("click/countdown", True, type=bool))

        # Targeting (4 choices)
        self.follow_radio = QRadioButton("Follow cursor")
        self.fixed_radio = QRadioButton("Fixed point")
        self.screen_center_radio = QRadioButton("Screen center (current screen)")
        self.win_center_radio = QRadioButton("Active window center (Windows)")
        # Restore selection
        tgt_mode_saved = self.settings.value("target/mode", "follow")
        self.follow_radio.setChecked(tgt_mode_saved == "follow")
        self.fixed_radio.setChecked(tgt_mode_saved == "fixed")
        self.screen_center_radio.setChecked(tgt_mode_saved == "screen_center")
        self.win_center_radio.setChecked(tgt_mode_saved == "win_center")

        self.x_box = QSpinBox()
        self.y_box = QSpinBox()
        self.x_box.setRange(-20000, 20000)
        self.y_box.setRange(-20000, 20000)
        self.x_box.setValue(self.settings.value("target/x", 0, type=int))
        self.y_box.setValue(self.settings.value("target/y", 0, type=int))
        self.x_box.setEnabled(False)
        self.y_box.setEnabled(False)

        self.pick_btn = QPushButton("Pick current mouse position")
        self.pick_btn.setEnabled(False)
        self.pick_btn.setToolTip("Click to copy the current mouse X,Y into the boxes (F8 also works)")
        self.pick_btn.clicked.connect(self._pick_current_mouse)

        # App-scoped shortcuts
        QShortcut(QKeySequence("F8"), self, activated=self._pick_current_mouse)
        QShortcut(QKeySequence("F5"), self, activated=lambda: self.start_btn.click())
        QShortcut(QKeySequence("F6"), self, activated=lambda: self.stop_btn.click())

        self.follow_radio.toggled.connect(self._on_target_mode_changed)
        self.fixed_radio.toggled.connect(self._on_target_mode_changed)
        self.screen_center_radio.toggled.connect(self._on_target_mode_changed)
        self.win_center_radio.toggled.connect(self._on_target_mode_changed)

        # Disable Windows-only option on other OSes
        if platform.system() != "Windows":
            self.win_center_radio.setEnabled(False)
            self.win_center_radio.setToolTip("Available on Windows only")

        # Live HUD toggle
        self.hud_chk = QCheckBox("Show live coordinates HUD (F8 to Pick)")
        self.hud_chk.setChecked(self.settings.value("ui/hud", False, type=bool))
        self.hud_chk.toggled.connect(self._on_hud_toggled)
        self.coord_hud = CoordHUD()
        self.coord_hud.hide()
        if self.hud_chk.isChecked():
            self.coord_hud.start()

        # Start/Stop
        self.start_btn = QPushButton("&Start  (F5)")
        self.stop_btn = QPushButton("S&top   (F6)")
        self.stop_btn.setEnabled(False)
        self.start_btn.setDefault(True)

        # Live status
        self.status_lbl = QLabel("Status: Idle.")
        self.status_lbl.setProperty("state", "idle")
        self.perf_lbl = QLabel("Clicks performed: 0")

        # ------------- Layout -------------
        # Timing group (left column)
        timing_group = QGroupBox("Timing & Click")
        form_a = QFormLayout()
        form_a.setLabelAlignment(Qt.AlignRight)
        int_row = QHBoxLayout()
        int_row.addWidget(self.interval_box)
        int_row.addSpacing(8)
        int_row.addWidget(self.preset_box)
        int_row.addStretch(1)
        int_row_w = QWidget(); int_row_w.setLayout(int_row)

        cps_row = QHBoxLayout()
        cps_row.addWidget(self.cps_lbl)
        cps_row.addStretch(1)
        cps_row_w = QWidget(); cps_row_w.setLayout(cps_row)

        form_a.addRow("Interval:", int_row_w)
        form_a.addRow("", cps_row_w)
        form_a.addRow("Click type:", self.mode_box)
        form_a.addRow("Number of clicks:", self.count_box)
        form_a.addRow("", self.continuous_chk)
        form_a.addRow("", self.countdown_chk)
        timing_group.setLayout(form_a)

        # Target group (right column)
        target_group = QGroupBox("Target")
        tgt_grid = QGridLayout()
        tgt_grid.addWidget(self.follow_radio, 0, 0, 1, 2)
        tgt_grid.addWidget(self.fixed_radio, 1, 0)
        xy_row = QHBoxLayout()
        xy_row.addWidget(QLabel("X:"))
        xy_row.addWidget(self.x_box)
        xy_row.addSpacing(12)
        xy_row.addWidget(QLabel("Y:"))
        xy_row.addWidget(self.y_box)
        xy_row.addStretch(1)
        row1w = QWidget(); row1w.setLayout(xy_row)
        tgt_grid.addWidget(row1w, 1, 1)
        tgt_grid.addWidget(self.screen_center_radio, 2, 0, 1, 2)
        tgt_grid.addWidget(self.win_center_radio, 3, 0, 1, 2)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.pick_btn)
        btn_row.addStretch(1)
        btnw = QWidget(); btnw.setLayout(btn_row)
        tgt_grid.addWidget(btnw, 4, 0, 1, 2)

        tgt_grid.addWidget(self.hud_chk, 5, 0, 1, 2)
        target_group.setLayout(tgt_grid)

        # Columns: Timing | Target
        columns = QHBoxLayout()
        columns.addWidget(timing_group, 1)
        columns.addWidget(target_group, 1)

        # Buttons
        ctl_row = QHBoxLayout()
        ctl_row.addWidget(self.start_btn)
        ctl_row.addWidget(self.stop_btn)
        ctl_row.addItem(QSpacerItem(12, 12, QSizePolicy.Expanding, QSizePolicy.Minimum))

        # Live status group
        info_group = QGroupBox("Live Status")
        info_layout = QVBoxLayout()
        info_layout.addWidget(self.status_lbl)
        info_layout.addWidget(self.perf_lbl)
        info_group.setLayout(info_layout)

        # Root
        root = QWidget()
        v = QVBoxLayout(root)
        v.addLayout(columns)
        v.addLayout(ctl_row)
        v.addWidget(info_group)
        v.addStretch(1)
        self.setCentralWidget(root)

        # Status bar (shortcut hints on the right)
        self.setStatusBar(QStatusBar(self))
        self.statusBar().showMessage("Tips: F5 Start • F6 Stop • F8 Pick • ESC panic-stop")

        # Toolbar + Menu
        self._build_toolbar()
        self._build_menubar()

        # Connections
        self.start_btn.clicked.connect(self.start_clicking)
        self.stop_btn.clicked.connect(self.stop_clicking)

        # Thread/worker
        self.thread: Optional[QThread] = None
        self.worker: Optional[ClickWorker] = None

        self._on_target_mode_changed()
        self._update_status_style()

        # Restore geometry
        geo = self.settings.value("ui/geometry")
        if geo:
            self.restoreGeometry(geo)

        # Ensure window flags (always-on-top) are applied after construction
        if self._always_on_top:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
            self.show()
        else:
            self.show()

    # ---------- Toolbar / Menu ----------

    def _build_toolbar(self):
        tb = QToolBar("Main")
        tb.setIconSize(QSize(18, 18))
        self.addToolBar(tb)

        style = self.style()
        start_act = QAction(style.standardIcon(QStyle.SP_MediaPlay), "Start (F5)", self)
        stop_act  = QAction(style.standardIcon(QStyle.SP_MediaStop),  "Stop (F6)", self)
        pick_act  = QAction(style.standardIcon(QStyle.SP_DialogYesButton), "Pick (F8)", self)

        start_act.triggered.connect(lambda: self.start_btn.click())
        stop_act.triggered.connect(lambda: self.stop_btn.click())
        pick_act.triggered.connect(self._pick_current_mouse)

        tb.addAction(start_act)
        tb.addAction(stop_act)
        tb.addSeparator()
        tb.addAction(pick_act)
        tb.addSeparator()

        # Checkable actions — use toggled(bool) so a bool is always passed
        self.hud_act = QAction("Show HUD", self, checkable=True)
        self.hud_act.setChecked(self.hud_chk.isChecked())
        self.hud_act.toggled.connect(self.hud_chk.setChecked)        # <-- changed

        self.aot_act = QAction("Always on top", self, checkable=True)
        self.aot_act.setChecked(self._always_on_top)
        self.aot_act.toggled.connect(self._toggle_always_on_top)     # <-- changed

        self.theme_act = QAction("Dark mode", self, checkable=True)
        self.theme_act.setChecked(self._is_dark)
        self.theme_act.toggled.connect(self._toggle_theme)           # <-- changed

        tb.addAction(self.hud_act)
        tb.addAction(self.aot_act)
        tb.addAction(self.theme_act)


    def _build_menubar(self):
        mb: QMenuBar = self.menuBar()
        file_menu = mb.addMenu("&File")
        quit_act = QAction("E&xit", self)
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        view_menu = mb.addMenu("&View")
        view_menu.addAction(self.theme_act)
        view_menu.addAction(self.aot_act)
        view_menu.addAction(self.hud_act)

        help_menu = mb.addMenu("&Help")
        about_act = QAction("&About", self)
        about_act.triggered.connect(self._show_about)
        help_menu.addAction(about_act)

    # ---------- UI helpers ----------

    def _fallback_icon(self) -> QIcon:
        # Just use a generic application icon from the style
        return self.style().standardIcon(QStyle.SP_ComputerIcon)

    def _apply_light_palette(self):
        QApplication.setStyle("Fusion")
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor(250, 250, 250))
        palette.setColor(QPalette.WindowText, QColor(30, 30, 30))
        palette.setColor(QPalette.Base, QColor(255, 255, 255))
        palette.setColor(QPalette.AlternateBase, QColor(245, 245, 245))
        palette.setColor(QPalette.ToolTipBase, QColor(255, 255, 255))
        palette.setColor(QPalette.ToolTipText, QColor(30, 30, 30))
        palette.setColor(QPalette.Text, QColor(30, 30, 30))
        palette.setColor(QPalette.Button, QColor(245, 245, 245))
        palette.setColor(QPalette.ButtonText, QColor(30, 30, 30))
        palette.setColor(QPalette.Highlight, QColor(76, 163, 224))
        palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
        palette.setColor(QPalette.PlaceholderText, QColor(130, 130, 130))
        QApplication.setPalette(palette)

        self.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 1px solid #dcdcdc;
                border-radius: 6px;
                margin-top: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
                background: transparent;
            }
            QLabel[state="idle"] { color: #4a4a4a; }
            QLabel[state="running"] { color: #1a7f37; }   /* green-ish */
            QLabel[state="stopped"] { color: #9b6b17; }   /* amber */
            QLabel[state="error"] { color: #b00020; }     /* red */
            QPushButton {
                padding: 8px 14px;
                border-radius: 6px;
            }
            QPushButton:enabled:hover { border: 1px solid #6aa0ff; }
            QToolBar { spacing: 6px; }
        """)

    def _apply_dark_palette(self):
        QApplication.setStyle("Fusion")
        dark = QPalette()
        dark.setColor(QPalette.Window, QColor(37, 37, 37))
        dark.setColor(QPalette.WindowText, Qt.white)
        dark.setColor(QPalette.Base, QColor(30, 30, 30))
        dark.setColor(QPalette.AlternateBase, QColor(45, 45, 45))
        dark.setColor(QPalette.ToolTipBase, Qt.white)
        dark.setColor(QPalette.ToolTipText, Qt.black)
        dark.setColor(QPalette.Text, Qt.white)
        dark.setColor(QPalette.Button, QColor(45, 45, 45))
        dark.setColor(QPalette.ButtonText, Qt.white)
        dark.setColor(QPalette.Disabled, QPalette.Text, QColor(127,127,127))
        dark.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(127,127,127))
        dark.setColor(QPalette.Highlight, QColor(86, 156, 214))
        dark.setColor(QPalette.HighlightedText, Qt.black)
        QApplication.setPalette(dark)

        self.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 1px solid #444;
                border-radius: 6px;
                margin-top: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
                background: transparent;
            }
            QLabel[state="idle"] { color: #dadada; }
            QLabel[state="running"] { color: #7ee787; }   /* green */
            QLabel[state="stopped"] { color: #e3b341; }   /* amber */
            QLabel[state="error"] { color: #ff7b72; }     /* red */
            QPushButton {
                padding: 8px 14px;
                border-radius: 6px;
            }
            QPushButton:enabled:hover { border: 1px solid #7aa7ff; }
            QToolBar { spacing: 6px; }
        """)

    def _toggle_theme(self, checked: bool):
        self._is_dark = checked
        if checked:
            self._apply_dark_palette()
        else:
            self._apply_light_palette()

    def _toggle_always_on_top(self, checked: bool):
        self._always_on_top = checked
        self.setWindowFlag(Qt.WindowStaysOnTopHint, checked)
        self.show()  # re-apply flags

    def _set_status(self, text: str, state: str = "idle"):
        self.status_lbl.setText(f"Status: {text}")
        self.status_lbl.setProperty("state", state)
        self._update_status_style()

    def _update_status_style(self):
        self.status_lbl.style().unpolish(self.status_lbl)
        self.status_lbl.style().polish(self.status_lbl)
        self.status_lbl.update()

    def _on_continuous_changed(self, _state: int):
        self.count_box.setEnabled(not self.continuous_chk.isChecked())

    def _on_target_mode_changed(self):
        fixed = self.fixed_radio.isChecked()
        self.x_box.setEnabled(fixed)
        self.y_box.setEnabled(fixed)
        self.pick_btn.setEnabled(fixed)

    def _on_hud_toggled(self, checked: bool):
        if checked:
            self.coord_hud.start()
        else:
            self.coord_hud.stop()
        if hasattr(self, "hud_act"):  # mirror toolbar
            self.hud_act.setChecked(checked)

    def _pick_current_mouse(self):
        try:
            pos = QCursor.pos()  # global coords
            self.x_box.setValue(int(pos.x()))
            self.y_box.setValue(int(pos.y()))
            self.fixed_radio.setChecked(True)
            QGuiApplication.beep()
        except Exception as e:
            QMessageBox.warning(self, "Pick failed", f"Could not read mouse position: {e}")

    def _toggle_controls(self, running: bool):
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.interval_box.setEnabled(not running)
        self.preset_box.setEnabled(not running)
        self.mode_box.setEnabled(not running)
        self.count_box.setEnabled(not running and not self.continuous_chk.isChecked())
        self.continuous_chk.setEnabled(not running)
        self.countdown_chk.setEnabled(not running)
        self.follow_radio.setEnabled(not running)
        self.fixed_radio.setEnabled(not running)
        self.screen_center_radio.setEnabled(not running)
        # Keep Windows radio disabled if OS not Windows
        self.win_center_radio.setEnabled(not running and (platform.system() == "Windows"))
        self.x_box.setEnabled(not running and self.fixed_radio.isChecked())
        self.y_box.setEnabled(not running and self.fixed_radio.isChecked())
        self.pick_btn.setEnabled(not running and self.fixed_radio.isChecked())


    # ---------- Helpers to resolve dynamic targets at start ----------

    def _current_screen_center(self) -> Tuple[int, int]:
        pos = QCursor.pos()
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        g = screen.availableGeometry()
        return (int(g.center().x()), int(g.center().y()))

    def _active_window_center_windows(self) -> Optional[Tuple[int, int]]:
        if platform.system() != "Windows":
            return None
        # Win32: center of foreground window (screen coords)
        user32 = ctypes.windll.user32
        try:
            user32.SetProcessDPIAware()  # best-effort for correct coords on HiDPI
        except Exception:
            pass
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        rect = ctypes.wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        cx = (rect.left + rect.right) // 2
        cy = (rect.top + rect.bottom) // 2
        return (int(cx), int(cy))

    # ---------- Start/Stop logic ----------

    def validate_inputs(self) -> Optional[str]:
        interval = self.interval_box.value()
        if interval < 1:
            return "Interval must be at least 1 ms."
        if not self.continuous_chk.isChecked():
            count = self.count_box.value()
            if count < 1:
                return "Number of clicks must be at least 1."
        if self.fixed_radio.isChecked():
            # We allow negative values (multi-monitor). Just sanity-check range.
            x, y = self.x_box.value(), self.y_box.value()
            if abs(x) > 20000 or abs(y) > 20000:
                return "X/Y seem out of range (±20000)."
        return None

    @Slot()
    def start_clicking(self):
        err = self.validate_inputs()
        if err:
            QMessageBox.warning(self, "Invalid input", err)
            return

        # Resolve target mode/position
        target_mode = "follow"
        target_pos: Optional[Tuple[int, int]] = None

        if self.fixed_radio.isChecked():
            target_mode = "fixed"
            target_pos = (self.x_box.value(), self.y_box.value())
        elif self.screen_center_radio.isChecked():
            target_mode = "fixed"
            target_pos = self._current_screen_center()
        elif self.win_center_radio.isChecked():
            # Windows only; fall back to screen center if unavailable
            win_center = self._active_window_center_windows()
            if win_center:
                target_mode = "fixed"
                target_pos = win_center
            else:
                target_mode = "fixed"
                target_pos = self._current_screen_center()

        # Create worker + thread
        self.thread = QThread(self)
        self.worker = ClickWorker()
        self.worker.moveToThread(self.thread)

        # Configure from UI
        interval_ms = self.interval_box.value()
        mode = self.mode_box.currentText()
        total_clicks = None if self.continuous_chk.isChecked() else self.count_box.value()
        countdown = self.countdown_chk.isChecked()

        self.worker.configure(interval_ms, mode, total_clicks, countdown, target_mode, target_pos)

        # Connect signals
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.on_progress)
        self.worker.status.connect(self.on_status)
        self.worker.error.connect(self.on_error)
        self.worker.finished.connect(self.on_finished)

        # UI state
        self._set_status("Arming…", "running")
        self.perf_lbl.setText("Clicks performed: 0")
        self._toggle_controls(True)

        self.thread.start()

    @Slot()
    def stop_clicking(self):
        if self.worker:
            self.worker.request_stop()

    @Slot(int)
    def on_progress(self, performed: int):
        self.perf_lbl.setText(f"Clicks performed: {performed}")

    @Slot(str)
    def on_status(self, msg: str):
        current_state = self.status_lbl.property("state") or "idle"
        m = msg.lower()
        if "stop" in m or "completed" in m:
            self._set_status(msg, "stopped")
        elif "error" in m:
            self._set_status(msg, "error")
        else:
            self._set_status(msg, "running" if current_state in ("running", "idle") else current_state)

    @Slot(str)
    def on_error(self, msg: str):
        self._set_status(msg, "error")
        QMessageBox.critical(self, "Error", msg)

    @Slot()
    def on_finished(self):
        self._toggle_controls(False)
        try:
            if self.thread and self.thread.isRunning():
                self.thread.quit()
                self.thread.wait(2000)
        finally:
            self.thread = None
            self.worker = None
        if self.status_lbl.property("state") not in ("stopped", "error"):
            self._set_status("Idle.", "idle")

    # ---------- Small helpers ----------

    def _apply_preset_interval(self, _idx: int):
        text = self.preset_box.currentText()
        # Parse forms: "X ms" or "Y s"
        try:
            val, unit = text.split()
            val = float(val)
            if unit.lower().startswith("ms"):
                ms = int(max(1, round(val)))
            else:
                ms = int(max(1, round(val * 1000)))
            self.interval_box.setValue(ms)
        except Exception:
            pass  # ignore parse errors

    def _update_cps(self, ms: int):
        cps = 1000.0 / max(1, ms)
        self.cps_lbl.setText(f"Rate: {cps:.2f} cps")

    def _show_about(self):
        QMessageBox.information(
            self, "About Auto-Clicker",
            "Auto-Clicker\n\n"
            "• F5 Start • F6 Stop • F8 Pick • ESC panic-stop\n"
            "• Dark/Light theme, Always-on-top, Live HUD\n\n"
            "MIT License"
        )

    def closeEvent(self, event):
        # Persist UI state
        try:
            self.settings.setValue("ui/dark", self._is_dark)
            self.settings.setValue("ui/always_on_top", self._always_on_top)
            self.settings.setValue("ui/hud", self.hud_chk.isChecked())
            self.settings.setValue("click/interval_ms", self.interval_box.value())
            self.settings.setValue("click/mode", self.mode_box.currentText())
            self.settings.setValue("click/count", self.count_box.value())
            self.settings.setValue("click/continuous", self.continuous_chk.isChecked())
            self.settings.setValue("click/countdown", self.countdown_chk.isChecked())

            # Target mode
            if self.follow_radio.isChecked():
                self.settings.setValue("target/mode", "follow")
            elif self.fixed_radio.isChecked():
                self.settings.setValue("target/mode", "fixed")
            elif self.screen_center_radio.isChecked():
                self.settings.setValue("target/mode", "screen_center")
            elif self.win_center_radio.isChecked():
                self.settings.setValue("target/mode", "win_center")

            self.settings.setValue("target/x", self.x_box.value())
            self.settings.setValue("target/y", self.y_box.value())
            self.settings.setValue("ui/geometry", self.saveGeometry())

            self._on_hud_toggled(False)
            if self.worker:
                self.worker.request_stop()
            if self.thread and self.thread.isRunning():
                self.thread.quit()
                self.thread.wait(2000)
        except Exception:
            pass
        event.accept()


# --------------------------- Entry point ---------------------------

def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

"""
BitTorrent Client — Alternative UI (gui2.py)
A modern dashboard-style interface with sidebar navigation,
radial progress ring, live speed chart, and card-based metrics.
Design inspired by Spotify / Discord / modern SaaS dashboards.
"""

import os
import sys
import time
import math
import subprocess
import collections
from datetime import timedelta

from PySide6.QtCore import (
    Qt, QThread, Signal, Slot, QTimer, QSize, QRectF, QPointF,
    QPropertyAnimation, QEasingCurve, Property,
)
from PySide6.QtGui import (
    QAction, QColor, QFont, QPainter, QBrush, QPen, QIcon,
    QLinearGradient, QConicalGradient, QPainterPath, QFontMetrics,
    QPalette,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QPushButton, QProgressBar, QFileDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QPlainTextEdit,
    QFrame, QSplitter, QMessageBox, QStackedWidget,
    QSizePolicy, QSpacerItem, QScrollArea,
)

from calc_hash import get_torrent_summary
from tracker import generate_peer_id, get_peers, get_all_trackers
from swarm_downloader import SwarmDownloader


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fmt_size(n: float) -> str:
    if n is None or n < 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} PB"


def fmt_speed(n: float) -> str:
    return f"{fmt_size(n)}/s"


def fmt_duration(seconds: float) -> str:
    if seconds <= 0 or seconds > 360000:
        return "—"
    td = timedelta(seconds=int(seconds))
    hrs, remainder = divmod(td.seconds, 3600)
    mins, secs = divmod(remainder, 60)
    parts = []
    if td.days:
        parts.append(f"{td.days}d")
    if hrs:
        parts.append(f"{hrs}h")
    parts.append(f"{mins}m {secs}s")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Radial Progress Ring
# ---------------------------------------------------------------------------

class RadialProgress(QWidget):
    """Large circular progress indicator with percentage in the center."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 0.0  # 0..100
        self.setMinimumSize(180, 180)
        self.setMaximumSize(220, 220)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_value(self, v: float):
        self._value = max(0.0, min(100.0, v))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        side = min(self.width(), self.height())
        x_off = (self.width() - side) / 2
        y_off = (self.height() - side) / 2

        pen_width = 10
        margin = pen_width / 2 + 4
        rect = QRectF(x_off + margin, y_off + margin,
                       side - 2 * margin, side - 2 * margin)

        # Track (background arc)
        track_pen = QPen(QColor("#1e2035"), pen_width)
        track_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track_pen)
        painter.drawArc(rect, 0, 360 * 16)

        # Value arc — gradient from purple to cyan
        if self._value > 0:
            grad = QConicalGradient(rect.center(), 90)
            grad.setColorAt(0.0, QColor("#a855f7"))  # purple
            grad.setColorAt(0.5, QColor("#6366f1"))  # indigo
            grad.setColorAt(1.0, QColor("#06b6d4"))  # cyan
            arc_pen = QPen(QBrush(grad), pen_width)
            arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(arc_pen)
            span = int(self._value / 100.0 * 360 * 16)
            painter.drawArc(rect, 90 * 16, -span)

        # Center text
        painter.setPen(QColor("#f1f5f9"))
        font = QFont("Segoe UI", 28, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"{self._value:.1f}%")


# ---------------------------------------------------------------------------
# Live Speed Chart (rolling line graph)
# ---------------------------------------------------------------------------

class SpeedChart(QWidget):
    """Real-time speed graph painted with QPainter."""

    def __init__(self, parent=None, max_points: int = 80):
        super().__init__(parent)
        self.max_points = max_points
        self.data: collections.deque[float] = collections.deque(maxlen=max_points)
        self.setMinimumHeight(120)
        self.setStyleSheet("background: transparent;")

    def add_point(self, speed_bytes: float):
        self.data.append(speed_bytes)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        pad_l, pad_r, pad_t, pad_b = 40, 8, 8, 22

        chart_w = w - pad_l - pad_r
        chart_h = h - pad_t - pad_b

        # Background
        painter.fillRect(self.rect(), QColor("#0c0d14"))

        # Grid lines
        painter.setPen(QPen(QColor("#1a1c2a"), 1))
        for i in range(5):
            y = pad_t + int(chart_h * i / 4)
            painter.drawLine(pad_l, y, w - pad_r, y)

        if len(self.data) < 2:
            painter.setPen(QColor("#4b5563"))
            painter.setFont(QFont("Segoe UI", 9))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Waiting for data…")
            return

        max_val = max(self.data) * 1.2 or 1.0

        # Y-axis labels
        painter.setPen(QColor("#4b5563"))
        painter.setFont(QFont("Segoe UI", 8))
        for i in range(5):
            y = pad_t + int(chart_h * i / 4)
            val = max_val * (1 - i / 4)
            label = fmt_size(val) + "/s"
            painter.drawText(0, y - 6, pad_l - 4, 14,
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             label)

        # Build path
        points = list(self.data)
        n = len(points)
        step_x = chart_w / (self.max_points - 1)

        path = QPainterPath()
        fill_path = QPainterPath()

        start_x = pad_l + (self.max_points - n) * step_x

        for i, val in enumerate(points):
            x = start_x + i * step_x
            y = pad_t + chart_h * (1 - val / max_val)
            if i == 0:
                path.moveTo(x, y)
                fill_path.moveTo(x, pad_t + chart_h)
                fill_path.lineTo(x, y)
            else:
                path.lineTo(x, y)
                fill_path.lineTo(x, y)

        # Close fill
        last_x = start_x + (n - 1) * step_x
        fill_path.lineTo(last_x, pad_t + chart_h)
        fill_path.closeSubpath()

        # Gradient fill under the line
        grad = QLinearGradient(0, pad_t, 0, pad_t + chart_h)
        grad.setColorAt(0.0, QColor(139, 92, 246, 60))   # purple-ish
        grad.setColorAt(1.0, QColor(139, 92, 246, 0))
        painter.setBrush(QBrush(grad))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(fill_path)

        # Line
        line_pen = QPen(QColor("#8b5cf6"), 2)
        line_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(line_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        # Dot on the latest point
        if points:
            last_val = points[-1]
            lx = start_x + (n - 1) * step_x
            ly = pad_t + chart_h * (1 - last_val / max_val)
            painter.setBrush(QColor("#a78bfa"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(lx, ly), 4, 4)


# ---------------------------------------------------------------------------
# Piece Heatmap (horizontal bar style, different from grid)
# ---------------------------------------------------------------------------

class PieceBar(QWidget):
    """Horizontal segmented bar showing piece completion — each segment
    is a vertical stripe that lights up when done."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.total = 0
        self.completed: set[int] = set()
        self.setMinimumHeight(28)
        self.setMaximumHeight(36)

    def reset(self, total: int):
        self.total = total
        self.completed.clear()
        self.update()

    def mark_done(self, idx: int):
        self.completed.add(idx)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        w = self.width()
        h = self.height()
        painter.fillRect(self.rect(), QColor("#0c0d14"))

        if self.total <= 0:
            painter.setPen(QColor("#4b5563"))
            painter.setFont(QFont("Segoe UI", 9))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No pieces")
            return

        # Each segment is a thin vertical stripe
        seg_w = w / self.total
        painter.setPen(Qt.PenStyle.NoPen)

        color_done = QColor("#8b5cf6")
        color_empty = QColor("#1a1c2a")

        if seg_w >= 1.5:
            # Draw individually
            for i in range(self.total):
                x = i * seg_w
                painter.setBrush(color_done if i in self.completed else color_empty)
                painter.drawRect(QRectF(x, 0, max(seg_w - 0.5, 1), h))
        else:
            # Too many pieces — bucket them
            buckets = min(w, self.total)
            bw = w / buckets
            pieces_per_bucket = self.total / buckets
            for b in range(buckets):
                start_p = int(b * pieces_per_bucket)
                end_p = int((b + 1) * pieces_per_bucket)
                done_in_bucket = sum(1 for p in range(start_p, end_p) if p in self.completed)
                total_in_bucket = max(1, end_p - start_p)
                ratio = done_in_bucket / total_in_bucket

                r = int(color_empty.red()   + (color_done.red()   - color_empty.red())   * ratio)
                g = int(color_empty.green() + (color_done.green() - color_empty.green()) * ratio)
                b_val = int(color_empty.blue()  + (color_done.blue()  - color_empty.blue())  * ratio)
                painter.setBrush(QColor(r, g, b_val))
                painter.drawRect(QRectF(b * bw, 0, max(bw, 1), h))


# ---------------------------------------------------------------------------
# Sidebar Navigation Button
# ---------------------------------------------------------------------------

class NavButton(QPushButton):
    """Sidebar navigation button with icon + label."""

    def __init__(self, icon_char: str, label: str, parent=None):
        super().__init__(parent)
        self.setText(f"  {icon_char}   {label}")
        self.setCheckable(True)
        self.setFixedHeight(44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


# ---------------------------------------------------------------------------
# Metric Card
# ---------------------------------------------------------------------------

class MetricCard(QFrame):
    """Small dashboard card showing a label + value."""

    def __init__(self, title: str, value: str = "—", parent=None):
        super().__init__(parent)
        self.setProperty("class", "metric-card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("color: #6b7280; font-size: 11px; font-weight: 700; letter-spacing: 0.5px;")

        self.value_label = QLabel(value)
        self.value_label.setStyleSheet("color: #f1f5f9; font-size: 20px; font-weight: 700;")

        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)

    def set_value(self, text: str):
        self.value_label.setText(text)


# ---------------------------------------------------------------------------
# Torrent Worker Thread (same logic, different file)
# ---------------------------------------------------------------------------

class TorrentWorker(QThread):
    sig_log          = Signal(str)
    sig_metadata     = Signal(dict)
    sig_tracker_done = Signal(str, int)
    sig_peers_total  = Signal(int)
    sig_peer_status  = Signal(str, int, str)
    sig_piece_done   = Signal(int, int, int, float, float)
    sig_finished     = Signal(bool, str)

    def __init__(self, torrent_path: str, download_dir: str, max_workers: int = 30):
        super().__init__()
        self.torrent_path = torrent_path
        self.download_dir = download_dir
        self.max_workers = max_workers
        self.downloader: SwarmDownloader | None = None
        self._stopped = False

    def request_stop(self):
        self._stopped = True
        if self.downloader:
            self.downloader.stop()

    def run(self):
        try:
            self.sig_log.emit("Parsing torrent file …")
            summary = get_torrent_summary(self.torrent_path)
            self.sig_metadata.emit(summary)

            peer_id = generate_peer_id()
            trackers = get_all_trackers(summary)
            self.sig_log.emit(f"Contacting {len(trackers)} tracker(s) simultaneously …")

            import threading
            from concurrent.futures import ThreadPoolExecutor

            peers: list[tuple[str, int]] = []
            peers_lock = threading.Lock()

            def _query_single_tracker(url: str):
                if self._stopped:
                    return
                try:
                    found = get_peers(
                        announce_url=url,
                        info_hash_bytes=summary["info_hash"],
                        total_length=summary["total_length"],
                        peer_id=peer_id,
                    )
                    if found:
                        with peers_lock:
                            for p in found:
                                if p not in peers:
                                    peers.append(p)
                        self.sig_tracker_done.emit(url, len(found))
                        self.sig_log.emit(f"  ✓ {url}  →  {len(found)} peers")
                    else:
                        self.sig_tracker_done.emit(url, 0)
                        self.sig_log.emit(f"  ✗ {url}  →  0 peers")
                except Exception as exc:
                    self.sig_tracker_done.emit(url, -1)
                    self.sig_log.emit(f"  ✗ {url}  →  {type(exc).__name__}")

            with ThreadPoolExecutor(max_workers=min(16, len(trackers) or 1)) as pool:
                list(pool.map(_query_single_tracker, trackers))

            self.sig_peers_total.emit(len(peers))
            self.sig_log.emit(f"Discovered {len(peers)} unique peers")

            if not peers:
                self.sig_finished.emit(False, "No peers found.")
                return
            if self._stopped:
                return

            os.makedirs(self.download_dir, exist_ok=True)

            self.downloader = SwarmDownloader(
                summary=summary,
                peers=peers,
                peer_id=peer_id,
                download_dir=self.download_dir,
                max_workers=self.max_workers,
                on_piece_done=self._on_piece,
                on_log=lambda msg: self.sig_log.emit(msg),
                on_peer_status=lambda ip, port, st: self.sig_peer_status.emit(ip, port, st),
            )

            self.sig_log.emit(f"Swarm started — {len(peers)} peers, {self.max_workers} workers")
            success = self.downloader.download()

            if self._stopped:
                self.sig_finished.emit(False, "Stopped by user.")
            elif success:
                self.sig_finished.emit(True, "Download complete ✓")
            else:
                done = len(self.downloader.done)
                total = self.downloader.total_pieces
                self.sig_finished.emit(False, f"Partial: {done}/{total} pieces")

        except Exception as exc:
            self.sig_log.emit(f"ERROR: {exc}")
            self.sig_finished.emit(False, str(exc))

    def _on_piece(self, idx, done_cnt, total, bytes_done, total_len, speed):
        self.sig_piece_done.emit(idx, done_cnt, total, bytes_done, speed)


# ---------------------------------------------------------------------------
# Stylesheet
# ---------------------------------------------------------------------------

STYLE = """
QMainWindow {
    background-color: #08090d;
}
QWidget {
    color: #c9cdd6;
    font-family: "Segoe UI", "SF Pro Display", system-ui, sans-serif;
    font-size: 13px;
}

/* ── Sidebar ─────────────────────────────────── */
QFrame#sidebar {
    background-color: #0c0d14;
    border-right: 1px solid #16182a;
}
NavButton {
    background: transparent;
    border: none;
    border-radius: 8px;
    text-align: left;
    padding: 0 12px;
    font-size: 13px;
    font-weight: 600;
    color: #6b7280;
}
NavButton:hover {
    background-color: #13152a;
    color: #a5b4fc;
}
NavButton:checked {
    background-color: #1e1b4b;
    color: #a78bfa;
}

/* ── Metric cards ────────────────────────────── */
QFrame[class="metric-card"] {
    background-color: #0f1019;
    border: 1px solid #1a1c2e;
    border-radius: 10px;
}

/* ── Info panel ──────────────────────────────── */
QFrame#infoPanel {
    background-color: #0f1019;
    border: 1px solid #1a1c2e;
    border-radius: 10px;
}

/* ── Tables ──────────────────────────────────── */
QTableWidget {
    background-color: #0c0d14;
    alternate-background-color: #0f1019;
    gridline-color: #16182a;
    border: none;
    selection-background-color: #1e1b4b;
    selection-color: #e2e8f0;
    font-size: 12px;
}
QTableWidget::item {
    padding: 5px 10px;
    border: none;
}
QHeaderView::section {
    background-color: #0c0d14;
    color: #6b7280;
    font-weight: 700;
    font-size: 11px;
    padding: 7px 10px;
    border: none;
    border-bottom: 1px solid #1a1c2e;
}

/* ── Log ─────────────────────────────────────── */
QPlainTextEdit#logView {
    background-color: #08090d;
    color: #6b7280;
    border: none;
    font-family: "Cascadia Code", "Consolas", monospace;
    font-size: 11px;
    selection-background-color: #1e1b4b;
}

/* ── Action buttons ──────────────────────────── */
QPushButton#btnPrimary {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #7c3aed, stop:1 #6366f1);
    border: none;
    border-radius: 8px;
    padding: 10px 24px;
    color: #ffffff;
    font-weight: 700;
    font-size: 13px;
}
QPushButton#btnPrimary:hover {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #6d28d9, stop:1 #4f46e5);
}
QPushButton#btnPrimary:disabled {
    background: #1a1c2e;
    color: #4b5563;
}
QPushButton#btnDanger {
    background: transparent;
    border: 1px solid #7f1d1d;
    border-radius: 8px;
    padding: 10px 20px;
    color: #fca5a5;
    font-weight: 600;
    font-size: 13px;
}
QPushButton#btnDanger:hover {
    background: #450a0a;
    border-color: #991b1b;
}
QPushButton#btnDanger:disabled {
    border-color: #1a1c2e;
    color: #4b5563;
}
QPushButton#btnGhost {
    background: transparent;
    border: 1px solid #1a1c2e;
    border-radius: 8px;
    padding: 10px 20px;
    color: #9ca3af;
    font-weight: 600;
    font-size: 13px;
}
QPushButton#btnGhost:hover {
    background: #13152a;
    border-color: #2d3250;
    color: #e2e8f0;
}

/* ── Scrollbars ──────────────────────────────── */
QScrollBar:vertical {
    background: #08090d;
    width: 6px;
}
QScrollBar::handle:vertical {
    background: #1e2035;
    border-radius: 3px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: #3d4154;
}
QScrollBar::add-line, QScrollBar::sub-line,
QScrollBar::add-page, QScrollBar::sub-page {
    background: none;
    height: 0; width: 0;
}
QScrollBar:horizontal {
    background: #08090d;
    height: 6px;
}
QScrollBar::handle:horizontal {
    background: #1e2035;
    border-radius: 3px;
}

/* ── Piece bar frame ─────────────────────────── */
QFrame#pieceFrame {
    background-color: #0c0d14;
    border: 1px solid #1a1c2e;
    border-radius: 8px;
}
"""


# ---------------------------------------------------------------------------
# Main Window
# ---------------------------------------------------------------------------

class DashboardWindow(QMainWindow):
    """Modern dashboard-style BitTorrent client."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("⚡ Torrent Dashboard")
        self.resize(1140, 760)
        self.setStyleSheet(STYLE)

        # State
        self._torrent_path = ""
        self._download_dir = os.path.join(os.getcwd(), "downloads")
        self._summary: dict | None = None
        self._worker: TorrentWorker | None = None
        self._peer_rows: dict[str, int] = {}
        self._is_downloading = False
        self._total_peers = 0
        self._speed_timer = QTimer(self)
        self._speed_timer.setInterval(1000)
        self._speed_timer.timeout.connect(self._tick_speed)
        self._last_speed = 0.0

        self._build_ui()

    # ===================================================================
    #  UI Construction
    # ===================================================================

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ── Sidebar ───────────────────────────────────────────────
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(200)
        sb_layout = QVBoxLayout(sidebar)
        sb_layout.setContentsMargins(12, 20, 12, 20)
        sb_layout.setSpacing(4)

        # Logo
        logo = QLabel("⚡ Torrent")
        logo.setStyleSheet(
            "font-size: 18px; font-weight: 800; color: #a78bfa; "
            "padding: 0 0 16px 4px; border: none;"
        )
        sb_layout.addWidget(logo)

        self.nav_buttons: list[NavButton] = []
        pages = [
            ("🏠", "Dashboard"),
            ("🌐", "Peers"),
            ("📜", "Log"),
        ]
        for icon, label in pages:
            btn = NavButton(icon, label)
            btn.clicked.connect(lambda checked, l=label: self._navigate(l))
            sb_layout.addWidget(btn)
            self.nav_buttons.append(btn)

        sb_layout.addStretch()

        # Bottom sidebar: file actions
        self.btn_browse = QPushButton("📁  Open Torrent")
        self.btn_browse.setObjectName("btnGhost")
        self.btn_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_browse.clicked.connect(self._browse_torrent)
        sb_layout.addWidget(self.btn_browse)

        self.btn_folder = QPushButton("📂  Downloads")
        self.btn_folder.setObjectName("btnGhost")
        self.btn_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_folder.clicked.connect(self._open_folder)
        sb_layout.addWidget(self.btn_folder)

        root_layout.addWidget(sidebar)

        # ── Stacked Pages ─────────────────────────────────────────
        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_dashboard_page())   # 0
        self.pages.addWidget(self._build_peers_page())       # 1
        self.pages.addWidget(self._build_log_page())         # 2
        root_layout.addWidget(self.pages, 1)

        # Default to Dashboard
        self.nav_buttons[0].setChecked(True)
        self.pages.setCurrentIndex(0)

    # ── Page builders ─────────────────────────────────────────────

    def _build_dashboard_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 28, 28, 20)
        layout.setSpacing(20)

        # -- Header row: title + action buttons
        header = QHBoxLayout()
        title = QLabel("Dashboard")
        title.setStyleSheet("font-size: 22px; font-weight: 800; color: #f1f5f9;")
        header.addWidget(title)
        header.addStretch()

        self.btn_start = QPushButton("▶  Start Download")
        self.btn_start.setObjectName("btnPrimary")
        self.btn_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_start.setEnabled(False)
        self.btn_start.clicked.connect(self._start_download)

        self.btn_stop = QPushButton("⏹  Stop")
        self.btn_stop.setObjectName("btnDanger")
        self.btn_stop.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop_download)

        header.addWidget(self.btn_start)
        header.addWidget(self.btn_stop)
        layout.addLayout(header)

        # -- Torrent info bar
        self.info_panel = QFrame()
        self.info_panel.setObjectName("infoPanel")
        ip_layout = QHBoxLayout(self.info_panel)
        ip_layout.setContentsMargins(20, 14, 20, 14)
        ip_layout.setSpacing(24)

        self.lbl_torrent_name = QLabel("No torrent loaded")
        self.lbl_torrent_name.setStyleSheet("color: #e2e8f0; font-size: 14px; font-weight: 600;")
        self.lbl_torrent_size = QLabel("")
        self.lbl_torrent_size.setStyleSheet("color: #6b7280; font-size: 12px;")
        self.lbl_torrent_hash = QLabel("")
        self.lbl_torrent_hash.setStyleSheet("color: #4b5563; font-size: 11px;")
        self.lbl_torrent_hash.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        info_text = QVBoxLayout()
        info_text.setSpacing(2)
        info_text.addWidget(self.lbl_torrent_name)

        info_detail = QHBoxLayout()
        info_detail.setSpacing(16)
        info_detail.addWidget(self.lbl_torrent_size)
        info_detail.addWidget(self.lbl_torrent_hash)
        info_detail.addStretch()
        info_text.addLayout(info_detail)

        ip_layout.addLayout(info_text, 1)

        self.lbl_status_badge = QLabel("  IDLE  ")
        self.lbl_status_badge.setStyleSheet(
            "background-color: #1a1c2e; color: #6b7280; font-size: 11px; font-weight: 700; "
            "padding: 4px 12px; border-radius: 12px; letter-spacing: 0.5px;"
        )
        ip_layout.addWidget(self.lbl_status_badge)

        layout.addWidget(self.info_panel)

        # -- Center area: Radial ring + metric cards
        center = QHBoxLayout()
        center.setSpacing(24)

        # Left: radial progress
        ring_container = QVBoxLayout()
        ring_container.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.radial = RadialProgress()
        ring_container.addWidget(self.radial, 0, Qt.AlignmentFlag.AlignCenter)
        center.addLayout(ring_container)

        # Right: metric card grid
        cards_grid = QGridLayout()
        cards_grid.setSpacing(12)

        self.card_speed    = MetricCard("DOWNLOAD SPEED", "0 B/s")
        self.card_downloaded = MetricCard("DOWNLOADED", "0 B")
        self.card_pieces   = MetricCard("PIECES", "0 / 0")
        self.card_peers    = MetricCard("PEERS", "0")
        self.card_eta      = MetricCard("TIME REMAINING", "—")
        self.card_elapsed  = MetricCard("ELAPSED", "0s")

        cards_grid.addWidget(self.card_speed,      0, 0)
        cards_grid.addWidget(self.card_downloaded,  0, 1)
        cards_grid.addWidget(self.card_pieces,      0, 2)
        cards_grid.addWidget(self.card_peers,       1, 0)
        cards_grid.addWidget(self.card_eta,         1, 1)
        cards_grid.addWidget(self.card_elapsed,     1, 2)

        center.addLayout(cards_grid, 1)
        layout.addLayout(center)

        # -- Piece bar
        piece_frame = QFrame()
        piece_frame.setObjectName("pieceFrame")
        pf_layout = QVBoxLayout(piece_frame)
        pf_layout.setContentsMargins(12, 10, 12, 10)
        pf_layout.setSpacing(6)
        piece_title = QLabel("PIECE PROGRESS")
        piece_title.setStyleSheet("color: #6b7280; font-size: 11px; font-weight: 700; letter-spacing: 0.5px;")
        self.piece_bar = PieceBar()
        pf_layout.addWidget(piece_title)
        pf_layout.addWidget(self.piece_bar)
        layout.addWidget(piece_frame)

        # -- Speed chart
        chart_title = QLabel("SPEED OVER TIME")
        chart_title.setStyleSheet("color: #6b7280; font-size: 11px; font-weight: 700; letter-spacing: 0.5px; margin-top: 4px;")
        layout.addWidget(chart_title)
        self.speed_chart = SpeedChart()
        layout.addWidget(self.speed_chart, 1)

        return page

    def _build_peers_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 28, 28, 20)
        layout.setSpacing(12)

        header = QLabel("Connected Peers")
        header.setStyleSheet("font-size: 22px; font-weight: 800; color: #f1f5f9;")
        layout.addWidget(header)

        self.peer_table = QTableWidget(0, 3)
        self.peer_table.setHorizontalHeaderLabels(["IP Address", "Port", "Status"])
        self.peer_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.peer_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.peer_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.peer_table.verticalHeader().setVisible(False)
        self.peer_table.setAlternatingRowColors(True)
        self.peer_table.setShowGrid(False)
        layout.addWidget(self.peer_table, 1)

        return page

    def _build_log_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 28, 28, 20)
        layout.setSpacing(12)

        header = QLabel("Activity Log")
        header.setStyleSheet("font-size: 22px; font-weight: 800; color: #f1f5f9;")
        layout.addWidget(header)

        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("logView")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        layout.addWidget(self.log_view, 1)

        return page

    # ===================================================================
    #  Navigation
    # ===================================================================

    def _navigate(self, label: str):
        page_map = {"Dashboard": 0, "Peers": 1, "Log": 2}
        idx = page_map.get(label, 0)
        self.pages.setCurrentIndex(idx)
        for btn in self.nav_buttons:
            btn.setChecked(label in btn.text())

    # ===================================================================
    #  Actions
    # ===================================================================

    def _browse_torrent(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Torrent", "", "Torrent Files (*.torrent);;All (*)"
        )
        if path:
            self._load_torrent(path)

    def _load_torrent(self, path: str):
        try:
            summary = get_torrent_summary(path)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Cannot parse torrent:\n{exc}")
            return

        self._torrent_path = path
        self._summary = summary

        name = summary["name"]
        total = summary["total_length"]
        n_pieces = len(summary["piece_hashes"])

        self.lbl_torrent_name.setText(name)
        self.lbl_torrent_size.setText(f"{fmt_size(total)}  ·  {n_pieces} pieces")
        self.lbl_torrent_hash.setText(summary["info_hash_hex"])
        self.lbl_status_badge.setText("  READY  ")
        self.lbl_status_badge.setStyleSheet(
            "background-color: #1e1b4b; color: #a78bfa; font-size: 11px; font-weight: 700; "
            "padding: 4px 12px; border-radius: 12px; letter-spacing: 0.5px;"
        )

        self.piece_bar.reset(n_pieces)
        self.radial.set_value(0)
        self.card_pieces.set_value(f"0 / {n_pieces}")
        self.card_downloaded.set_value("0 B")
        self.card_speed.set_value("0 B/s")
        self.card_eta.set_value("—")
        self.card_elapsed.set_value("0s")
        self.card_peers.set_value("0")

        self.btn_start.setEnabled(True)
        self._log(f"Loaded: {name} ({fmt_size(total)})")

    def _start_download(self):
        if not self._torrent_path or self._is_downloading:
            return

        self._is_downloading = True
        self._start_time = time.time()
        self._peer_rows.clear()
        self.peer_table.setRowCount(0)
        self._last_speed = 0.0

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_browse.setEnabled(False)

        self.lbl_status_badge.setText("  DOWNLOADING  ")
        self.lbl_status_badge.setStyleSheet(
            "background-color: #052e16; color: #4ade80; font-size: 11px; font-weight: 700; "
            "padding: 4px 12px; border-radius: 12px; letter-spacing: 0.5px;"
        )

        self._speed_timer.start()

        self._worker = TorrentWorker(self._torrent_path, self._download_dir)
        self._worker.sig_log.connect(self._log)
        self._worker.sig_metadata.connect(self._on_metadata)
        self._worker.sig_peers_total.connect(self._on_peers_total)
        self._worker.sig_peer_status.connect(self._on_peer_status)
        self._worker.sig_piece_done.connect(self._on_piece_done)
        self._worker.sig_finished.connect(self._on_finished)
        self._worker.start()

    def _stop_download(self):
        if self._worker:
            self._log("Stopping …")
            self._worker.request_stop()
            self.btn_stop.setEnabled(False)

    def _open_folder(self):
        target = self._download_dir
        if not os.path.isdir(target):
            os.makedirs(target, exist_ok=True)
        subprocess.Popen(["explorer", os.path.normpath(target)])

    # ===================================================================
    #  Speed timer tick (updates elapsed time + chart)
    # ===================================================================

    def _tick_speed(self):
        if not self._is_downloading:
            return
        elapsed = time.time() - self._start_time
        self.card_elapsed.set_value(fmt_duration(elapsed))
        self.speed_chart.add_point(self._last_speed)

    # ===================================================================
    #  Slots
    # ===================================================================

    @Slot(str)
    def _log(self, text: str):
        ts = time.strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{ts}]  {text}")

    @Slot(dict)
    def _on_metadata(self, summary: dict):
        self._summary = summary
        n = len(summary["piece_hashes"])
        self.piece_bar.reset(n)
        self.card_pieces.set_value(f"0 / {n}")

    @Slot(int)
    def _on_peers_total(self, total: int):
        self._total_peers = total
        self.card_peers.set_value(str(total))

    @Slot(str, int, str)
    def _on_peer_status(self, ip: str, port: int, status: str):
        key = f"{ip}:{port}"
        if key not in self._peer_rows:
            row = self.peer_table.rowCount()
            self.peer_table.insertRow(row)
            self.peer_table.setItem(row, 0, QTableWidgetItem(ip))
            self.peer_table.setItem(row, 1, QTableWidgetItem(str(port)))
            self.peer_table.setItem(row, 2, QTableWidgetItem(status))
            self._peer_rows[key] = row
        else:
            row = self._peer_rows[key]
            item = self.peer_table.item(row, 2)
            if item:
                item.setText(status)

        # Color coding
        color_map = {
            "Active / Unchoked": "#22c55e",
            "Connected (Choked)": "#eab308",
            "Connecting": "#818cf8",
            "Discovered": "#6b7280",
            "Disconnected": "#374151",
            "Timeout (Choked)": "#ef4444",
        }
        row = self._peer_rows[key]
        item = self.peer_table.item(row, 2)
        if item:
            item.setForeground(QColor(color_map.get(status, "#6b7280")))

    @Slot(int, int, int, float, float)
    def _on_piece_done(self, idx: int, done_cnt: int, total: int, bytes_done: float, speed: float):
        pct = (done_cnt / total * 100) if total else 0
        self._last_speed = speed

        self.radial.set_value(pct)
        self.piece_bar.mark_done(idx)
        self.card_speed.set_value(fmt_speed(speed))
        self.card_speed.value_label.setStyleSheet("color: #a78bfa; font-size: 20px; font-weight: 700;")
        self.card_downloaded.set_value(fmt_size(bytes_done))
        self.card_pieces.set_value(f"{done_cnt} / {total}")

        if speed > 0 and self._summary:
            remaining = self._summary["total_length"] - bytes_done
            self.card_eta.set_value(fmt_duration(remaining / speed))
        else:
            self.card_eta.set_value("—")

    @Slot(bool, str)
    def _on_finished(self, success: bool, message: str):
        self._is_downloading = False
        self._speed_timer.stop()
        self._last_speed = 0.0

        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_browse.setEnabled(True)

        self.card_speed.set_value("0 B/s")
        self.card_speed.value_label.setStyleSheet("color: #f1f5f9; font-size: 20px; font-weight: 700;")

        if success:
            self.lbl_status_badge.setText("  COMPLETE  ")
            self.lbl_status_badge.setStyleSheet(
                "background-color: #052e16; color: #4ade80; font-size: 11px; font-weight: 700; "
                "padding: 4px 12px; border-radius: 12px; letter-spacing: 0.5px;"
            )
            self.radial.set_value(100.0)
        else:
            self.lbl_status_badge.setText("  STOPPED  ")
            self.lbl_status_badge.setStyleSheet(
                "background-color: #450a0a; color: #fca5a5; font-size: 11px; font-weight: 700; "
                "padding: 4px 12px; border-radius: 12px; letter-spacing: 0.5px;"
            )

        elapsed = time.time() - self._start_time if hasattr(self, '_start_time') else 0
        self.card_elapsed.set_value(fmt_duration(elapsed))
        self.card_eta.set_value("—")
        self._log(f"{message}  ({fmt_duration(elapsed)})")

        if success:
            QMessageBox.information(self, "Complete", f"{message}\n\nSaved to: {self._download_dir}")

    # ===================================================================
    #  Window events
    # ===================================================================

    def closeEvent(self, event):
        if self._is_downloading:
            reply = QMessageBox.question(
                self, "Quit?", "Download in progress. Stop and quit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                event.ignore()
                return
            self._stop_download()
            if self._worker:
                self._worker.wait(3000)
        event.accept()


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = DashboardWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

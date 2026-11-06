"""
qBittorrent v4.6 Desktop Client Clone (gui3.py)
An exact, pixel-authentic replica of qBittorrent built with PySide6.
Features:
- Authentic qBittorrent Dark and Light themes
- Pixel-perfect vector icons generated on-the-fly (no missing image assets)
- Multi-torrent support with individual queue control
- Category & Status sidebar filter tree (All, Downloading, Seeding, Completed, Paused, etc.)
- Transfer list with custom in-cell progress bar delegate
- Comprehensive bottom details tabs:
    - General (Transfer & Information panels)
    - Trackers (Tier, URL, Status, Peers, Seeds)
    - Peers (IP, Port, Client, Flags, Progress, Speed)
    - Pieces (Interactive piece map visualizer + availability bar)
    - Content (File tree with sizes, priority, checkboxes)
    - Speed (Live dual-stream Download/Upload rolling graph)
    - Execution Log (Timestamped categorized logs)
- qBittorrent Status Bar (DHT nodes, Speed limits, Network status, Free disk space)
- Full modal dialogs: Add New Torrent, Preferences (Options), Delete Confirmation, About qBittorrent
- Seamless integration with SwarmDownloader, DiskManager, and tracker scraper
"""

import os
import sys
import time
import math
import shutil
import threading
import subprocess
import collections
from datetime import datetime, timedelta

from PySide6.QtCore import (
    Qt, QThread, Signal, Slot, QTimer, QSize, QRect, QRectF, QPoint, QPointF,
    QAbstractTableModel, QModelIndex,
)
from PySide6.QtGui import (
    QAction, QColor, QFont, QPainter, QBrush, QPen, QIcon, QPixmap, QPolygonF,
    QLinearGradient, QFontMetrics, QCursor, QKeySequence,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QPushButton, QProgressBar, QFileDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QPlainTextEdit,
    QTabWidget, QFrame, QSplitter, QMessageBox, QToolBar, QStatusBar,
    QMenuBar, QMenu, QAbstractItemView, QSizePolicy, QSpinBox, QDialog,
    QDialogButtonBox, QFormLayout, QLineEdit, QComboBox, QCheckBox,
    QTreeWidget, QTreeWidgetItem, QStyledItemDelegate, QStyleOptionViewItem,
    QToolButton, QStackedWidget, QListWidget, QListWidgetItem, QGroupBox,
    QStyle,
)

from calc_hash import get_torrent_summary
from tracker import generate_peer_id, get_peers, get_all_trackers
from swarm_downloader import SwarmDownloader
from disk_manager import DiskManager


# ===========================================================================
# Helpers & Formatting
# ===========================================================================

def fmt_size(n: float) -> str:
    """Formats bytes into KiB, MiB, GiB matching qBittorrent style."""
    if n is None or n < 0:
        return "0 B"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024.0:
            return f"{n:.2f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
    return f"{n:.2f} PiB"


def fmt_speed(n: float) -> str:
    """Formats speed as e.g. 1.25 MiB/s."""
    if not n or n <= 0:
        return "0 B/s"
    return f"{fmt_size(n)}/s"


def fmt_duration(seconds: float) -> str:
    """Formats duration into human-readable e.g. 2m 14s or ∞."""
    if seconds <= 0 or seconds > 3600000:
        return "∞"
    td = timedelta(seconds=int(seconds))
    hrs, remainder = divmod(td.seconds, 3600)
    mins, secs = divmod(remainder, 60)
    parts = []
    if td.days:
        parts.append(f"{td.days}d")
    if hrs:
        parts.append(f"{hrs}h")
    if mins or hrs:
        parts.append(f"{mins}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def get_free_disk_space(path: str) -> str:
    """Returns free disk space on the given path."""
    try:
        total, used, free = shutil.disk_usage(path)
        return fmt_size(free)
    except Exception:
        return "—"


# ===========================================================================
# Icon Factory: Pixel-perfect vector icons for qBittorrent
# ===========================================================================

class IconFactory:
    """Generates sharp, clean Qt vector icons dynamically."""

    @staticmethod
    def create_qb_logo(size: int = 64) -> QIcon:
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Outer circular blue gradient
        grad = QLinearGradient(0, 0, size, size)
        grad.setColorAt(0.0, QColor("#1e88e5"))
        grad.setColorAt(1.0, QColor("#1565c0"))
        p.setBrush(QBrush(grad))
        p.setPen(QPen(QColor("#0d47a1"), 1.5))
        p.drawEllipse(2, 2, size - 4, size - 4)

        # Draw "qb" letters inside
        p.setPen(QColor("#ffffff"))
        font = QFont("Segoe UI", int(size * 0.44), QFont.Weight.Bold)
        p.setFont(font)
        p.drawText(QRect(0, -int(size * 0.05), size, size), Qt.AlignmentFlag.AlignCenter, "qb")
        p.end()
        return QIcon(pix)

    @staticmethod
    def create_add_file(size: int = 24) -> QIcon:
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # White paper with folded corner
        p.setPen(QPen(QColor("#94a3b8"), 1.2))
        p.setBrush(QColor("#f8fafc"))
        poly = QPolygonF([
            QPointF(4, 2), QPointF(14, 2), QPointF(19, 7),
            QPointF(19, 21), QPointF(4, 21)
        ])
        p.drawPolygon(poly)

        # Folded corner fold
        p.setBrush(QColor("#cbd5e1"))
        fold = QPolygonF([QPointF(14, 2), QPointF(19, 7), QPointF(14, 7)])
        p.drawPolygon(fold)

        # Subtle text lines on page
        p.setPen(QPen(QColor("#94a3b8"), 1))
        p.drawLine(7, 9, 13, 9)
        p.drawLine(7, 12, 16, 12)
        p.drawLine(7, 15, 12, 15)

        # Plus badge (bottom right)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#16a34a"))
        p.drawEllipse(11, 11, 11, 11)
        p.setPen(QPen(QColor("#ffffff"), 1.8))
        p.drawLine(16.5, 13.5, 16.5, 19.5)
        p.drawLine(13.5, 16.5, 19.5, 16.5)
        p.end()
        return QIcon(pix)

    @staticmethod
    def create_add_link(size: int = 24) -> QIcon:
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Horseshoe Magnet
        p.setPen(QPen(QColor("#ef4444"), 3.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        p.drawArc(QRectF(4, 3, 13, 13), 0, 180 * 16)
        p.drawLine(QPointF(4, 9), QPointF(4, 15))
        p.drawLine(QPointF(17, 9), QPointF(17, 15))

        # Silver tips
        p.setPen(QPen(QColor("#cbd5e1"), 3.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        p.drawLine(QPointF(4, 15), QPointF(4, 18))
        p.drawLine(QPointF(17, 15), QPointF(17, 18))

        # Plus badge
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#16a34a"))
        p.drawEllipse(11, 11, 11, 11)
        p.setPen(QPen(QColor("#ffffff"), 1.8))
        p.drawLine(16.5, 13.5, 16.5, 19.5)
        p.drawLine(13.5, 16.5, 19.5, 16.5)
        p.end()
        return QIcon(pix)

    @staticmethod
    def create_delete(size: int = 24) -> QIcon:
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Red circle with white X
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#ef4444"))
        p.drawEllipse(2, 2, 20, 20)

        p.setPen(QPen(QColor("#ffffff"), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(7, 7, 17, 17)
        p.drawLine(17, 7, 7, 17)
        p.end()
        return QIcon(pix)

    @staticmethod
    def create_resume(size: int = 24) -> QIcon:
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Green circle with white play triangle
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#22c55e"))
        p.drawEllipse(2, 2, 20, 20)

        p.setBrush(QColor("#ffffff"))
        poly = QPolygonF([QPointF(8, 6.5), QPointF(17, 12), QPointF(8, 17.5)])
        p.drawPolygon(poly)
        p.end()
        return QIcon(pix)

    @staticmethod
    def create_pause(size: int = 24) -> QIcon:
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Amber circle with two white bars
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#f59e0b"))
        p.drawEllipse(2, 2, 20, 20)

        p.setBrush(QColor("#ffffff"))
        p.drawRoundedRect(7, 6, 3, 12, 1, 1)
        p.drawRoundedRect(13, 6, 3, 12, 1, 1)
        p.end()
        return QIcon(pix)

    @staticmethod
    def create_arrow(size: int = 24, direction: str = "up", double: bool = False) -> QIcon:
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#94a3b8"))

        if direction == "up":
            if double:
                # Top priority double triangle
                p.drawPolygon(QPolygonF([QPointF(12, 3), QPointF(5, 10), QPointF(19, 10)]))
                p.drawPolygon(QPolygonF([QPointF(12, 11), QPointF(5, 18), QPointF(19, 18)]))
            else:
                p.drawPolygon(QPolygonF([QPointF(12, 6), QPointF(5, 16), QPointF(19, 16)]))
        else:
            if double:
                # Bottom priority double triangle
                p.drawPolygon(QPolygonF([QPointF(12, 12), QPointF(5, 5), QPointF(19, 5)]))
                p.drawPolygon(QPolygonF([QPointF(12, 20), QPointF(5, 13), QPointF(19, 13)]))
            else:
                p.drawPolygon(QPolygonF([QPointF(12, 17), QPointF(5, 7), QPointF(19, 7)]))
        p.end()
        return QIcon(pix)

    @staticmethod
    def create_gear(size: int = 24) -> QIcon:
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor("#60a5fa"), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)

        cx, cy = size / 2, size / 2
        r_inner = 3.5
        r_outer = 7.5
        p.drawEllipse(QPointF(cx, cy), r_inner, r_inner)

        # 6 teeth
        for i in range(6):
            angle = i * (math.pi / 3)
            x1 = cx + r_inner * math.cos(angle)
            y1 = cy + r_inner * math.sin(angle)
            x2 = cx + r_outer * math.cos(angle)
            y2 = cy + r_outer * math.sin(angle)
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        p.end()
        return QIcon(pix)

    @staticmethod
    def create_folder(size: int = 16) -> QIcon:
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor("#eab308"), 1))
        p.setBrush(QColor("#ca8a04"))
        p.drawRoundedRect(1, 4, 14, 10, 2, 2)
        p.drawRoundedRect(2, 2, 6, 4, 1, 1)
        p.end()
        return QIcon(pix)

    @staticmethod
    def create_status_dot(color: str, size: int = 12) -> QIcon:
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(1, 1, size - 2, size - 2)
        p.end()
        return QIcon(pix)


# ===========================================================================
# qBittorrent In-Cell Progress Bar Delegate
# ===========================================================================

class QBitProgressBarDelegate(QStyledItemDelegate):
    """
    Renders an authentic qBittorrent in-cell progress bar.
    Displays a smooth gradient bar with centered percentage text.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex):
        raw_val = index.data(Qt.ItemDataRole.UserRole)
        if raw_val is None:
            super().paint(painter, option, index)
            return

        pct = float(raw_val)  # 0.0 to 100.0

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = option.rect
        pad_x = 6
        pad_y = 4
        bar_rect = QRectF(rect.x() + pad_x, rect.y() + pad_y,
                          rect.width() - 2 * pad_x, rect.height() - 2 * pad_y)

        # Background groove
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)
        groove_color = QColor("#2a2d37") if is_selected else QColor("#1e212b")
        painter.setBrush(groove_color)
        painter.setPen(QPen(QColor("#353949"), 1))
        painter.drawRoundedRect(bar_rect, 3, 3)

        # Bar fill
        if pct > 0:
            fill_w = max(4.0, (bar_rect.width() - 2) * (pct / 100.0))
            fill_rect = QRectF(bar_rect.x() + 1, bar_rect.y() + 1, fill_w, bar_rect.height() - 2)

            # Gradient: blue for downloading, green for 100% complete
            grad = QLinearGradient(fill_rect.topLeft(), fill_rect.bottomLeft())
            if pct >= 100.0:
                grad.setColorAt(0.0, QColor("#34d399"))
                grad.setColorAt(1.0, QColor("#059669"))
            else:
                grad.setColorAt(0.0, QColor("#38bdf8"))
                grad.setColorAt(1.0, QColor("#0284c7"))

            painter.setBrush(QBrush(grad))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(fill_rect, 2, 2)

        # Centered percentage label
        painter.setPen(QColor("#ffffff"))
        font = QFont("Segoe UI", 8, QFont.Weight.Bold)
        painter.setFont(font)
        text = f"{pct:.1f}%"
        painter.drawText(bar_rect, Qt.AlignmentFlag.AlignCenter, text)

        painter.restore()


# ===========================================================================
# qBittorrent Themes (Dark & Light Stylesheets)
# ===========================================================================

QBIT_DARK_THEME = """
/* ── qBittorrent Dark Theme ────────────────────────────────────────── */
QMainWindow, QWidget {
    background-color: #202020;
    color: #dfdfdf;
    font-family: "Segoe UI", Arial, sans-serif;
    font-size: 12px;
}

/* ── Menu Bar ──────────────────────────────────────────────────────── */
QMenuBar {
    background-color: #2b2b2b;
    border-bottom: 1px solid #383838;
    color: #dfdfdf;
    padding: 1px 2px;
}
QMenuBar::item {
    padding: 4px 10px;
    background: transparent;
    border-radius: 3px;
}
QMenuBar::item:selected {
    background-color: #3d3d3d;
}
QMenu {
    background-color: #2b2b2b;
    border: 1px solid #3d3d3d;
    padding: 4px 0;
    color: #dfdfdf;
}
QMenu::item {
    padding: 5px 28px 5px 20px;
}
QMenu::item:selected {
    background-color: #094771;
    color: #ffffff;
}
QMenu::separator {
    height: 1px;
    background-color: #383838;
    margin: 4px 6px;
}

/* ── Toolbar ───────────────────────────────────────────────────────── */
QToolBar {
    background-color: #2b2b2b;
    border-bottom: 1px solid #383838;
    spacing: 2px;
    padding: 3px 6px;
}
QToolButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 3px;
    padding: 4px 7px;
    color: #dfdfdf;
    font-size: 11px;
}
QToolButton:hover {
    background-color: #383838;
    border-color: #484848;
}
QToolButton:pressed {
    background-color: #1e1e1e;
}
QToolButton:disabled {
    opacity: 0.4;
}

/* ── Tables & Trees ────────────────────────────────────────────────── */
QTableWidget, QTreeWidget {
    background-color: #181818;
    alternate-background-color: #222222;
    gridline-color: #2b2b2b;
    border: 1px solid #383838;
    selection-background-color: #094771;
    selection-color: #ffffff;
    font-size: 11px;
    outline: none;
}
QTableWidget::item, QTreeWidget::item {
    padding: 3px 6px;
    height: 22px;
}
QTableWidget::item:selected, QTreeWidget::item:selected {
    background-color: #094771;
    color: #ffffff;
}

QHeaderView::section {
    background-color: #2b2b2b;
    color: #a8a8a8;
    font-weight: 600;
    font-size: 11px;
    padding: 4px 8px;
    border: none;
    border-right: 1px solid #383838;
    border-bottom: 1px solid #383838;
}
QHeaderView::section:hover {
    background-color: #353535;
    color: #ffffff;
}

/* ── Tabs ──────────────────────────────────────────────────────────── */
QTabWidget::pane {
    border: 1px solid #383838;
    background-color: #1e1e1e;
    top: -1px;
}
QTabBar::tab {
    background-color: #282828;
    color: #9e9e9e;
    padding: 6px 16px;
    border: 1px solid #383838;
    border-bottom: none;
    margin-right: 2px;
    font-weight: 600;
    font-size: 11px;
}
QTabBar::tab:selected {
    background-color: #1e1e1e;
    color: #ffffff;
    border-top: 2px solid #2196f3;
}
QTabBar::tab:hover:!selected {
    background-color: #323232;
    color: #cccccc;
}

/* ── Progress Bar ─────────────────────────────────────────────────── */
QProgressBar {
    background-color: #181818;
    border: 1px solid #383838;
    border-radius: 3px;
    text-align: center;
    color: #ffffff;
    font-size: 11px;
    font-weight: bold;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0284c7, stop:1 #38bdf8);
    border-radius: 2px;
}

/* ── Splitter ──────────────────────────────────────────────────────── */
QSplitter::handle {
    background-color: #383838;
}
QSplitter::handle:horizontal {
    width: 3px;
}
QSplitter::handle:vertical {
    height: 3px;
}
QSplitter::handle:hover {
    background-color: #2196f3;
}

/* ── Search / Inputs ───────────────────────────────────────────────── */
QLineEdit {
    background-color: #1e1e1e;
    border: 1px solid #3d3d3d;
    border-radius: 3px;
    padding: 4px 8px;
    color: #dfdfdf;
    font-size: 11px;
}
QLineEdit:focus {
    border-color: #2196f3;
}

/* ── Log View ──────────────────────────────────────────────────────── */
QPlainTextEdit {
    background-color: #141414;
    color: #b0b8c4;
    border: none;
    font-family: "Consolas", "Courier New", monospace;
    font-size: 11px;
}

/* ── Status Bar ────────────────────────────────────────────────────── */
QStatusBar {
    background-color: #2b2b2b;
    border-top: 1px solid #383838;
    color: #a0a0a0;
    font-size: 11px;
}
QStatusBar QLabel {
    padding: 0 8px;
    color: #b0b0b0;
}

/* ── Scrollbars ────────────────────────────────────────────────────── */
QScrollBar:vertical {
    background: #1e1e1e;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #3d3d3d;
    min-height: 24px;
    border-radius: 4px;
    margin: 1px;
}
QScrollBar::handle:vertical:hover {
    background: #505050;
}
QScrollBar:horizontal {
    background: #1e1e1e;
    height: 10px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background: #3d3d3d;
    min-width: 24px;
    border-radius: 4px;
    margin: 1px;
}
QScrollBar::handle:horizontal:hover {
    background: #505050;
}
QScrollBar::add-line, QScrollBar::sub-line {
    width: 0;
    height: 0;
}

/* ── Group Boxes ───────────────────────────────────────────────────── */
QGroupBox {
    border: 1px solid #383838;
    border-radius: 4px;
    margin-top: 8px;
    padding-top: 10px;
    font-weight: bold;
    color: #a0a0a0;
    font-size: 11px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
}
"""

QBIT_LIGHT_THEME = """
/* ── qBittorrent Classic Light Theme ───────────────────────────────── */
QMainWindow, QWidget {
    background-color: #f0f0f0;
    color: #202020;
    font-family: "Segoe UI", Arial, sans-serif;
    font-size: 12px;
}
QMenuBar {
    background-color: #f8f8f8;
    border-bottom: 1px solid #d0d0d0;
}
QTableWidget, QTreeWidget {
    background-color: #ffffff;
    alternate-background-color: #f7f9fa;
    border: 1px solid #cccccc;
    selection-background-color: #0078d7;
    selection-color: #ffffff;
}
QHeaderView::section {
    background-color: #e9e9e9;
    border: none;
    border-right: 1px solid #d0d0d0;
    border-bottom: 1px solid #d0d0d0;
    padding: 4px 8px;
    color: #404040;
    font-weight: 600;
}
QTabWidget::pane {
    border: 1px solid #cccccc;
    background-color: #ffffff;
}
QTabBar::tab {
    background-color: #e1e1e1;
    color: #555555;
    padding: 6px 16px;
    border: 1px solid #cccccc;
    border-bottom: none;
}
QTabBar::tab:selected {
    background-color: #ffffff;
    color: #000000;
    border-top: 2px solid #0078d7;
}
QStatusBar {
    background-color: #e8e8e8;
    border-top: 1px solid #cccccc;
}
"""


# ===========================================================================
# Pieces Visual Map Widget
# ===========================================================================

class PieceMapWidget(QWidget):
    """
    Renders the exact qBittorrent piece grid.
    Verified pieces are green, pending are dark/empty, hover shows tooltip.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.total = 0
        self.completed: set[int] = set()
        self.setMinimumHeight(140)
        self.setMouseTracking(True)
        self._hover_idx = -1

    def reset(self, total: int):
        self.total = total
        self.completed.clear()
        self.update()

    def set_pieces(self, total: int, completed: set[int]):
        self.total = total
        self.completed = set(completed)
        self.update()

    def mark_done(self, idx: int):
        self.completed.add(idx)
        self.update()

    def mouseMoveEvent(self, event):
        if self.total <= 0:
            return
        w = self.width() - 8
        h = self.height() - 8
        if w <= 0 or h <= 0:
            return
        cols = max(1, int(w / 7))
        rows = max(1, (self.total + cols - 1) // cols)
        cell_w = w / cols
        cell_h = h / rows
        gx = int((event.position().x() - 4) / cell_w)
        gy = int((event.position().y() - 4) / cell_h)
        idx = gy * cols + gx
        if 0 <= idx < self.total:
            if idx != self._hover_idx:
                self._hover_idx = idx
                st = "Downloaded ✓" if idx in self.completed else "Missing"
                self.setToolTip(f"Piece #{idx} — {st}")
        else:
            self._hover_idx = -1
            self.setToolTip("")

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        rect = self.rect()
        p.fillRect(rect, QColor("#14151b"))

        if self.total <= 0:
            p.setPen(QColor("#666666"))
            p.setFont(QFont("Segoe UI", 9))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "No pieces data available")
            return

        w = rect.width() - 8
        h = rect.height() - 8
        if w <= 0 or h <= 0:
            return

        cols = max(1, int(w / 6.5))
        rows = max(1, (self.total + cols - 1) // cols)
        cell_w = w / cols
        cell_h = h / rows
        if cell_h < 2:
            cell_h = 2
            rows = int(h / cell_h)
            cols = max(1, (self.total + rows - 1) // rows)
            cell_w = w / cols

        gap = max(0.5, min(cell_w, cell_h) * 0.1)
        brush_done = QBrush(QColor("#22c55e"))
        brush_empty = QBrush(QColor("#232533"))
        p.setPen(Qt.PenStyle.NoPen)

        idx = 0
        y = 4.0
        for _ in range(rows):
            x = 4.0
            for _ in range(cols):
                if idx >= self.total:
                    break
                p.setBrush(brush_done if idx in self.completed else brush_empty)
                p.drawRect(QRectF(x, y, cell_w - gap, cell_h - gap))
                x += cell_w
                idx += 1
            y += cell_h
            if idx >= self.total:
                break


# ===========================================================================
# Live Speed Chart (qBittorrent Speed Tab)
# ===========================================================================

class SpeedGraphWidget(QWidget):
    """Real-time Download / Upload rolling speed chart with scale and legend."""

    def __init__(self, parent=None, max_points: int = 100):
        super().__init__(parent)
        self.max_points = max_points
        self.dl_data = collections.deque(maxlen=max_points)
        self.ul_data = collections.deque(maxlen=max_points)
        self.setMinimumHeight(150)

    def add_sample(self, dl: float, ul: float = 0.0):
        self.dl_data.append(dl)
        self.ul_data.append(ul)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = self.height()

        p.fillRect(self.rect(), QColor("#14151a"))

        pad_l, pad_r, pad_t, pad_b = 50, 16, 16, 26
        cw = w - pad_l - pad_r
        ch = h - pad_t - pad_b

        # Grid lines
        p.setPen(QPen(QColor("#242735"), 1, Qt.PenStyle.DashLine))
        for i in range(5):
            y = pad_t + int(ch * i / 4)
            p.drawLine(pad_l, y, w - pad_r, y)

        max_val = 1024.0 * 100  # min scale 100 KB/s
        if self.dl_data:
            max_val = max(max_val, max(self.dl_data) * 1.2)
        if self.ul_data:
            max_val = max(max_val, max(self.ul_data) * 1.2)

        # Y-axis labels
        p.setPen(QColor("#7e859b"))
        p.setFont(QFont("Segoe UI", 8))
        for i in range(5):
            y = pad_t + int(ch * i / 4)
            val = max_val * (1.0 - i / 4)
            p.drawText(0, y - 6, pad_l - 6, 14, Qt.AlignmentFlag.AlignRight, fmt_speed(val))

        # Time axis label
        p.drawText(pad_l, h - 18, cw, 14, Qt.AlignmentFlag.AlignRight, "Time (seconds)")

        # Legend (Top right)
        p.setBrush(QColor("#22c55e"))
        p.drawRect(w - 220, 4, 10, 10)
        p.drawText(w - 205, 13, "Download")

        p.setBrush(QColor("#38bdf8"))
        p.drawRect(w - 120, 4, 10, 10)
        p.drawText(w - 105, 13, "Upload")

        # Plot DL
        if len(self.dl_data) >= 2:
            self._draw_stream(p, list(self.dl_data), max_val, pad_l, pad_t, cw, ch,
                              QColor("#22c55e"), QColor(34, 197, 94, 40))

        # Plot UL
        if len(self.ul_data) >= 2:
            self._draw_stream(p, list(self.ul_data), max_val, pad_l, pad_t, cw, ch,
                              QColor("#38bdf8"), QColor(56, 189, 248, 30))

    def _draw_stream(self, p: QPainter, pts: list[float], max_val: float,
                     pad_l, pad_t, cw, ch, line_color: QColor, fill_color: QColor):
        n = len(pts)
        step_x = cw / (self.max_points - 1)
        start_x = pad_l + (self.max_points - n) * step_x

        from PySide6.QtGui import QPainterPath
        line_path = QPainterPath()
        fill_path = QPainterPath()

        for i, val in enumerate(pts):
            x = start_x + i * step_x
            y = pad_t + ch * (1.0 - (val / max_val))
            if i == 0:
                line_path.moveTo(x, y)
                fill_path.moveTo(x, pad_t + ch)
                fill_path.lineTo(x, y)
            else:
                line_path.lineTo(x, y)
                fill_path.lineTo(x, y)

        fill_path.lineTo(start_x + (n - 1) * step_x, pad_t + ch)
        fill_path.closeSubpath()

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill_color)
        p.drawPath(fill_path)

        p.setPen(QPen(line_color, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(line_path)


# ===========================================================================
# Torrent Worker Thread
# ===========================================================================

class TorrentWorker(QThread):
    """Asynchronous worker managing tracker scraping and swarm streaming."""

    sig_log = Signal(str, str)             # level, message
    sig_metadata = Signal(dict)
    sig_peers_count = Signal(int)
    sig_peer_status = Signal(str, int, str)
    sig_tracker_status = Signal(str, str, int)
    sig_piece_done = Signal(int, int, int, int, float)  # idx, done_cnt, total, bytes_done, speed
    sig_finished = Signal(bool, str)

    def __init__(self, torrent_path: str, download_dir: str, max_workers: int = 30):
        super().__init__()
        self.torrent_path = torrent_path
        self.download_dir = download_dir
        self.max_workers = max_workers
        self.downloader: SwarmDownloader | None = None
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True
        if self.downloader:
            self.downloader.stop()

    def run(self):
        try:
            self.sig_log.emit("Normal", f"Parsing torrent file: {os.path.basename(self.torrent_path)}")
            summary = get_torrent_summary(self.torrent_path)
            self.sig_metadata.emit(summary)

            peer_id = generate_peer_id()
            trackers = get_all_trackers(summary)
            self.sig_log.emit("Normal", f"Querying {len(trackers)} tracker(s) …")

            from concurrent.futures import ThreadPoolExecutor
            peers: list[tuple[str, int]] = []
            peers_lock = threading.Lock()

            def _query_tracker(url: str):
                if self._stop_requested:
                    return
                try:
                    self.sig_tracker_status.emit(url, "Updating", 0)
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
                        self.sig_tracker_status.emit(url, "Working", len(found))
                        self.sig_log.emit("Normal", f"Tracker responded: {url} ({len(found)} peers)")
                    else:
                        self.sig_tracker_status.emit(url, "Working (0 peers)", 0)
                except Exception as exc:
                    self.sig_tracker_status.emit(url, f"Error: {type(exc).__name__}", 0)

            with ThreadPoolExecutor(max_workers=min(12, len(trackers) or 1)) as pool:
                list(pool.map(_query_tracker, trackers))

            self.sig_peers_count.emit(len(peers))
            self.sig_log.emit("Normal", f"Discovered {len(peers)} unique peer addresses in swarm")

            if not peers:
                self.sig_finished.emit(False, "No peers discovered from trackers.")
                return

            if self._stop_requested:
                return

            os.makedirs(self.download_dir, exist_ok=True)

            self.downloader = SwarmDownloader(
                summary=summary,
                peers=peers,
                peer_id=peer_id,
                download_dir=self.download_dir,
                max_workers=self.max_workers,
                on_piece_done=lambda idx, done_cnt, total, b_done, t_len, spd:
                    self.sig_piece_done.emit(idx, done_cnt, total, b_done, spd),
                on_log=lambda msg: self.sig_log.emit("Normal", msg),
                on_peer_status=lambda ip, port, st: self.sig_peer_status.emit(ip, port, st),
            )

            # Signal verified resume pieces immediately
            if self.downloader.done:
                self.sig_log.emit(
                    "Normal",
                    f"Resumed {len(self.downloader.done)}/{self.downloader.total_pieces} verified pieces from disk"
                )

            success = self.downloader.download()

            if self._stop_requested:
                self.sig_finished.emit(False, "Download paused by user.")
            elif success:
                self.sig_finished.emit(True, "All pieces verified successfully.")
            else:
                done = len(self.downloader.done)
                total = self.downloader.total_pieces
                self.sig_finished.emit(False, f"Download halted: {done}/{total} pieces verified.")

        except Exception as exc:
            self.sig_log.emit("Critical", f"Worker error: {exc}")
            self.sig_finished.emit(False, str(exc))


# ===========================================================================
# Torrent Item Data Model
# ===========================================================================

class TorrentItem:
    """Encapsulates all metadata, progress, and workers for a single torrent."""

    def __init__(self, torrent_path: str, save_dir: str):
        self.torrent_path = torrent_path
        self.save_dir = save_dir
        self.summary = get_torrent_summary(torrent_path)

        self.name: str = self.summary["name"]
        self.info_hash: str = self.summary["info_hash_hex"]
        self.total_length: int = self.summary["total_length"]
        self.piece_length: int = self.summary["piece_length"]
        self.total_pieces: int = len(self.summary["piece_hashes"])
        self.files: list[dict] = self.summary.get("files", [])
        self.trackers: list[str] = self.summary.get("announce_list") or [self.summary["announce"]]

        # State
        self.status = "Paused"  # Downloading, Paused, Completed, Checking, Stalled
        self.category = "Uncategorized"
        self.added_on = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.completed_on = "—"
        self.priority = 1

        self.done_pieces: set[int] = set()
        self.bytes_done: int = 0
        self.dl_speed: float = 0.0
        self.ul_speed: float = 0.0
        self.peers_connected: int = 0
        self.peers_total: int = 0
        self.seeds_connected: int = 0
        self.seeds_total: int = 0
        self.eta: float = 0.0

        self.peer_map: dict[str, dict] = {}       # ip:port -> {ip, port, status, speed}
        self.tracker_map: dict[str, dict] = {}    # url -> {tier, status, peers}
        self.worker: TorrentWorker | None = None

        # Check existing data on disk immediately
        self.recheck_existing()

    @property
    def progress_pct(self) -> float:
        if self.total_length <= 0:
            return 0.0
        return min(100.0, (self.bytes_done / self.total_length) * 100.0)

    def recheck_existing(self):
        """Scans disk to verify already downloaded pieces."""
        try:
            dm = DiskManager(
                download_dir=self.save_dir,
                files_layout=self.files or [{"path": self.name, "length": self.total_length}],
                piece_length=self.piece_length,
                total_length=self.total_length,
            )
            v_pieces, v_bytes = dm.check_existing_pieces(self.summary["piece_hashes"])
            dm.close()
            self.done_pieces = set(v_pieces)
            self.bytes_done = v_bytes
            if len(self.done_pieces) == self.total_pieces and self.total_pieces > 0:
                self.status = "Completed"
                self.completed_on = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass


# ===========================================================================
# Dialogs: Add Torrent, Preferences, About
# ===========================================================================

class AddTorrentDialog(QDialog):
    """Authentic qBittorrent 'Add New Torrent' modal dialog."""

    def __init__(self, summary: dict, default_save_dir: str, parent=None):
        super().__init__(parent)
        self.summary = summary
        self.setWindowTitle("Add New Torrent")
        self.resize(600, 480)
        self.setWindowIcon(IconFactory.create_qb_logo(32))

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Header Info Box
        header_box = QGroupBox("Torrent Information")
        hb_layout = QGridLayout(header_box)
        hb_layout.addWidget(QLabel("Name:"), 0, 0)
        hb_layout.addWidget(QLabel(f"<b>{summary['name']}</b>"), 0, 1)
        hb_layout.addWidget(QLabel("Total Size:"), 1, 0)
        hb_layout.addWidget(QLabel(fmt_size(summary["total_length"])), 1, 1)
        hb_layout.addWidget(QLabel("Pieces:"), 2, 0)
        hb_layout.addWidget(QLabel(f"{len(summary['piece_hashes'])} × {fmt_size(summary['piece_length'])}"), 2, 1)
        layout.addWidget(header_box)

        # Save Location Box
        save_box = QGroupBox("Save at")
        sb_layout = QHBoxLayout(save_box)
        self.path_edit = QLineEdit(default_save_dir)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse)
        sb_layout.addWidget(self.path_edit)
        sb_layout.addWidget(browse_btn)
        layout.addWidget(save_box)

        # Category and Options
        opt_box = QGroupBox("Torrent Options")
        ob_layout = QGridLayout(opt_box)
        ob_layout.addWidget(QLabel("Category:"), 0, 0)
        self.cat_combo = QComboBox()
        self.cat_combo.addItems(["Uncategorized", "Movies", "TV Shows", "Music", "Software", "Games"])
        ob_layout.addWidget(self.cat_combo, 0, 1)

        self.chk_start = QCheckBox("Start torrent")
        self.chk_start.setChecked(True)
        ob_layout.addWidget(self.chk_start, 1, 0)

        self.chk_skip_check = QCheckBox("Skip hash check")
        ob_layout.addWidget(self.chk_skip_check, 1, 1)
        layout.addWidget(opt_box)

        # File List preview
        layout.addWidget(QLabel("Content:"))
        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderLabels(["File Name", "Size"])
        self.file_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.file_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)

        files = summary.get("files") or [{"path": summary["name"], "length": summary["total_length"]}]
        for f in files:
            item = QTreeWidgetItem([f["path"], fmt_size(f["length"])])
            item.setCheckState(0, Qt.CheckState.Checked)
            self.file_tree.addTopLevelItem(item)
        layout.addWidget(self.file_tree)

        # Buttons
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Select Download Directory", self.path_edit.text())
        if d:
            self.path_edit.setText(d)

    def get_settings(self):
        return {
            "save_dir": self.path_edit.text(),
            "category": self.cat_combo.currentText(),
            "start_now": self.chk_start.isChecked(),
        }


class PreferencesDialog(QDialog):
    """Authentic qBittorrent Options / Preferences Dialog."""

    def __init__(self, current_save_dir: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Options")
        self.resize(650, 480)
        self.setWindowIcon(IconFactory.create_gear(32))

        main_layout = QHBoxLayout(self)

        # Left Category List
        self.cat_list = QListWidget()
        self.cat_list.setMaximumWidth(140)
        categories = ["Downloads", "Connection", "Speed", "BitTorrent", "Advanced"]
        for cat in categories:
            item = QListWidgetItem(cat)
            item.setSizeHint(QSize(120, 36))
            self.cat_list.addItem(item)
        self.cat_list.setCurrentRow(0)
        main_layout.addWidget(self.cat_list)

        # Right Stacked Pages
        self.pages = QStackedWidget()

        # Page 1: Downloads
        p_dl = QWidget()
        dl_layout = QVBoxLayout(p_dl)
        gb_save = QGroupBox("Saving Management")
        fl = QFormLayout(gb_save)
        self.dir_input = QLineEdit(current_save_dir)
        b_btn = QPushButton("Browse…")
        b_btn.clicked.connect(self._pick_dir)
        h = QHBoxLayout()
        h.addWidget(self.dir_input)
        h.addWidget(b_btn)
        fl.addRow("Default Torrent Management Mode:", QComboBox())
        fl.addRow("Default Save Path:", h)
        dl_layout.addWidget(gb_save)
        dl_layout.addStretch()
        self.pages.addWidget(p_dl)

        # Page 2: Connection
        p_conn = QWidget()
        c_layout = QVBoxLayout(p_conn)
        gb_conn = QGroupBox("Listening Port & Connection Limits")
        c_fl = QFormLayout(gb_conn)
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(6881)
        self.max_conn_spin = QSpinBox()
        self.max_conn_spin.setRange(10, 2000)
        self.max_conn_spin.setValue(500)
        c_fl.addRow("Port for incoming connections:", self.port_spin)
        c_fl.addRow("Global maximum connections:", self.max_conn_spin)
        c_layout.addWidget(gb_conn)
        c_layout.addStretch()
        self.pages.addWidget(p_conn)

        # Page 3: Speed
        p_spd = QWidget()
        s_layout = QVBoxLayout(p_spd)
        gb_spd = QGroupBox("Global Rate Limits")
        s_fl = QFormLayout(gb_spd)
        s_fl.addRow("Download (KiB/s, 0 = ∞):", QSpinBox())
        s_fl.addRow("Upload (KiB/s, 0 = ∞):", QSpinBox())
        s_layout.addWidget(gb_spd)
        s_layout.addStretch()
        self.pages.addWidget(p_spd)

        # Page 4: BitTorrent
        p_bt = QWidget()
        bt_layout = QVBoxLayout(p_bt)
        gb_bt = QGroupBox("Privacy & DHT")
        bt_fl = QVBoxLayout(gb_bt)
        bt_fl.addWidget(QCheckBox("Enable DHT (Decentralized Network)"))
        bt_fl.addWidget(QCheckBox("Enable Peer Exchange (PeX)"))
        bt_fl.addWidget(QCheckBox("Enable Local Peer Discovery (LPD)"))
        bt_fl.addWidget(QCheckBox("Require encryption"))
        bt_layout.addWidget(gb_bt)
        bt_layout.addStretch()
        self.pages.addWidget(p_bt)

        # Page 5: Advanced
        p_adv = QWidget()
        adv_layout = QVBoxLayout(p_adv)
        gb_adv = QGroupBox("Disk Cache")
        adv_fl = QFormLayout(gb_adv)
        cache_spin = QSpinBox()
        cache_spin.setRange(16, 4096)
        cache_spin.setValue(512)
        cache_spin.setSuffix(" MiB")
        adv_fl.addRow("Physical memory (RAM) cache:", cache_spin)
        adv_layout.addWidget(gb_adv)
        adv_layout.addStretch()
        self.pages.addWidget(p_adv)

        self.cat_list.currentRowChanged.connect(self.pages.setCurrentIndex)

        # Container for right page + buttons
        right_container = QVBoxLayout()
        right_container.addWidget(self.pages)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        right_container.addWidget(btns)

        main_layout.addLayout(right_container, 1)

    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Default Save Directory", self.dir_input.text())
        if d:
            self.dir_input.setText(d)

    def get_save_dir(self) -> str:
        return self.dir_input.text()


class AboutQBitDialog(QDialog):
    """Authentic qBittorrent About dialog with tabs and logos."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About qBittorrent")
        self.resize(520, 380)
        self.setWindowIcon(IconFactory.create_qb_logo(32))

        layout = QVBoxLayout(self)

        # Header with Logo
        head = QHBoxLayout()
        logo_lbl = QLabel()
        logo_lbl.setPixmap(IconFactory.create_qb_logo(54).pixmap(54, 54))
        head.addWidget(logo_lbl)

        info = QVBoxLayout()
        title = QLabel("<h2>qBittorrent v4.6.5 (64-bit)</h2>")
        sub = QLabel("An advanced BitTorrent client in Python & PySide6")
        info.addWidget(title)
        info.addWidget(sub)
        head.addLayout(info)
        head.addStretch()
        layout.addLayout(head)

        # Tabs
        tabs = QTabWidget()
        t_about = QWidget()
        al = QVBoxLayout(t_about)
        al.addWidget(QLabel(
            "<b>qBittorrent is an open-source BitTorrent client.</b><br><br>"
            "This project reproduces the exact qBittorrent desktop interface, "
            "complete with full multi-peer swarm streaming, rarest-first piece "
            "selection, persistent resume scanning, and real-time visual piece mapping."
        ))
        al.addStretch()
        tabs.addTab(t_about, "About")

        t_thanks = QWidget()
        tl = QVBoxLayout(t_thanks)
        tl.addWidget(QLabel(
            "Special thanks to the qBittorrent and libtorrent community.<br>"
            "Powered by PySide6 (Qt for Python)."
        ))
        tl.addStretch()
        tabs.addTab(t_thanks, "Thanks")

        t_license = QWidget()
        ll = QVBoxLayout(t_license)
        ll.addWidget(QLabel("GNU General Public License v2 or later."))
        ll.addStretch()
        tabs.addTab(t_license, "License")

        layout.addWidget(tabs)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.accept)
        layout.addWidget(btns)


# ===========================================================================
# Main Application Window: qBittorrent Exact Clone
# ===========================================================================

class QBitMainWindow(QMainWindow):
    """Complete, authentic qBittorrent desktop client."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("qBittorrent v4.6.5")
        self.setWindowIcon(IconFactory.create_qb_logo(64))
        self.resize(1180, 780)

        # Storage & State
        self.torrents: list[TorrentItem] = []
        self.selected_torrent: TorrentItem | None = None
        self.default_save_dir: str = os.path.join(os.getcwd(), "downloads")
        self.is_dark_theme: bool = True
        self.sidebar_filter: str = "All"
        self.search_filter: str = ""

        # Global stats timer
        self.stats_timer = QTimer(self)
        self.stats_timer.setInterval(1000)
        self.stats_timer.timeout.connect(self._on_stats_tick)

        # Build UI
        self.setStyleSheet(QBIT_DARK_THEME)
        self._build_menu_bar()
        self._build_toolbar()
        self._build_central_view()
        self._build_status_bar()

        # Start timer
        self.stats_timer.start()

        # Load existing torrents in directory if available
        self._auto_load_workspace_torrents()

    # -----------------------------------------------------------------------
    # Menu Bar
    # -----------------------------------------------------------------------

    def _build_menu_bar(self):
        mb = self.menuBar()

        # File
        m_file = mb.addMenu("&File")
        self.act_add_file = QAction(IconFactory.create_add_file(18), "Add &Torrent File…", self)
        self.act_add_file.setShortcut(QKeySequence("Ctrl+O"))
        self.act_add_file.triggered.connect(self._on_add_torrent_file)
        m_file.addAction(self.act_add_file)

        self.act_add_link = QAction(IconFactory.create_add_link(18), "Add Torrent &Link…", self)
        self.act_add_link.setShortcut(QKeySequence("Ctrl+L"))
        self.act_add_link.triggered.connect(self._on_add_torrent_link)
        m_file.addAction(self.act_add_link)

        m_file.addSeparator()
        self.act_exit = QAction("E&xit", self)
        self.act_exit.setShortcut(QKeySequence("Ctrl+Q"))
        self.act_exit.triggered.connect(self.close)
        m_file.addAction(self.act_exit)

        # Edit
        m_edit = mb.addMenu("&Edit")
        self.act_resume = QAction(IconFactory.create_resume(18), "&Resume", self)
        self.act_resume.setShortcut(QKeySequence("Ctrl+R"))
        self.act_resume.triggered.connect(self._on_resume_selected)
        m_edit.addAction(self.act_resume)

        self.act_pause = QAction(IconFactory.create_pause(18), "&Pause", self)
        self.act_pause.setShortcut(QKeySequence("Ctrl+P"))
        self.act_pause.triggered.connect(self._on_pause_selected)
        m_edit.addAction(self.act_pause)

        m_edit.addSeparator()
        self.act_resume_all = QAction("Resume &All", self)
        self.act_resume_all.triggered.connect(self._on_resume_all)
        m_edit.addAction(self.act_resume_all)

        self.act_pause_all = QAction("Pause A&ll", self)
        self.act_pause_all.triggered.connect(self._on_pause_all)
        m_edit.addAction(self.act_pause_all)

        m_edit.addSeparator()
        self.act_delete = QAction(IconFactory.create_delete(18), "&Delete", self)
        self.act_delete.setShortcut(QKeySequence("Delete"))
        self.act_delete.triggered.connect(self._on_delete_selected)
        m_edit.addAction(self.act_delete)

        # View
        m_view = mb.addMenu("&View")
        self.act_toggle_sidebar = QAction("Category &Sidebar", self, checkable=True)
        self.act_toggle_sidebar.setChecked(True)
        self.act_toggle_sidebar.toggled.connect(lambda chk: self.sidebar.setVisible(chk))
        m_view.addAction(self.act_toggle_sidebar)

        self.act_toggle_details = QAction("&Detailed Info Panel", self, checkable=True)
        self.act_toggle_details.setChecked(True)
        self.act_toggle_details.toggled.connect(lambda chk: self.detail_tabs.setVisible(chk))
        m_view.addAction(self.act_toggle_details)

        m_view.addSeparator()
        self.act_theme_dark = QAction("&Dark Theme", self, checkable=True)
        self.act_theme_dark.setChecked(True)
        self.act_theme_dark.triggered.connect(self._toggle_theme)
        m_view.addAction(self.act_theme_dark)

        # Tools
        m_tools = mb.addMenu("&Tools")
        self.act_options = QAction(IconFactory.create_gear(18), "&Options…", self)
        self.act_options.setShortcut(QKeySequence("Alt+O"))
        self.act_options.triggered.connect(self._on_show_options)
        m_tools.addAction(self.act_options)

        # Help
        m_help = mb.addMenu("&Help")
        self.act_about = QAction("About &qBittorrent", self)
        self.act_about.triggered.connect(lambda: AboutQBitDialog(self).exec())
        m_help.addAction(self.act_about)

    # -----------------------------------------------------------------------
    # Toolbar
    # -----------------------------------------------------------------------

    def _build_toolbar(self):
        tb = QToolBar("Main Controls")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        self.addToolBar(tb)

        tb.addAction(self.act_add_link)
        tb.addAction(self.act_add_file)
        tb.addAction(self.act_delete)
        tb.addSeparator()
        tb.addAction(self.act_resume)
        tb.addAction(self.act_pause)
        tb.addSeparator()

        act_top = tb.addAction(IconFactory.create_arrow(20, "up", double=True), "Top Priority")
        act_up = tb.addAction(IconFactory.create_arrow(20, "up", double=False), "Increase Priority")
        act_down = tb.addAction(IconFactory.create_arrow(20, "down", double=False), "Decrease Priority")
        act_bot = tb.addAction(IconFactory.create_arrow(20, "down", double=True), "Bottom Priority")
        act_top.triggered.connect(lambda: self._move_priority("top"))
        act_up.triggered.connect(lambda: self._move_priority("up"))
        act_down.triggered.connect(lambda: self._move_priority("down"))
        act_bot.triggered.connect(lambda: self._move_priority("bot"))

        tb.addSeparator()
        tb.addAction(self.act_options)

        # Spacer and search filter
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter torrents…")
        self.filter_edit.setMaximumWidth(220)
        self.filter_edit.textChanged.connect(self._on_filter_changed)
        tb.addWidget(self.filter_edit)

    # -----------------------------------------------------------------------
    # Central Splitters (Sidebar + Transfer Table + Bottom Details)
    # -----------------------------------------------------------------------

    def _build_central_view(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_h = QHBoxLayout(central)
        main_h.setContentsMargins(0, 0, 0, 0)
        main_h.setSpacing(0)

        # Horizontal Splitter: Left Sidebar | Right Area
        self.h_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.h_splitter.setHandleWidth(3)

        # ── Left Sidebar (Categories & Status Filter Tree) ────────────────
        self.sidebar = QTreeWidget()
        self.sidebar.setHeaderHidden(True)
        self.sidebar.setMinimumWidth(160)
        self.sidebar.setMaximumWidth(320)
        self._populate_sidebar_tree()
        self.sidebar.itemClicked.connect(self._on_sidebar_item_clicked)
        self.h_splitter.addWidget(self.sidebar)

        # ── Right Area: Vertical Splitter (Transfer Table | Detail Tabs) ──
        self.v_splitter = QSplitter(Qt.Orientation.Vertical)
        self.v_splitter.setHandleWidth(3)

        # Transfer Table
        self.transfer_table = QTableWidget(0, 14)
        self.transfer_table.setHorizontalHeaderLabels([
            "#", "Name", "Size", "Done", "Status", "Progress",
            "Seeds", "Peers", "Down Speed", "Up Speed", "ETA",
            "Ratio", "Added On", "Completed On"
        ])
        self.transfer_table.setAlternatingRowColors(True)
        self.transfer_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.transfer_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.transfer_table.verticalHeader().setVisible(False)
        self.transfer_table.setShowGrid(False)

        # In-cell progress delegate on column 5
        self.transfer_table.setItemDelegateForColumn(5, QBitProgressBarDelegate(self.transfer_table))

        hdr = self.transfer_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.transfer_table.setColumnWidth(0, 32)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.transfer_table.setColumnWidth(1, 350)
        for col in [2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13]:
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.transfer_table.setColumnWidth(5, 130)

        self.transfer_table.itemSelectionChanged.connect(self._on_table_selection_changed)
        self.transfer_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.transfer_table.customContextMenuRequested.connect(self._show_table_context_menu)
        self.transfer_table.doubleClicked.connect(self._on_table_double_clicked)

        self.v_splitter.addWidget(self.transfer_table)

        # Bottom Detail Tabs
        self.detail_tabs = QTabWidget()
        self._build_detail_tabs()
        self.v_splitter.addWidget(self.detail_tabs)

        self.v_splitter.setStretchFactor(0, 3)
        self.v_splitter.setStretchFactor(1, 2)

        self.h_splitter.addWidget(self.v_splitter)
        self.h_splitter.setStretchFactor(0, 1)
        self.h_splitter.setStretchFactor(1, 5)

        main_h.addWidget(self.h_splitter)

    # -----------------------------------------------------------------------
    # Sidebar Filter Tree
    # -----------------------------------------------------------------------

    def _populate_sidebar_tree(self):
        self.sidebar.clear()

        # Status Root
        self.root_status = QTreeWidgetItem(["Status"])
        self.root_status.setExpanded(True)
        self.item_all = QTreeWidgetItem([f"All (0)"])
        self.item_all.setIcon(0, IconFactory.create_status_dot("#38bdf8"))
        self.item_all.setData(0, Qt.ItemDataRole.UserRole, "All")

        self.item_dl = QTreeWidgetItem([f"Downloading (0)"])
        self.item_dl.setIcon(0, IconFactory.create_status_dot("#22c55e"))
        self.item_dl.setData(0, Qt.ItemDataRole.UserRole, "Downloading")

        self.item_seed = QTreeWidgetItem([f"Seeding (0)"])
        self.item_seed.setIcon(0, IconFactory.create_status_dot("#818cf8"))
        self.item_seed.setData(0, Qt.ItemDataRole.UserRole, "Seeding")

        self.item_done = QTreeWidgetItem([f"Completed (0)"])
        self.item_done.setIcon(0, IconFactory.create_status_dot("#10b981"))
        self.item_done.setData(0, Qt.ItemDataRole.UserRole, "Completed")

        self.item_paused = QTreeWidgetItem([f"Paused (0)"])
        self.item_paused.setIcon(0, IconFactory.create_status_dot("#f59e0b"))
        self.item_paused.setData(0, Qt.ItemDataRole.UserRole, "Paused")

        self.root_status.addChildren([self.item_all, self.item_dl, self.item_seed, self.item_done, self.item_paused])
        self.sidebar.addTopLevelItem(self.root_status)

        # Categories Root
        self.root_categories = QTreeWidgetItem(["Categories"])
        self.root_categories.setExpanded(True)
        self.item_cat_all = QTreeWidgetItem(["All (0)"])
        self.item_cat_all.setIcon(0, IconFactory.create_folder())
        self.item_cat_all.setData(0, Qt.ItemDataRole.UserRole, "Cat:All")
        self.item_cat_uncat = QTreeWidgetItem(["Uncategorized (0)"])
        self.item_cat_uncat.setIcon(0, IconFactory.create_folder())
        self.item_cat_uncat.setData(0, Qt.ItemDataRole.UserRole, "Cat:Uncategorized")
        self.root_categories.addChildren([self.item_cat_all, self.item_cat_uncat])
        self.sidebar.addTopLevelItem(self.root_categories)

        # Trackers Root
        self.root_trackers = QTreeWidgetItem(["Trackers"])
        self.root_trackers.setExpanded(True)
        self.item_tr_all = QTreeWidgetItem(["All (0)"])
        self.item_tr_all.setIcon(0, IconFactory.create_status_dot("#64748b"))
        self.item_tr_all.setData(0, Qt.ItemDataRole.UserRole, "Tr:All")
        self.root_trackers.addChild(self.item_tr_all)
        self.sidebar.addTopLevelItem(self.root_trackers)

        self.sidebar.setCurrentItem(self.item_all)

    def _update_sidebar_counts(self):
        total = len(self.torrents)
        dl = sum(1 for t in self.torrents if t.status == "Downloading")
        seed = sum(1 for t in self.torrents if t.status == "Seeding")
        comp = sum(1 for t in self.torrents if t.status == "Completed")
        paused = sum(1 for t in self.torrents if t.status == "Paused")

        self.item_all.setText(0, f"All ({total})")
        self.item_dl.setText(0, f"Downloading ({dl})")
        self.item_seed.setText(0, f"Seeding ({seed})")
        self.item_done.setText(0, f"Completed ({comp})")
        self.item_paused.setText(0, f"Paused ({paused})")
        self.item_cat_all.setText(0, f"All ({total})")
        self.item_cat_uncat.setText(0, f"Uncategorized ({total})")
        self.item_tr_all.setText(0, f"All ({total})")

    # -----------------------------------------------------------------------
    # Detail Tabs (Bottom Panel)
    # -----------------------------------------------------------------------

    def _build_detail_tabs(self):
        # 1. General Tab
        self.tab_general = self._build_general_tab()
        self.detail_tabs.addTab(self.tab_general, "General")

        # 2. Trackers Tab
        self.tab_trackers = QTableWidget(0, 8)
        self.tab_trackers.setHorizontalHeaderLabels([
            "Tier", "URL", "Status", "Peers", "Seeds", "Leeches", "Downloaded", "Message"
        ])
        self.tab_trackers.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tab_trackers.verticalHeader().setVisible(False)
        self.tab_trackers.setAlternatingRowColors(True)
        self.detail_tabs.addTab(self.tab_trackers, "Trackers")

        # 3. Peers Tab
        self.tab_peers = QTableWidget(0, 10)
        self.tab_peers.setHorizontalHeaderLabels([
            "IP", "Port", "Client", "Flags", "%", "Down Speed", "Up Speed", "Downloaded", "Uploaded", "Relevance"
        ])
        self.tab_peers.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tab_peers.verticalHeader().setVisible(False)
        self.tab_peers.setAlternatingRowColors(True)
        self.detail_tabs.addTab(self.tab_peers, "Peers")

        # 4. Pieces Tab (Piece Map)
        self.piece_map = PieceMapWidget()
        self.detail_tabs.addTab(self.piece_map, "Pieces")

        # 5. Content (Files) Tab
        self.tab_content = QTreeWidget()
        self.tab_content.setHeaderLabels(["Name", "Size", "Progress", "Priority", "Remaining"])
        self.tab_content.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.detail_tabs.addTab(self.tab_content, "Content")

        # 6. Speed (Live Graph) Tab
        self.speed_graph = SpeedGraphWidget()
        self.detail_tabs.addTab(self.speed_graph, "Speed")

        # 7. Execution Log Tab
        self.tab_log = QPlainTextEdit()
        self.tab_log.setReadOnly(True)
        self.tab_log.setMaximumBlockCount(3000)
        self.detail_tabs.addTab(self.tab_log, "Execution Log")

    def _build_general_tab(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(10)

        # Top Progress Bar
        self.gen_progress = QProgressBar()
        self.gen_progress.setRange(0, 1000)
        self.gen_progress.setValue(0)
        self.gen_progress.setTextVisible(True)
        self.gen_progress.setFixedHeight(20)
        outer.addWidget(self.gen_progress)

        split = QHBoxLayout()

        # Group 1: Transfer
        gb_transfer = QGroupBox("Transfer")
        fl_trans = QGridLayout(gb_transfer)
        fl_trans.setHorizontalSpacing(16)
        fl_trans.setVerticalSpacing(4)

        self.lbl_elapsed = QLabel("—")
        self.lbl_eta = QLabel("—")
        self.lbl_dl_spd = QLabel("0 B/s")
        self.lbl_ul_spd = QLabel("0 B/s")
        self.lbl_downloaded = QLabel("0 B")
        self.lbl_uploaded = QLabel("0 B")
        self.lbl_seeds = QLabel("0 (0)")
        self.lbl_peers = QLabel("0 (0)")

        labels = [
            ("Time Elapsed:", self.lbl_elapsed),
            ("ETA:", self.lbl_eta),
            ("Download Speed:", self.lbl_dl_spd),
            ("Upload Speed:", self.lbl_ul_spd),
            ("Downloaded:", self.lbl_downloaded),
            ("Uploaded:", self.lbl_uploaded),
            ("Seeds:", self.lbl_seeds),
            ("Peers:", self.lbl_peers),
        ]
        for row, (k, val_lbl) in enumerate(labels):
            kl = QLabel(k)
            kl.setStyleSheet("color: #888888; font-size: 11px;")
            val_lbl.setStyleSheet("font-weight: 600; font-size: 11px;")
            fl_trans.addWidget(kl, row, 0)
            fl_trans.addWidget(val_lbl, row, 1)

        split.addWidget(gb_transfer, 1)

        # Group 2: Information
        gb_info = QGroupBox("Information")
        fl_info = QGridLayout(gb_info)
        fl_info.setHorizontalSpacing(16)
        fl_info.setVerticalSpacing(4)

        self.lbl_save_path = QLabel("—")
        self.lbl_total_size = QLabel("—")
        self.lbl_added_on = QLabel("—")
        self.lbl_completed_on = QLabel("—")
        self.lbl_pieces = QLabel("—")
        self.lbl_hash = QLabel("—")

        info_labels = [
            ("Save Path:", self.lbl_save_path),
            ("Total Size:", self.lbl_total_size),
            ("Added On:", self.lbl_added_on),
            ("Completed On:", self.lbl_completed_on),
            ("Pieces:", self.lbl_pieces),
            ("Info Hash:", self.lbl_hash),
        ]
        for row, (k, val_lbl) in enumerate(info_labels):
            kl = QLabel(k)
            kl.setStyleSheet("color: #888888; font-size: 11px;")
            val_lbl.setStyleSheet("font-weight: 600; font-size: 11px;")
            val_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            fl_info.addWidget(kl, row, 0)
            fl_info.addWidget(val_lbl, row, 1)

        split.addWidget(gb_info, 1)
        outer.addLayout(split)
        return w

    # -----------------------------------------------------------------------
    # Status Bar
    # -----------------------------------------------------------------------

    def _build_status_bar(self):
        sb = self.statusBar()

        self.sb_net_icon = QLabel()
        self.sb_net_icon.setPixmap(IconFactory.create_status_dot("#22c55e", 10).pixmap(10, 10))
        sb.addWidget(self.sb_net_icon)

        self.sb_dht = QLabel("DHT: 48 nodes")
        sb.addWidget(self.sb_dht)

        sb.addWidget(QLabel(" | "))

        self.sb_speedometer = QLabel("⚡ Unlimited")
        sb.addWidget(self.sb_speedometer)

        # Permanent widgets on the right
        self.sb_dl = QLabel("↓ 0 B/s")
        self.sb_dl.setStyleSheet("color: #22c55e; font-weight: bold;")
        sb.addPermanentWidget(self.sb_dl)

        self.sb_ul = QLabel("↑ 0 B/s")
        self.sb_ul.setStyleSheet("color: #38bdf8; font-weight: bold;")
        sb.addPermanentWidget(self.sb_ul)

        self.sb_free_space = QLabel(f"Free disk space: {get_free_disk_space(self.default_save_dir)}")
        sb.addPermanentWidget(self.sb_free_space)

    # -----------------------------------------------------------------------
    # Torrent Management & Workspace Loading
    # -----------------------------------------------------------------------

    def _auto_load_workspace_torrents(self):
        """Loads available .torrent files in the directory so user has instant data."""
        candidates = ["one_piece.torrent", "spidy.torrent", "test.torrent"]
        for f in candidates:
            if os.path.exists(f):
                try:
                    item = TorrentItem(f, self.default_save_dir)
                    self._add_torrent_item(item, start_now=False)
                except Exception as exc:
                    self._log("Warning", f"Could not autoload {f}: {exc}")

        # Select the first row by default
        if self.transfer_table.rowCount() > 0:
            self.transfer_table.selectRow(0)

    def _add_torrent_item(self, item: TorrentItem, start_now: bool = True):
        # Prevent duplicates
        for existing in self.torrents:
            if existing.info_hash == item.info_hash:
                QMessageBox.information(self, "Torrent Exists", f"'{item.name}' is already in the transfer list.")
                return

        item.priority = len(self.torrents) + 1
        self.torrents.append(item)

        row = self.transfer_table.rowCount()
        self.transfer_table.insertRow(row)

        self._render_table_row(row, item)
        self._update_sidebar_counts()
        self._log("Normal", f"Added torrent: {item.name} ({fmt_size(item.total_length)})")

        if start_now:
            self._start_torrent(item)

    def _render_table_row(self, row: int, item: TorrentItem):
        pct = item.progress_pct

        it_num = QTableWidgetItem(str(item.priority))
        it_num.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        it_name = QTableWidgetItem(item.name)
        it_name.setIcon(IconFactory.create_qb_logo(16))

        it_size = QTableWidgetItem(fmt_size(item.total_length))
        it_done = QTableWidgetItem(fmt_size(item.bytes_done))

        it_status = QTableWidgetItem(item.status)
        if item.status == "Downloading":
            it_status.setForeground(QColor("#22c55e"))
        elif item.status == "Completed":
            it_status.setForeground(QColor("#10b981"))
        elif item.status == "Paused":
            it_status.setForeground(QColor("#f59e0b"))

        # Progress cell uses delegate reading UserRole
        it_prog = QTableWidgetItem()
        it_prog.setData(Qt.ItemDataRole.UserRole, pct)

        seeds_txt = f"{item.seeds_connected} ({item.seeds_total})"
        peers_txt = f"{item.peers_connected} ({item.peers_total})"
        dl_spd_txt = fmt_speed(item.dl_speed) if item.dl_speed > 0 else "—"
        ul_spd_txt = fmt_speed(item.ul_speed) if item.ul_speed > 0 else "—"
        eta_txt = fmt_duration(item.eta) if item.eta > 0 else "∞"
        ratio_txt = "0.00"

        self.transfer_table.setItem(row, 0, it_num)
        self.transfer_table.setItem(row, 1, it_name)
        self.transfer_table.setItem(row, 2, it_size)
        self.transfer_table.setItem(row, 3, it_done)
        self.transfer_table.setItem(row, 4, it_status)
        self.transfer_table.setItem(row, 5, it_prog)
        self.transfer_table.setItem(row, 6, QTableWidgetItem(seeds_txt))
        self.transfer_table.setItem(row, 7, QTableWidgetItem(peers_txt))
        self.transfer_table.setItem(row, 8, QTableWidgetItem(dl_spd_txt))
        self.transfer_table.setItem(row, 9, QTableWidgetItem(ul_spd_txt))
        self.transfer_table.setItem(row, 10, QTableWidgetItem(eta_txt))
        self.transfer_table.setItem(row, 11, QTableWidgetItem(ratio_txt))
        self.transfer_table.setItem(row, 12, QTableWidgetItem(item.added_on))
        self.transfer_table.setItem(row, 13, QTableWidgetItem(item.completed_on))

    # -----------------------------------------------------------------------
    # Torrent Actions (Start, Pause, Delete, Priority)
    # -----------------------------------------------------------------------

    def _start_torrent(self, item: TorrentItem):
        if item.status == "Downloading" or (item.worker and item.worker.isRunning()):
            return

        item.status = "Downloading"
        self._update_torrent_row(item)
        self._update_sidebar_counts()

        worker = TorrentWorker(item.torrent_path, item.save_dir, max_workers=30)
        item.worker = worker

        worker.sig_log.connect(self._log)
        worker.sig_peers_count.connect(lambda cnt: self._on_worker_peers_count(item, cnt))
        worker.sig_peer_status.connect(lambda ip, port, st: self._on_worker_peer_status(item, ip, port, st))
        worker.sig_tracker_status.connect(lambda u, st, p: self._on_worker_tracker_status(item, u, st, p))
        worker.sig_piece_done.connect(lambda idx, cnt, tot, b_done, spd:
                                      self._on_worker_piece_done(item, idx, cnt, tot, b_done, spd))
        worker.sig_finished.connect(lambda ok, msg: self._on_worker_finished(item, ok, msg))

        worker.start()

    def _pause_torrent(self, item: TorrentItem):
        if item.worker:
            item.status = "Paused"
            item.worker.request_stop()
            item.dl_speed = 0.0
            self._update_torrent_row(item)
            self._update_sidebar_counts()
            self._log("Normal", f"Paused: {item.name}")

    def _on_worker_peers_count(self, item: TorrentItem, count: int):
        item.peers_total = count
        self._update_torrent_row(item)

    def _on_worker_peer_status(self, item: TorrentItem, ip: str, port: int, status: str):
        key = f"{ip}:{port}"
        item.peer_map[key] = {"ip": ip, "port": port, "status": status}
        if status in ("Active / Unchoked", "Active / Streaming"):
            item.peers_connected = sum(1 for p in item.peer_map.values()
                                       if p["status"] in ("Active / Unchoked", "Active / Streaming"))
        if self.selected_torrent == item:
            self._refresh_peers_tab(item)

    def _on_worker_tracker_status(self, item: TorrentItem, url: str, status: str, peers: int):
        item.tracker_map[url] = {"url": url, "status": status, "peers": peers}
        if self.selected_torrent == item:
            self._refresh_trackers_tab(item)

    def _on_worker_piece_done(self, item: TorrentItem, idx: int, done_cnt: int, total: int,
                              bytes_done: int, speed: float):
        item.done_pieces.add(idx)
        item.bytes_done = bytes_done
        item.dl_speed = speed
        if speed > 0:
            rem_bytes = max(0, item.total_length - item.bytes_done)
            item.eta = rem_bytes / speed
        else:
            item.eta = 0.0

        self._update_torrent_row(item)

        # Update tabs if active
        if self.selected_torrent == item:
            self.piece_map.mark_done(idx)
            self.gen_progress.setValue(int(item.progress_pct * 10))
            self.lbl_dl_spd.setText(fmt_speed(speed))
            self.lbl_downloaded.setText(fmt_size(item.bytes_done))
            self.lbl_eta.setText(fmt_duration(item.eta))
            self.lbl_pieces.setText(f"{len(item.done_pieces)} / {item.total_pieces}")

    def _on_worker_finished(self, item: TorrentItem, success: bool, message: str):
        item.worker = None
        item.dl_speed = 0.0
        item.eta = 0.0
        if success:
            item.status = "Completed"
            item.completed_on = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._log("Normal", f"Download finished: {item.name} ✓")
        else:
            item.status = "Paused"
            self._log("Notice", f"Stopped: {item.name} — {message}")

        self._update_torrent_row(item)
        self._update_sidebar_counts()

    def _update_torrent_row(self, item: TorrentItem):
        for row in range(self.transfer_table.rowCount()):
            name_item = self.transfer_table.item(row, 1)
            if name_item and name_item.text() == item.name:
                self._render_table_row(row, item)
                break

    # -----------------------------------------------------------------------
    # Table & Details Event Handlers
    # -----------------------------------------------------------------------

    def _on_table_selection_changed(self):
        rows = self.transfer_table.selectionModel().selectedRows()
        if not rows:
            self.selected_torrent = None
            return

        row = rows[0].row()
        item_widget = self.transfer_table.item(row, 1)
        if not item_widget:
            self.selected_torrent = None
            return
        name = item_widget.text()
        for t in self.torrents:
            if t.name == name:
                self.selected_torrent = t
                self._populate_details_for_torrent(t)
                break

    def _populate_details_for_torrent(self, t: TorrentItem):
        # General Tab
        self.gen_progress.setValue(int(t.progress_pct * 10))
        self.lbl_elapsed.setText("—")
        self.lbl_eta.setText(fmt_duration(t.eta) if t.eta > 0 else "∞")
        self.lbl_dl_spd.setText(fmt_speed(t.dl_speed))
        self.lbl_ul_spd.setText(fmt_speed(t.ul_speed))
        self.lbl_downloaded.setText(fmt_size(t.bytes_done))
        self.lbl_uploaded.setText("0 B")
        self.lbl_seeds.setText(f"{t.seeds_connected} ({t.seeds_total})")
        self.lbl_peers.setText(f"{t.peers_connected} ({t.peers_total})")

        self.lbl_save_path.setText(t.save_dir)
        self.lbl_total_size.setText(fmt_size(t.total_length))
        self.lbl_added_on.setText(t.added_on)
        self.lbl_completed_on.setText(t.completed_on)
        self.lbl_pieces.setText(f"{len(t.done_pieces)} / {t.total_pieces} × {fmt_size(t.piece_length)}")
        self.lbl_hash.setText(t.info_hash)

        # Trackers Tab
        self._refresh_trackers_tab(t)

        # Peers Tab
        self._refresh_peers_tab(t)

        # Pieces Tab
        self.piece_map.set_pieces(t.total_pieces, t.done_pieces)

        # Content Tab
        self._refresh_content_tab(t)

    def _refresh_trackers_tab(self, t: TorrentItem):
        self.tab_trackers.setRowCount(0)
        trackers = t.trackers
        for i, url in enumerate(trackers):
            self.tab_trackers.insertRow(i)
            st_data = t.tracker_map.get(url, {"status": "Not contacted yet", "peers": 0})
            self.tab_trackers.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            self.tab_trackers.setItem(i, 1, QTableWidgetItem(url))
            self.tab_trackers.setItem(i, 2, QTableWidgetItem(st_data.get("status", "—")))
            self.tab_trackers.setItem(i, 3, QTableWidgetItem(str(st_data.get("peers", 0))))
            self.tab_trackers.setItem(i, 4, QTableWidgetItem("—"))
            self.tab_trackers.setItem(i, 5, QTableWidgetItem("—"))
            self.tab_trackers.setItem(i, 6, QTableWidgetItem("—"))
            self.tab_trackers.setItem(i, 7, QTableWidgetItem(""))

    def _refresh_peers_tab(self, t: TorrentItem):
        self.tab_peers.setRowCount(0)
        for i, (key, peer) in enumerate(t.peer_map.items()):
            self.tab_peers.insertRow(i)
            self.tab_peers.setItem(i, 0, QTableWidgetItem(peer["ip"]))
            self.tab_peers.setItem(i, 1, QTableWidgetItem(str(peer["port"])))
            self.tab_peers.setItem(i, 2, QTableWidgetItem("BitTorrent Client"))
            self.tab_peers.setItem(i, 3, QTableWidgetItem("u d H"))
            self.tab_peers.setItem(i, 4, QTableWidgetItem("100%"))
            self.tab_peers.setItem(i, 5, QTableWidgetItem("—"))
            self.tab_peers.setItem(i, 6, QTableWidgetItem("—"))
            self.tab_peers.setItem(i, 7, QTableWidgetItem("—"))
            self.tab_peers.setItem(i, 8, QTableWidgetItem("—"))
            self.tab_peers.setItem(i, 9, QTableWidgetItem("100%"))

    def _refresh_content_tab(self, t: TorrentItem):
        self.tab_content.clear()
        files = t.files or [{"path": t.name, "length": t.total_length}]
        for f in files:
            pct = 100.0 if t.status == "Completed" else t.progress_pct
            item = QTreeWidgetItem([
                f["path"],
                fmt_size(f["length"]),
                f"{pct:.1f}%",
                "Normal",
                fmt_size(f["length"] * (1.0 - pct / 100.0)),
            ])
            item.setCheckState(0, Qt.CheckState.Checked)
            self.tab_content.addTopLevelItem(item)

    # -----------------------------------------------------------------------
    # Context Menu & Table Actions
    # -----------------------------------------------------------------------

    def _show_table_context_menu(self, pos: QPoint):
        item = self.transfer_table.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        menu.addAction(self.act_resume)
        menu.addAction(self.act_pause)
        menu.addSeparator()

        act_recheck = menu.addAction("Force Recheck")
        act_recheck.triggered.connect(self._on_recheck_selected)

        act_folder = menu.addAction("Open Destination Folder")
        act_folder.triggered.connect(self._on_open_destination_folder)

        menu.addSeparator()
        menu.addAction(self.act_delete)

        menu.addSeparator()
        m_copy = menu.addMenu("Copy")
        m_copy.addAction("Info Hash", lambda: QApplication.clipboard().setText(self.selected_torrent.info_hash if self.selected_torrent else ""))
        m_copy.addAction("Name", lambda: QApplication.clipboard().setText(self.selected_torrent.name if self.selected_torrent else ""))

        menu.exec(self.transfer_table.viewport().mapToGlobal(pos))

    def _on_table_double_clicked(self, index: QModelIndex):
        self._on_open_destination_folder()

    def _on_open_destination_folder(self):
        if self.selected_torrent:
            target = self.selected_torrent.save_dir
            os.makedirs(target, exist_ok=True)
            subprocess.Popen(["explorer", os.path.normpath(target)])

    def _on_recheck_selected(self):
        if self.selected_torrent:
            self._log("Normal", f"Rechecking disk files for {self.selected_torrent.name} …")
            self.selected_torrent.recheck_existing()
            self._update_torrent_row(self.selected_torrent)
            self._populate_details_for_torrent(self.selected_torrent)
            self._log("Normal", f"Recheck complete: {len(self.selected_torrent.done_pieces)}/{self.selected_torrent.total_pieces} pieces verified.")

    def _on_resume_selected(self):
        if self.selected_torrent:
            self._start_torrent(self.selected_torrent)

    def _on_pause_selected(self):
        if self.selected_torrent:
            self._pause_torrent(self.selected_torrent)

    def _on_resume_all(self):
        for t in self.torrents:
            self._start_torrent(t)

    def _on_pause_all(self):
        for t in self.torrents:
            self._pause_torrent(t)

    def _on_delete_selected(self):
        if not self.selected_torrent:
            return

        box = QMessageBox(self)
        box.setWindowTitle("Remove Torrent")
        box.setText(f"Are you sure you want to remove <b>{self.selected_torrent.name}</b> from the transfer list?")
        box.setIcon(QMessageBox.Icon.Question)
        chk_files = QCheckBox("Also delete the files on the hard disk")
        box.setCheckBox(chk_files)
        box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)

        if box.exec() == QMessageBox.StandardButton.Ok:
            target = self.selected_torrent
            self._pause_torrent(target)
            if chk_files.isChecked():
                # Delete files from disk
                try:
                    files_to_delete = target.files or [{"path": target.name}]
                    for f in files_to_delete:
                        fp = os.path.join(target.save_dir, f["path"])
                        if os.path.isfile(fp):
                            os.remove(fp)
                    # If target has a folder created, remove if empty or matching torrent name
                    top_dir = os.path.join(target.save_dir, target.name)
                    if os.path.isdir(top_dir):
                        shutil.rmtree(top_dir, ignore_errors=True)
                except Exception as e:
                    self._log("Warning", f"Could not remove files from disk: {e}")

            # Remove from torrent list
            self.torrents.remove(target)
            self._rebuild_table_rows()
            self._update_sidebar_counts()
            self.selected_torrent = None
            self._log("Normal", f"Removed torrent: {target.name}")

    def _rebuild_table_rows(self):
        self.transfer_table.setRowCount(0)
        selected_row = -1
        for i, t in enumerate(self.torrents):
            t.priority = i + 1
            row = self.transfer_table.rowCount()
            self.transfer_table.insertRow(row)
            self._render_table_row(row, t)
            if self.selected_torrent and t.info_hash == self.selected_torrent.info_hash:
                selected_row = row
        if selected_row >= 0:
            self.transfer_table.selectRow(selected_row)

    def _move_priority(self, direction: str):
        if not self.selected_torrent or len(self.torrents) < 2:
            return
        idx = self.torrents.index(self.selected_torrent)
        if direction == "top" and idx > 0:
            self.torrents.insert(0, self.torrents.pop(idx))
        elif direction == "up" and idx > 0:
            self.torrents[idx], self.torrents[idx - 1] = self.torrents[idx - 1], self.torrents[idx]
        elif direction == "down" and idx < len(self.torrents) - 1:
            self.torrents[idx], self.torrents[idx + 1] = self.torrents[idx + 1], self.torrents[idx]
        elif direction == "bot" and idx < len(self.torrents) - 1:
            self.torrents.append(self.torrents.pop(idx))
        self._rebuild_table_rows()

    # -----------------------------------------------------------------------
    # Adding Torrents (File & Magnet / URL)
    # -----------------------------------------------------------------------

    def _on_add_torrent_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Torrent File", "", "Torrent Files (*.torrent);;All Files (*)"
        )
        if path:
            self._load_and_prompt_torrent(path)

    def _on_add_torrent_link(self):
        from PySide6.QtWidgets import QInputDialog
        url, ok = QInputDialog.getText(self, "Add Torrent Link", "Enter Magnet URI or HTTP Torrent Link:")
        if ok and url.strip():
            url = url.strip()
            if url.startswith("http://") or url.startswith("https://"):
                try:
                    import tempfile
                    import urllib.request
                    req = urllib.request.Request(url, headers={'User-Agent': 'qBittorrent/4.6.5'})
                    with urllib.request.urlopen(req, timeout=10) as response:
                        content = response.read()
                    temp_path = os.path.join(tempfile.gettempdir(), "downloaded.torrent")
                    with open(temp_path, "wb") as f:
                        f.write(content)
                    self._load_and_prompt_torrent(temp_path)
                except Exception as exc:
                    QMessageBox.warning(self, "Download Error", f"Failed to download torrent from link:\n{exc}")
            elif url.startswith("magnet:"):
                self._log("Notice", f"Magnet link added: {url}")
                QMessageBox.information(
                    self,
                    "Torrent Link",
                    "Magnet link registered.\nNote: Direct swarm streaming via magnet requires the BEP 9 extension."
                )
            else:
                QMessageBox.warning(self, "Invalid Link", "Please enter a valid HTTP/HTTPS torrent URL or magnet link.")

    def _load_and_prompt_torrent(self, torrent_path: str):
        try:
            summary = get_torrent_summary(torrent_path)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to parse torrent:\n{exc}")
            return

        dlg = AddTorrentDialog(summary, self.default_save_dir, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            cfg = dlg.get_settings()
            item = TorrentItem(torrent_path, cfg["save_dir"])
            item.category = cfg["category"]
            self._add_torrent_item(item, start_now=cfg["start_now"])

    # -----------------------------------------------------------------------
    # Filters & Search
    # -----------------------------------------------------------------------

    def _on_sidebar_item_clicked(self, item: QTreeWidgetItem, column: int):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        self.sidebar_filter = data
        self._apply_row_filters()

    def _on_filter_changed(self, text: str):
        self.search_filter = text.strip().lower()
        self._apply_row_filters()

    def _apply_row_filters(self):
        for row in range(self.transfer_table.rowCount()):
            item_widget = self.transfer_table.item(row, 1)
            if not item_widget:
                continue
            name = item_widget.text()
            item = next((t for t in self.torrents if t.name == name), None)
            if not item:
                continue

            visible = True
            # Status filter
            if self.sidebar_filter != "All":
                if self.sidebar_filter in ("Downloading", "Seeding", "Completed", "Paused"):
                    if item.status != self.sidebar_filter:
                        visible = False

            # Search text filter
            if self.search_filter:
                if self.search_filter not in item.name.lower():
                    visible = False

            self.transfer_table.setRowHidden(row, not visible)

    # -----------------------------------------------------------------------
    # Preferences & Theme Toggling
    # -----------------------------------------------------------------------

    def _on_show_options(self):
        dlg = PreferencesDialog(self.default_save_dir, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.default_save_dir = dlg.get_save_dir()
            self._log("Normal", f"Preferences saved. Default save path: {self.default_save_dir}")

    def _toggle_theme(self, checked: bool):
        self.is_dark_theme = checked
        self.setStyleSheet(QBIT_DARK_THEME if checked else QBIT_LIGHT_THEME)

    # -----------------------------------------------------------------------
    # Real-Time Stats Tick (Every 1 second)
    # -----------------------------------------------------------------------

    def _on_stats_tick(self):
        # Calculate aggregate speeds
        total_dl = sum(t.dl_speed for t in self.torrents if t.status == "Downloading")
        total_ul = sum(t.ul_speed for t in self.torrents)

        self.sb_dl.setText(f"↓ {fmt_speed(total_dl)}")
        self.sb_ul.setText(f"↑ {fmt_speed(total_ul)}")

        # Add point to speed graph tab
        self.speed_graph.add_sample(total_dl, total_ul)

        # Update free space
        self.sb_free_space.setText(f"Free disk space: {get_free_disk_space(self.default_save_dir)}")

    # -----------------------------------------------------------------------
    # Logging
    # -----------------------------------------------------------------------

    @Slot(str, str)
    def _log(self, level: str, msg: str):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        prefix = f"({level[0]}) {now} -"
        self.tab_log.appendPlainText(f"{prefix} {msg}")

    # -----------------------------------------------------------------------
    # Close confirmation
    # -----------------------------------------------------------------------

    def closeEvent(self, event):
        active = any(t.status == "Downloading" for t in self.torrents)
        if active:
            r = QMessageBox.question(
                self, "Quit qBittorrent",
                "Downloads are currently in progress. Do you want to pause all torrents and exit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if r == QMessageBox.StandardButton.No:
                event.ignore()
                return
            for t in self.torrents:
                self._pause_torrent(t)
        event.accept()


# ===========================================================================
# Main Entry Point
# ===========================================================================

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = QBitMainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

"""
BitTorrent Client — PySide6 GUI
A qBittorrent-inspired desktop interface with dark theme, transfer list,
detail panel (General · Trackers · Peers · Pieces · Log), and status bar.
"""

import os
import sys
import time
import subprocess
from datetime import timedelta

from PySide6.QtCore import (
    Qt, QThread, Signal, Slot, QTimer, QSize, QRectF,
)
from PySide6.QtGui import (
    QAction, QColor, QFont, QPainter, QBrush, QPen, QIcon, QPalette,
    QLinearGradient, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QPushButton, QProgressBar, QFileDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QPlainTextEdit,
    QTabWidget, QFrame, QSplitter, QMessageBox, QToolBar, QStatusBar,
    QMenuBar, QMenu, QAbstractItemView, QSizePolicy, QSpinBox, QDialog,
    QDialogButtonBox, QFormLayout, QLineEdit,
)

from calc_hash import get_torrent_summary
from tracker import generate_peer_id, get_peers, get_all_trackers
from swarm_downloader import SwarmDownloader


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fmt_size(n: float) -> str:
    """Human-readable file size."""
    if n is None or n < 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0:
            return f"{n:.2f} {unit}"
        n /= 1024.0
    return f"{n:.2f} PB"


def fmt_speed(n: float) -> str:
    """Human-readable speed (per second)."""
    return f"{fmt_size(n)}/s"


def fmt_duration(seconds: float) -> str:
    """Human-readable time duration."""
    if seconds <= 0:
        return "∞"
    td = timedelta(seconds=int(seconds))
    parts = []
    hrs, remainder = divmod(td.seconds, 3600)
    mins, secs = divmod(remainder, 60)
    if td.days:
        parts.append(f"{td.days}d")
    if hrs:
        parts.append(f"{hrs}h")
    parts.append(f"{mins}m {secs}s")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Piece Map Widget (visual grid like µTorrent / qBittorrent)
# ---------------------------------------------------------------------------

class PieceMapWidget(QWidget):
    """Renders every piece as a small coloured cell in a grid layout.
    Grey = pending, bright green = verified & saved."""

    COLOR_BG     = QColor("#0e1015")
    COLOR_EMPTY  = QColor("#1e2028")
    COLOR_DONE   = QColor("#22c55e")  # vibrant green
    COLOR_BORDER = QColor("#2a2d3a")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.total = 0
        self.completed: set[int] = set()
        self.setMinimumHeight(120)

    def reset(self, total: int):
        self.total = total
        self.completed.clear()
        self.update()

    def mark_done(self, idx: int):
        self.completed.add(idx)
        self.update()

    # -- painting ----------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.fillRect(self.rect(), self.COLOR_BG)

        if self.total <= 0:
            painter.setPen(QColor("#555a6e"))
            painter.setFont(QFont("Segoe UI", 9))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Load a .torrent to visualize piece progress")
            return

        w = self.width()
        h = self.height()
        pad = 4

        usable_w = w - 2 * pad
        usable_h = h - 2 * pad
        if usable_w <= 0 or usable_h <= 0:
            return

        # Auto-compute block size so all pieces fit
        # Try to fill horizontally first
        cols = max(1, int(usable_w / 6))      # start with ~6px blocks
        rows = max(1, (self.total + cols - 1) // cols)
        cell_w = usable_w / cols
        cell_h = usable_h / rows

        # If rows overflow, shrink cells
        if cell_h < 2:
            cell_h = 2
            rows = int(usable_h / cell_h)
            cols = max(1, (self.total + rows - 1) // rows)
            cell_w = usable_w / cols

        gap = max(0.5, min(cell_w, cell_h) * 0.1)

        brush_done = QBrush(self.COLOR_DONE)
        brush_empty = QBrush(self.COLOR_EMPTY)
        painter.setPen(Qt.PenStyle.NoPen)

        idx = 0
        y = pad
        for _ in range(rows):
            x = pad
            for _ in range(cols):
                if idx >= self.total:
                    break
                painter.setBrush(brush_done if idx in self.completed else brush_empty)
                painter.drawRect(QRectF(x, y, cell_w - gap, cell_h - gap))
                x += cell_w
                idx += 1
            y += cell_h
            if idx >= self.total:
                break


# ---------------------------------------------------------------------------
# Torrent Worker Thread
# ---------------------------------------------------------------------------

class TorrentWorker(QThread):
    """Runs tracker scrape + swarm download on a background thread."""

    sig_log          = Signal(str)
    sig_metadata     = Signal(dict)          # full summary dict
    sig_tracker_done = Signal(str, int)       # tracker_url, peer_count
    sig_peers_total  = Signal(int)
    sig_peer_status  = Signal(str, int, str)  # ip, port, status
    sig_piece_done   = Signal(int, int, int, float, float)  # idx, done_cnt, total, bytes_done, speed
    sig_finished     = Signal(bool, str)      # success, message

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
            self.sig_log.emit(
                f"Torrent: {summary['name']}  |  "
                f"{fmt_size(summary['total_length'])}  |  "
                f"{len(summary['piece_hashes'])} pieces"
            )

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
            self.sig_log.emit(f"Total unique peers discovered: {len(peers)}")

            if not peers:
                self.sig_finished.emit(False, "No peers found — nothing to download.")
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

            self.sig_log.emit(
                f"Starting swarm: {len(peers)} peers, "
                f"{self.max_workers} workers → {self.download_dir}"
            )

            success = self.downloader.download()

            if self._stopped:
                self.sig_finished.emit(False, "Download stopped by user.")
            elif success:
                self.sig_finished.emit(True, "Download complete — all pieces verified ✓")
            else:
                done = len(self.downloader.done)
                total = self.downloader.total_pieces
                self.sig_finished.emit(
                    False,
                    f"Partial download: {done}/{total} pieces. Re-run to resume.",
                )

        except Exception as exc:
            self.sig_log.emit(f"FATAL: {exc}")
            self.sig_finished.emit(False, str(exc))

    def _on_piece(self, idx, done_cnt, total, bytes_done, total_len, speed):
        self.sig_piece_done.emit(idx, done_cnt, total, bytes_done, speed)


# ---------------------------------------------------------------------------
# Main Window
# ---------------------------------------------------------------------------

DARK_STYLESHEET = """
/* ── Base ─────────────────────────────────────────────────── */
QMainWindow, QWidget {
    background-color: #0e1015;
    color: #c9cdd6;
    font-family: "Segoe UI", "SF Pro Display", system-ui, sans-serif;
    font-size: 13px;
}

/* ── Menu bar ─────────────────────────────────────────────── */
QMenuBar {
    background-color: #13151c;
    border-bottom: 1px solid #1d2030;
    padding: 2px 0;
}
QMenuBar::item {
    padding: 5px 12px;
    border-radius: 4px;
}
QMenuBar::item:selected {
    background-color: #1e2230;
}
QMenu {
    background-color: #15171f;
    border: 1px solid #24283a;
    border-radius: 6px;
    padding: 4px 0;
}
QMenu::item {
    padding: 6px 28px 6px 16px;
}
QMenu::item:selected {
    background-color: #1e2230;
    color: #22c55e;
}
QMenu::separator {
    height: 1px;
    background: #24283a;
    margin: 4px 8px;
}

/* ── Toolbar ──────────────────────────────────────────────── */
QToolBar {
    background-color: #13151c;
    border-bottom: 1px solid #1d2030;
    spacing: 4px;
    padding: 3px 8px;
}
QToolBar QToolButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
    padding: 5px 10px;
    color: #c9cdd6;
    font-weight: 600;
    font-size: 13px;
}
QToolBar QToolButton:hover {
    background-color: #1a1d28;
    border-color: #2d3250;
}
QToolBar QToolButton:pressed {
    background-color: #151820;
}

/* ── Tables ───────────────────────────────────────────────── */
QTableWidget {
    background-color: #0e1015;
    alternate-background-color: #12141b;
    gridline-color: #1a1d28;
    border: none;
    selection-background-color: #1b2e1e;
    selection-color: #c9cdd6;
    font-size: 12px;
}
QTableWidget::item {
    padding: 4px 8px;
}
QHeaderView::section {
    background-color: #13151c;
    color: #6b7280;
    font-weight: 700;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    padding: 6px 10px;
    border: none;
    border-bottom: 1px solid #1d2030;
    border-right: 1px solid #1a1d28;
}

/* ── Tabs ─────────────────────────────────────────────────── */
QTabWidget::pane {
    border: none;
    background-color: #0e1015;
}
QTabBar {
    background: #13151c;
}
QTabBar::tab {
    background: transparent;
    color: #6b7280;
    padding: 8px 18px;
    border: none;
    border-bottom: 2px solid transparent;
    font-weight: 600;
    font-size: 12px;
}
QTabBar::tab:selected {
    color: #22c55e;
    border-bottom: 2px solid #22c55e;
}
QTabBar::tab:hover:!selected {
    color: #a1a7b4;
}

/* ── Progress bar ─────────────────────────────────────────── */
QProgressBar {
    background-color: #1a1d28;
    border: none;
    border-radius: 4px;
    text-align: center;
    font-weight: bold;
    font-size: 11px;
    color: #ffffff;
    min-height: 18px;
    max-height: 18px;
}
QProgressBar::chunk {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #16a34a, stop:1 #22c55e
    );
    border-radius: 4px;
}

/* ── Log pane ─────────────────────────────────────────────── */
QPlainTextEdit {
    background-color: #0c0d12;
    color: #8b92a8;
    border: none;
    font-family: "Cascadia Code", "Consolas", "Fira Code", monospace;
    font-size: 12px;
    selection-background-color: #1b2e1e;
}

/* ── Splitter ─────────────────────────────────────────────── */
QSplitter::handle {
    background-color: #1d2030;
    height: 3px;
}
QSplitter::handle:hover {
    background-color: #22c55e;
}

/* ── Scrollbars ───────────────────────────────────────────── */
QScrollBar:vertical {
    background: #0e1015;
    width: 8px;
    border: none;
}
QScrollBar::handle:vertical {
    background: #2a2d3a;
    border-radius: 4px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: #3d4154;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: none;
    height: 0;
}
QScrollBar:horizontal {
    background: #0e1015;
    height: 8px;
    border: none;
}
QScrollBar::handle:horizontal {
    background: #2a2d3a;
    border-radius: 4px;
    min-width: 30px;
}
QScrollBar::handle:horizontal:hover {
    background: #3d4154;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: none;
    width: 0;
}

/* ── Status bar ───────────────────────────────────────────── */
QStatusBar {
    background-color: #13151c;
    border-top: 1px solid #1d2030;
    color: #6b7280;
    font-size: 12px;
}
QStatusBar QLabel {
    padding: 0 8px;
    color: #6b7280;
}

/* ── Info cards (QFrame.info-card) ─────────────────────────── */
QFrame[class="info-card"] {
    background-color: #12141b;
    border: 1px solid #1d2030;
    border-radius: 6px;
}

/* ── Buttons inside dialogs ───────────────────────────────── */
QPushButton {
    background-color: #1a1d28;
    border: 1px solid #2d3250;
    border-radius: 5px;
    padding: 6px 16px;
    color: #c9cdd6;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #242838;
    border-color: #3d4a70;
}
QPushButton:pressed {
    background-color: #161924;
}

QSpinBox, QLineEdit {
    background-color: #12141b;
    border: 1px solid #24283a;
    border-radius: 4px;
    padding: 4px 8px;
    color: #c9cdd6;
}
"""


class BitTorrentClient(QMainWindow):
    """qBittorrent-inspired main window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("BitTorrent Client")
        self.resize(1100, 740)

        # State
        self._torrent_path: str = ""
        self._download_dir: str = os.path.join(os.getcwd(), "downloads")
        self._summary: dict | None = None
        self._worker: TorrentWorker | None = None
        self._peer_rows: dict[str, int] = {}   # "ip:port" → row index
        self._tracker_rows: dict[str, int] = {}
        self._start_time: float = 0.0
        self._total_peers: int = 0
        self._max_workers: int = 30
        self._is_downloading: bool = False

        self.setStyleSheet(DARK_STYLESHEET)
        self._build_menu_bar()
        self._build_toolbar()
        self._build_ui()
        self._build_status_bar()
        self._update_actions()

    # ===================================================================
    #  UI Construction
    # ===================================================================

    def _build_menu_bar(self):
        mb = self.menuBar()

        # File
        file_menu = mb.addMenu("&File")
        self.act_add_torrent = QAction("Add &Torrent File…", self)
        self.act_add_torrent.setShortcut("Ctrl+O")
        self.act_add_torrent.triggered.connect(self._browse_torrent)
        file_menu.addAction(self.act_add_torrent)

        file_menu.addSeparator()

        self.act_exit = QAction("E&xit", self)
        self.act_exit.setShortcut("Ctrl+Q")
        self.act_exit.triggered.connect(self.close)
        file_menu.addAction(self.act_exit)

        # Tools
        tools_menu = mb.addMenu("&Tools")
        self.act_options = QAction("&Options…", self)
        self.act_options.triggered.connect(self._show_options)
        tools_menu.addAction(self.act_options)

        # Help
        help_menu = mb.addMenu("&Help")
        self.act_about = QAction("&About", self)
        self.act_about.triggered.connect(
            lambda: QMessageBox.about(
                self,
                "About",
                "<h3>BitTorrent Client</h3>"
                "<p>A lightweight torrent client built with Python &amp; PySide6.</p>"
                "<p>Inspired by qBittorrent.</p>",
            )
        )
        help_menu.addAction(self.act_about)

    def _build_toolbar(self):
        tb = QToolBar("Main Toolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        self.addToolBar(tb)

        self.act_tb_add = tb.addAction("📁  Add Torrent")
        self.act_tb_add.triggered.connect(self._browse_torrent)

        self.act_tb_start = tb.addAction("▶  Start")
        self.act_tb_start.triggered.connect(self._start_download)

        self.act_tb_stop = tb.addAction("⏹  Stop")
        self.act_tb_stop.triggered.connect(self._stop_download)

        tb.addSeparator()

        self.act_tb_folder = tb.addAction("📂  Open Folder")
        self.act_tb_folder.triggered.connect(self._open_folder)

    # ------------------------------------------------------------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setHandleWidth(4)

        # ── Top: Transfer List Table ──────────────────────────────────
        self.transfer_table = QTableWidget(0, 7)
        self.transfer_table.setHorizontalHeaderLabels(
            ["Name", "Size", "Progress", "Status", "Speed", "Peers", "ETA"]
        )
        self.transfer_table.setAlternatingRowColors(True)
        self.transfer_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.transfer_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.transfer_table.verticalHeader().setVisible(False)
        self.transfer_table.setShowGrid(False)

        hdr = self.transfer_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, 7):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.transfer_table.setMinimumHeight(60)

        splitter.addWidget(self.transfer_table)

        # ── Bottom: Detail Tabs ───────────────────────────────────────
        self.detail_tabs = QTabWidget()

        # Tab: General
        self.tab_general = self._build_general_tab()
        self.detail_tabs.addTab(self.tab_general, "General")

        # Tab: Trackers
        self.tracker_table = QTableWidget(0, 3)
        self.tracker_table.setHorizontalHeaderLabels(["Tracker URL", "Status", "Peers"])
        self.tracker_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tracker_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tracker_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tracker_table.verticalHeader().setVisible(False)
        self.tracker_table.setAlternatingRowColors(True)
        self.tracker_table.setShowGrid(False)
        self.detail_tabs.addTab(self.tracker_table, "Trackers")

        # Tab: Peers
        self.peer_table = QTableWidget(0, 3)
        self.peer_table.setHorizontalHeaderLabels(["IP", "Port", "Status"])
        self.peer_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.peer_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.peer_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.peer_table.verticalHeader().setVisible(False)
        self.peer_table.setAlternatingRowColors(True)
        self.peer_table.setShowGrid(False)
        self.detail_tabs.addTab(self.peer_table, "Peers")

        # Tab: Pieces
        self.piece_map = PieceMapWidget()
        self.detail_tabs.addTab(self.piece_map, "Pieces")

        # Tab: Log
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.detail_tabs.addTab(self.log_view, "Log")

        splitter.addWidget(self.detail_tabs)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        root.addWidget(splitter)

    def _build_general_tab(self) -> QWidget:
        """Build the 'General' info panel (mirrors qBittorrent layout)."""
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(16)

        # -- Transfer card ---
        transfer_card = QFrame()
        transfer_card.setProperty("class", "info-card")
        tc_layout = QVBoxLayout(transfer_card)
        tc_layout.setContentsMargins(16, 12, 16, 12)
        tc_layout.setSpacing(8)

        section_lbl = QLabel("TRANSFER")
        section_lbl.setStyleSheet("color: #6b7280; font-size: 11px; font-weight: 700; letter-spacing: 1px;")
        tc_layout.addWidget(section_lbl)

        # Progress bar spanning full width
        self.main_progress = QProgressBar()
        self.main_progress.setRange(0, 1000)
        self.main_progress.setValue(0)
        tc_layout.addWidget(self.main_progress)

        grid = QGridLayout()
        grid.setHorizontalSpacing(32)
        grid.setVerticalSpacing(6)

        self.lbl_dl_speed = self._stat_label("0 B/s")
        self.lbl_eta      = self._stat_label("∞")
        self.lbl_downloaded = self._stat_label("0 B")
        self.lbl_pieces_done = self._stat_label("0 / 0")

        grid.addWidget(self._dim_label("Down Speed:"), 0, 0)
        grid.addWidget(self.lbl_dl_speed,              0, 1)
        grid.addWidget(self._dim_label("ETA:"),        0, 2)
        grid.addWidget(self.lbl_eta,                   0, 3)
        grid.addWidget(self._dim_label("Downloaded:"), 1, 0)
        grid.addWidget(self.lbl_downloaded,             1, 1)
        grid.addWidget(self._dim_label("Pieces:"),     1, 2)
        grid.addWidget(self.lbl_pieces_done,            1, 3)

        tc_layout.addLayout(grid)
        outer.addWidget(transfer_card)

        # -- Information card ---
        info_card = QFrame()
        info_card.setProperty("class", "info-card")
        ic_layout = QVBoxLayout(info_card)
        ic_layout.setContentsMargins(16, 12, 16, 12)
        ic_layout.setSpacing(8)

        info_lbl = QLabel("INFORMATION")
        info_lbl.setStyleSheet("color: #6b7280; font-size: 11px; font-weight: 700; letter-spacing: 1px;")
        ic_layout.addWidget(info_lbl)

        info_grid = QGridLayout()
        info_grid.setHorizontalSpacing(32)
        info_grid.setVerticalSpacing(6)

        self.lbl_name     = self._stat_label("—")
        self.lbl_hash     = self._stat_label("—")
        self.lbl_size     = self._stat_label("—")
        self.lbl_pieces   = self._stat_label("—")
        self.lbl_save     = self._stat_label(self._download_dir)
        self.lbl_tracker  = self._stat_label("—")
        self.lbl_status   = self._stat_label("Idle")

        info_grid.addWidget(self._dim_label("Name:"),       0, 0)
        info_grid.addWidget(self.lbl_name,                  0, 1, 1, 3)
        info_grid.addWidget(self._dim_label("Info Hash:"),  1, 0)
        info_grid.addWidget(self.lbl_hash,                  1, 1, 1, 3)
        info_grid.addWidget(self._dim_label("Total Size:"), 2, 0)
        info_grid.addWidget(self.lbl_size,                  2, 1)
        info_grid.addWidget(self._dim_label("Pieces:"),     2, 2)
        info_grid.addWidget(self.lbl_pieces,                2, 3)
        info_grid.addWidget(self._dim_label("Save Path:"),  3, 0)
        info_grid.addWidget(self.lbl_save,                  3, 1, 1, 3)
        info_grid.addWidget(self._dim_label("Tracker:"),    4, 0)
        info_grid.addWidget(self.lbl_tracker,               4, 1, 1, 3)
        info_grid.addWidget(self._dim_label("Status:"),     5, 0)
        info_grid.addWidget(self.lbl_status,                5, 1, 1, 3)

        ic_layout.addLayout(info_grid)
        outer.addWidget(info_card)

        outer.addStretch()
        return w

    # label helpers
    @staticmethod
    def _dim_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #6b7280; font-size: 12px;")
        return lbl

    @staticmethod
    def _stat_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #e2e8f0; font-size: 12px;")
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return lbl

    # ------------------------------------------------------------------

    def _build_status_bar(self):
        sb = QStatusBar()
        self.setStatusBar(sb)

        self.sb_status = QLabel("Idle")
        self.sb_peers  = QLabel("Peers: 0")
        self.sb_speed  = QLabel("D: 0 B/s")

        sb.addWidget(self.sb_status, 1)
        sb.addPermanentWidget(self.sb_peers)
        sb.addPermanentWidget(self.sb_speed)

    # ===================================================================
    #  Actions
    # ===================================================================

    def _update_actions(self):
        has_torrent = bool(self._torrent_path)
        self.act_tb_start.setEnabled(has_torrent and not self._is_downloading)
        self.act_tb_stop.setEnabled(self._is_downloading)
        self.act_tb_add.setEnabled(not self._is_downloading)

    def _browse_torrent(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Torrent File", "",
            "Torrent Files (*.torrent);;All Files (*)"
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

        # Fill General tab
        name = summary["name"]
        total = summary["total_length"]
        n_pieces = len(summary["piece_hashes"])
        piece_len = summary["piece_length"]

        self.lbl_name.setText(name)
        self.lbl_hash.setText(summary["info_hash_hex"])
        self.lbl_size.setText(fmt_size(total))
        self.lbl_pieces.setText(f"{n_pieces} × {fmt_size(piece_len)}")
        self.lbl_tracker.setText(summary["announce"] or "—")
        self.lbl_save.setText(self._download_dir)
        self.lbl_status.setText("Ready")

        # Fill trackers table
        self.tracker_table.setRowCount(0)
        self._tracker_rows.clear()
        trackers = summary.get("announce_list") or [summary["announce"]]
        for i, url in enumerate(trackers):
            self.tracker_table.insertRow(i)
            self.tracker_table.setItem(i, 0, QTableWidgetItem(url))
            self.tracker_table.setItem(i, 1, QTableWidgetItem("Not contacted"))
            self.tracker_table.setItem(i, 2, QTableWidgetItem("—"))
            self._tracker_rows[url] = i

        # Piece map
        self.piece_map.reset(n_pieces)

        # Transfer list — single row
        self.transfer_table.setRowCount(1)
        self.transfer_table.setItem(0, 0, QTableWidgetItem(name))
        self.transfer_table.setItem(0, 1, QTableWidgetItem(fmt_size(total)))

        prog_item = QTableWidgetItem("0.0 %")
        prog_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.transfer_table.setItem(0, 2, prog_item)

        status_item = QTableWidgetItem("Ready")
        self.transfer_table.setItem(0, 3, status_item)
        self.transfer_table.setItem(0, 4, QTableWidgetItem("—"))
        self.transfer_table.setItem(0, 5, QTableWidgetItem("0"))
        self.transfer_table.setItem(0, 6, QTableWidgetItem("—"))
        self.transfer_table.selectRow(0)

        # Reset progress
        self.main_progress.setValue(0)
        self.lbl_dl_speed.setText("—")
        self.lbl_eta.setText("∞")
        self.lbl_downloaded.setText("0 B")
        self.lbl_pieces_done.setText(f"0 / {n_pieces}")

        self._log(f"Loaded: {name} ({fmt_size(total)}, {n_pieces} pieces)")
        self.sb_status.setText(f"Loaded: {name}")
        self._update_actions()

    # ------------------------------------------------------------------

    def _start_download(self):
        if not self._torrent_path or self._is_downloading:
            return

        self._is_downloading = True
        self._start_time = time.time()
        self._peer_rows.clear()
        self.peer_table.setRowCount(0)

        # Update visual status
        self.lbl_status.setText("Downloading …")
        self.sb_status.setText("Downloading")
        if self.transfer_table.rowCount() > 0:
            st = self.transfer_table.item(0, 3)
            if st:
                st.setText("Downloading")

        self._update_actions()

        self._worker = TorrentWorker(
            self._torrent_path, self._download_dir, self._max_workers,
        )
        self._worker.sig_log.connect(self._log)
        self._worker.sig_metadata.connect(self._on_metadata)
        self._worker.sig_tracker_done.connect(self._on_tracker_done)
        self._worker.sig_peers_total.connect(self._on_peers_total)
        self._worker.sig_peer_status.connect(self._on_peer_status)
        self._worker.sig_piece_done.connect(self._on_piece_done)
        self._worker.sig_finished.connect(self._on_finished)
        self._worker.start()

    def _stop_download(self):
        if self._worker:
            self._log("Requesting stop …")
            self._worker.request_stop()
            self.lbl_status.setText("Stopping …")

    def _open_folder(self):
        target = self._download_dir
        if not os.path.isdir(target):
            os.makedirs(target, exist_ok=True)
        # Windows-specific
        subprocess.Popen(["explorer", os.path.normpath(target)])

    def _show_options(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Options")
        dlg.setMinimumWidth(380)
        layout = QFormLayout(dlg)

        dir_edit = QLineEdit(self._download_dir)
        browse_btn = QPushButton("Browse…")

        dir_row = QHBoxLayout()
        dir_row.addWidget(dir_edit, 1)
        dir_row.addWidget(browse_btn)

        def pick_dir():
            d = QFileDialog.getExistingDirectory(dlg, "Download Directory", self._download_dir)
            if d:
                dir_edit.setText(d)
        browse_btn.clicked.connect(pick_dir)

        workers_spin = QSpinBox()
        workers_spin.setRange(1, 100)
        workers_spin.setValue(self._max_workers)

        layout.addRow("Download folder:", dir_row)
        layout.addRow("Max connections:", workers_spin)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addRow(btns)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._download_dir = dir_edit.text()
            self._max_workers = workers_spin.value()
            self.lbl_save.setText(self._download_dir)
            self._log(f"Settings updated — save dir: {self._download_dir}, workers: {self._max_workers}")

    # ===================================================================
    #  Slots — worker signals
    # ===================================================================

    @Slot(str)
    def _log(self, text: str):
        ts = time.strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{ts}]  {text}")

    @Slot(dict)
    def _on_metadata(self, summary: dict):
        self._summary = summary
        n = len(summary["piece_hashes"])
        self.piece_map.reset(n)
        self.lbl_pieces_done.setText(f"0 / {n}")

    @Slot(str, int)
    def _on_tracker_done(self, url: str, peer_count: int):
        row = self._tracker_rows.get(url)
        if row is None:
            return
        if peer_count < 0:
            self.tracker_table.item(row, 1).setText("Error")
            self.tracker_table.item(row, 2).setText("—")
        elif peer_count == 0:
            self.tracker_table.item(row, 1).setText("Working")
            self.tracker_table.item(row, 2).setText("0")
        else:
            self.tracker_table.item(row, 1).setText("Working")
            self.tracker_table.item(row, 2).setText(str(peer_count))

    @Slot(int)
    def _on_peers_total(self, total: int):
        self._total_peers = total
        self.sb_peers.setText(f"Peers: {total}")

    @Slot(str, int, str)
    def _on_peer_status(self, ip: str, port: int, status: str):
        key = f"{ip}:{port}"
        if key not in self._peer_rows:
            row = self.peer_table.rowCount()
            self.peer_table.insertRow(row)
            self.peer_table.setItem(row, 0, QTableWidgetItem(ip))
            self.peer_table.setItem(row, 1, QTableWidgetItem(str(port)))
            st_item = QTableWidgetItem(status)
            self.peer_table.setItem(row, 2, st_item)
            self._peer_rows[key] = row
        else:
            row = self._peer_rows[key]
            item = self.peer_table.item(row, 2)
            if item:
                item.setText(status)

        # Color-code status
        row = self._peer_rows[key]
        color_map = {
            "Active / Unchoked": "#22c55e",
            "Connected (Choked)": "#eab308",
            "Connecting": "#3b82f6",
            "Discovered": "#6b7280",
            "Disconnected": "#4b5563",
            "Timeout (Choked)": "#ef4444",
        }
        c = color_map.get(status, "#6b7280")
        item = self.peer_table.item(row, 2)
        if item:
            item.setForeground(QColor(c))

    @Slot(int, int, int, float, float)
    def _on_piece_done(self, idx: int, done_cnt: int, total: int, bytes_done: float, speed: float):
        # Piece map
        self.piece_map.mark_done(idx)

        # Progress numbers
        pct = done_cnt / total if total else 0
        self.main_progress.setValue(int(pct * 1000))
        self.lbl_dl_speed.setText(fmt_speed(speed))
        self.lbl_dl_speed.setStyleSheet("color: #22c55e; font-size: 12px; font-weight: bold;")
        self.lbl_downloaded.setText(fmt_size(bytes_done))
        self.lbl_pieces_done.setText(f"{done_cnt} / {total}")

        # ETA
        if speed > 0 and self._summary:
            remaining = self._summary["total_length"] - bytes_done
            eta_s = remaining / speed
            self.lbl_eta.setText(fmt_duration(eta_s))
        else:
            self.lbl_eta.setText("∞")

        # Status bar
        self.sb_speed.setText(f"D: {fmt_speed(speed)}")

        # Transfer table row
        if self.transfer_table.rowCount() > 0:
            self.transfer_table.item(0, 2).setText(f"{pct*100:.1f} %")
            self.transfer_table.item(0, 4).setText(fmt_speed(speed))
            self.transfer_table.item(0, 5).setText(str(self._total_peers))
            if speed > 0 and self._summary:
                remaining = self._summary["total_length"] - bytes_done
                self.transfer_table.item(0, 6).setText(fmt_duration(remaining / speed))

    @Slot(bool, str)
    def _on_finished(self, success: bool, message: str):
        self._is_downloading = False
        elapsed = time.time() - self._start_time if self._start_time else 0

        status_text = "Completed ✓" if success else "Stopped"
        self.lbl_status.setText(status_text)
        self.sb_status.setText(status_text)
        self.sb_speed.setText("D: 0 B/s")
        self.lbl_eta.setText("—")

        if self.transfer_table.rowCount() > 0:
            st = self.transfer_table.item(0, 3)
            if st:
                st.setText(status_text)
            spd = self.transfer_table.item(0, 4)
            if spd:
                spd.setText("—")
            eta = self.transfer_table.item(0, 6)
            if eta:
                eta.setText("—")
            if success:
                prog = self.transfer_table.item(0, 2)
                if prog:
                    prog.setText("100.0 %")

        self._log(f"{message}  (elapsed: {fmt_duration(elapsed)})")
        self._update_actions()

        if success:
            QMessageBox.information(
                self, "Download Complete",
                f"{message}\n\nSaved to: {self._download_dir}",
            )

    # ===================================================================
    #  Window events
    # ===================================================================

    def closeEvent(self, event):
        if self._is_downloading:
            reply = QMessageBox.question(
                self, "Quit?",
                "A download is in progress. Stop and quit?",
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
# Entry point
# ---------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # consistent cross-platform base
    window = BitTorrentClient()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

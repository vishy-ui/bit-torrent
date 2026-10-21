import time
import threading
from concurrent.futures import ThreadPoolExecutor
from peer import PeerConnection
from piece_downloader import download_piece_from_peer, peer_has_piece
from disk_manager import DiskManager


class SwarmDownloader:
    """
    Phase 6 & 7: Concurrent swarm downloader with disk assembly.
    Spawns a pool of worker threads — one per peer — that all share
    a global piece queue.  Each worker:
      1. Opens a TCP connection and does the BitTorrent handshake.
      2. Reads the peer's BITFIELD so we know which pieces it has.
      3. Sends INTERESTED and waits to be unchoked.
      4. Loops: grab an un-started piece from the queue, download it,
         verify its SHA-1, write it to disk via DiskManager.
    Pieces that fail (bad hash, timeout, choke) are put back in the
    queue for another worker to retry.
    """

    def __init__(
        self,
        summary: dict,
        peers: list,
        peer_id: bytes,
        download_dir: str = "downloads",
        max_workers: int = 30,
    ):
        self.summary = summary
        self.peers = peers
        self.peer_id = peer_id
        self.download_dir = download_dir
        self.max_workers = max_workers

        self.total_pieces = len(summary["piece_hashes"])
        self.total_length = summary["total_length"]
        self.piece_length = summary["piece_length"]

        # Phase 6 - Disk I/O
        self.disk_manager = DiskManager(
            download_dir=self.download_dir,
            files_layout=summary.get(
                "files", [{"path": summary["name"], "length": self.total_length}]
            ),
            piece_length=self.piece_length,
            total_length=self.total_length,
        )

        # Shared state - guarded by self.lock
        self.lock = threading.Lock()
        self.queue = list(range(self.total_pieces))   # pieces still to download
        self.done = set()                              # verified & saved pieces
        self.in_flight = set()                        # pieces currently being worked on
        self.bytes_done = 0
        self._stop = False                            # signal workers to stop
        self._start_time = 0.0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _piece_size(self, idx: int) -> int:
        if idx == self.total_pieces - 1:
            rem = self.total_length % self.piece_length
            return rem if rem else self.piece_length
        return self.piece_length

    def _claim_piece(self, bitfield: bytes | None) -> int | None:
        """Thread-safe: pick a piece this peer has and mark it in-flight."""
        with self.lock:
            if self._stop:
                return None
            available = [p for p in self.queue if p not in self.in_flight]
            if not available:
                return None
            # Prefer pieces the peer actually has (bitfield check)
            if bitfield:
                for p in available:
                    if peer_has_piece(bitfield, p):
                        self.in_flight.add(p)
                        return p
            # Fallback: just take the first piece
            p = available[0]
            self.in_flight.add(p)
            return p

    def _release_piece(self, idx: int):
        """Return a piece to the queue (after failure)."""
        with self.lock:
            self.in_flight.discard(idx)

    def _complete_piece(self, idx: int, nbytes: int):
        """Mark a piece as done."""
        with self.lock:
            self.queue.remove(idx)
            self.in_flight.discard(idx)
            self.done.add(idx)
            self.bytes_done += nbytes
            pct = len(self.done) / self.total_pieces * 100
            elapsed = max(1, time.time() - self._start_time)
            speed = self.bytes_done / elapsed
            print(
                f"  [{pct:5.1f}%] Piece {idx:5d} [OK]  "
                f"({len(self.done)}/{self.total_pieces})  "
                f"{self._fmt(self.bytes_done)} @ {self._fmt(speed)}/s"
            )
            if len(self.done) == self.total_pieces:
                self._stop = True

    @staticmethod
    def _fmt(n: float) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} TB"

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    def _worker(self, ip: str, port: int):
        """Worker thread: connect to one peer and download as many pieces as possible."""
        conn = None
        try:
            conn = PeerConnection(
                ip, port, self.summary["info_hash"], self.peer_id, timeout=3.0
            )
            conn.connect()
            conn.send_interested()

            # Read initial messages (BITFIELD, HAVE, UNCHOKE) for up to 15s
            conn.socket.settimeout(3.0)
            deadline = time.time() + 15.0
            while conn.peer_choked and time.time() < deadline:
                try:
                    conn.read_message()
                except Exception:
                    break
            conn.socket.settimeout(20.0)

            if conn.peer_choked:
                return  # Never unchoked

            # Download loop
            while not self._stop:
                idx = self._claim_piece(conn.bitfield)
                if idx is None:
                    break

                try:
                    data = download_piece_from_peer(
                        peer_conn=conn,
                        piece_index=idx,
                        piece_size=self._piece_size(idx),
                        expected_hash_hex=self.summary["piece_hashes"][idx],
                        max_wait_seconds=20.0,
                    )
                    self.disk_manager.write_piece(idx, data)
                    self._complete_piece(idx, len(data))

                except Exception:
                    self._release_piece(idx)
                    break  # Peer failed - retire this worker

        except Exception:
            pass
        finally:
            if conn:
                conn.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def download(self) -> bool:
        """
        Run the swarm download.
        Returns True if all pieces downloaded, False if partial.
        """
        self._start_time = time.time()

        print(
            f"\nStarting swarm: {len(self.peers)} peers | "
            f"{self.max_workers} workers | "
            f"{self.total_pieces} pieces | "
            f"saving to '{self.download_dir}/'"
        )
        print("-" * 60)

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            executor.map(lambda peer: self._worker(*peer), self.peers)

        elapsed = time.time() - self._start_time
        print("\n" + "=" * 60)
        print(f"  Downloaded : {len(self.done)}/{self.total_pieces} pieces")
        print(f"  Total data : {self._fmt(self.bytes_done)}")
        print(f"  Time       : {elapsed:.1f}s")
        if elapsed > 0 and self.bytes_done > 0:
            print(f"  Avg speed  : {self._fmt(self.bytes_done / elapsed)}/s")
        print("=" * 60)
        return len(self.done) == self.total_pieces

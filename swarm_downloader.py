import time
import threading
import queue
import collections
from concurrent.futures import ThreadPoolExecutor
from peer import PeerConnection, encode_message
from piece_downloader import download_piece_from_peer
from disk_manager import DiskManager
from tracker import get_peers, get_all_trackers


class SwarmDownloader:
    """
    Production-grade High-Performance BitTorrent Swarm Downloader.

    Key Optimizations:
    - Instant Resume: Scans existing files on disk, verifies SHA-1, skips completed pieces.
    - Persistent Connections: Streams piece after piece over healthy TCP connections without teardown.
    - Peer Health & Penalty System: Avoids dead/firewalled IPs and prioritizes responsive seeders.
    - Rarest-First Piece Selection: Dynamically tracks swarm availability to maximize download efficiency.
    - Endgame Mode: Eliminates tail latency on the last 5% of pieces via parallel redundant requests.
    - Background Swarm Re-announcer: Periodically discovers fresh peers if active swarm shrinks.
    - Multi-Block Pipelining: 64 in-flight blocks (1 MB) with immediate replenishment.
    - Dedicated Asynchronous Disk Writer: Worker threads never block on disk I/O.
    - Fast Deque-based Rolling Speed Window.
    """

    def __init__(
        self,
        summary: dict,
        peers: list,
        peer_id: bytes,
        download_dir: str = "downloads",
        max_workers: int = 40,
        on_piece_done=None,
        on_log=None,
        on_peer_status=None,
    ):
        self.summary = summary
        self.peers = list(peers)
        self.peer_id = peer_id
        self.download_dir = download_dir
        self.max_workers = max(1, min(max_workers, 60))
        self.on_piece_done = on_piece_done
        self.on_log = on_log
        self.on_peer_status = on_peer_status

        self.total_pieces = len(summary["piece_hashes"])
        self.total_length = summary["total_length"]
        self.piece_length = summary["piece_length"]

        # Disk Manager
        self.disk_manager = DiskManager(
            download_dir=self.download_dir,
            files_layout=summary.get(
                "files", [{"path": summary["name"], "length": self.total_length}]
            ),
            piece_length=self.piece_length,
            total_length=self.total_length,
        )

        # Thread-safe state
        self.lock = threading.Lock()
        self.needed = set(range(self.total_pieces))
        self.in_flight = set()
        self.done = set()
        self.bytes_done = 0
        self._stop = False
        self._start_time = 0.0
        self._speed_samples: collections.deque = collections.deque()

        # Rarest-first availability counters
        self._piece_availability: dict[int, int] = collections.defaultdict(int)

        # Peer health management
        self._peer_fails: dict[tuple[str, int], int] = collections.defaultdict(int)
        self._healthy_peers: set[tuple[str, int]] = set()
        self._active_connections = 0

        # Endgame flag
        self._endgame = False

        # Async disk write queue
        self._disk_queue: queue.Queue = queue.Queue(maxsize=128)

        # Peer queue
        self.peer_queue: queue.Queue = queue.Queue()

        # Fast resume: check if any pieces already exist on disk
        self._verify_existing()

    @property
    def queue(self):
        """Backwards compatibility for external code accessing queue."""
        with self.lock:
            return list(self.needed)

    def stop(self):
        """Signal all workers to halt immediately."""
        with self.lock:
            self._stop = True

    # ------------------------------------------------------------------
    # Fast Resume Scan
    # ------------------------------------------------------------------

    def _verify_existing(self):
        """Check already-downloaded pieces from previous runs and mark verified."""
        try:
            verified_pieces, verified_bytes = self.disk_manager.check_existing_pieces(
                self.summary["piece_hashes"]
            )
            if verified_pieces:
                self.done.update(verified_pieces)
                self.needed.difference_update(verified_pieces)
                self.bytes_done += verified_bytes
                msg = (
                    f"Resumed: {len(verified_pieces)}/{self.total_pieces} pieces "
                    f"({self._fmt(verified_bytes)}) verified on disk ✓"
                )
                print(msg)
                if self.on_log:
                    self.on_log(msg)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _piece_size(self, idx: int) -> int:
        if idx == self.total_pieces - 1:
            rem = self.total_length % self.piece_length
            return rem if rem else self.piece_length
        return self.piece_length

    def _update_availability(self, conn: PeerConnection):
        """Update piece availability counters from peer's bitfield/have set."""
        with self.lock:
            if conn.bitfield:
                for p in range(self.total_pieces):
                    if conn.has_piece(p):
                        self._piece_availability[p] += 1
            for p in conn.have_pieces:
                self._piece_availability[p] += 1

    def _claim_piece(self, conn: PeerConnection) -> int | None:
        """Thread-safe: pick a piece this peer has and mark it in-flight using rarest-first."""
        with self.lock:
            if self._stop:
                return None

            # In endgame mode, allow duplicate in-flight pieces
            if self._endgame:
                available = self.needed.copy()
            else:
                available = self.needed - self.in_flight

            if not available:
                return None

            # Filter pieces this peer announced having
            if conn.bitfield or conn.have_pieces:
                peer_available = [p for p in available if conn.has_piece(p)]
                if not peer_available:
                    return None

                # Rarest-first: sort ascending by availability count
                if self._piece_availability:
                    peer_available.sort(key=lambda p: self._piece_availability.get(p, 0))

                chosen = peer_available[0]
            else:
                chosen = next(iter(available))

            self.in_flight.add(chosen)
            return chosen

    def _release_piece(self, idx: int):
        """Return a piece to the available pool upon failure."""
        with self.lock:
            self.in_flight.discard(idx)

    def _complete_piece(self, idx: int, data: bytes):
        """Mark a piece as verified, queue async disk write, and update speed stats."""
        nbytes = len(data)

        # Queue async disk write
        try:
            self._disk_queue.put_nowait((idx, data))
        except queue.Full:
            self.disk_manager.write_piece(idx, data)

        with self.lock:
            self.needed.discard(idx)
            self.in_flight.discard(idx)
            self.done.add(idx)
            self.bytes_done += nbytes

            now = time.monotonic()
            self._speed_samples.append((now, self.bytes_done))
            cutoff = now - 5.0
            while self._speed_samples and self._speed_samples[0][0] < cutoff:
                self._speed_samples.popleft()

            if len(self._speed_samples) >= 2:
                dt = self._speed_samples[-1][0] - self._speed_samples[0][0]
                db = self._speed_samples[-1][1] - self._speed_samples[0][1]
                inst_speed = db / max(0.1, dt)
            else:
                elapsed = max(0.5, now - self._start_time)
                inst_speed = self.bytes_done / elapsed

            # Endgame mode check: activate when <= 5% remain
            remaining = len(self.needed)
            if not self._endgame and remaining <= max(5, int(self.total_pieces * 0.05)):
                self._endgame = True
                if self.on_log:
                    self.on_log(f"Entering Endgame Mode ({remaining} pieces remaining)")

            done_count = len(self.done)
            pct = done_count / self.total_pieces * 100
            msg = (
                f"  [{pct:5.1f}%] Piece {idx:5d} [OK]  "
                f"({done_count}/{self.total_pieces})  "
                f"{self._fmt(self.bytes_done)} @ {self._fmt(inst_speed)}/s"
            )
            print(msg)
            if self.on_log:
                self.on_log(msg.strip())
            if self.on_piece_done:
                self.on_piece_done(
                    idx,
                    done_count,
                    self.total_pieces,
                    self.bytes_done,
                    self.total_length,
                    inst_speed,
                )
            if done_count >= self.total_pieces:
                self._stop = True

    @staticmethod
    def _fmt(n: float) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} TB"

    # ------------------------------------------------------------------
    # Background Threads
    # ------------------------------------------------------------------

    def _disk_writer_thread(self):
        """Dedicated thread for non-blocking disk writes."""
        while True:
            try:
                item = self._disk_queue.get(timeout=1.0)
            except queue.Empty:
                if self._stop:
                    break
                continue

            if item is None:
                break

            idx, data = item
            try:
                self.disk_manager.write_piece(idx, data)
            except Exception:
                pass
            self._disk_queue.task_done()

    def _reannounce_thread(self):
        """Background thread: fetches new peers if the active pool runs low."""
        trackers = get_all_trackers(self.summary)
        while not self._stop:
            time.sleep(45.0)
            if self._stop:
                break
            with self.lock:
                if len(self.done) >= self.total_pieces:
                    break
                # Only re-announce if we need more healthy peers
                if len(self._healthy_peers) >= 20:
                    continue

            # Query trackers in parallel
            discovered = []
            d_lock = threading.Lock()

            def _q(url):
                try:
                    found = get_peers(
                        url, self.summary["info_hash"], self.total_length, self.peer_id
                    )
                    if found:
                        with d_lock:
                            for p in found:
                                if p not in discovered:
                                    discovered.append(p)
                except Exception:
                    pass

            with ThreadPoolExecutor(max_workers=min(12, len(trackers))) as pool:
                list(pool.map(_q, trackers))

            with self.lock:
                for p in discovered:
                    if p not in self.peers and self._peer_fails[p] < 2:
                        self.peers.append(p)
                        self.peer_queue.put(p)

    # ------------------------------------------------------------------
    # Worker Logic
    # ------------------------------------------------------------------

    def _worker_thread(self):
        """Worker thread: consumes peers, connects, and streams pieces."""
        while not self._stop:
            with self.lock:
                if len(self.done) >= self.total_pieces:
                    break

            try:
                ip, port = self.peer_queue.get(timeout=1.0)
            except queue.Empty:
                with self.lock:
                    if len(self.done) >= self.total_pieces:
                        break
                    # Refill peer queue with non-failed peers
                    if self.peer_queue.empty() and not self._stop and self.peers:
                        # Prioritize healthy peers first
                        for p in self._healthy_peers:
                            self.peer_queue.put(p)
                        for p in self.peers:
                            if p not in self._healthy_peers and self._peer_fails[p] < 2:
                                self.peer_queue.put(p)
                time.sleep(0.3)
                continue

            try:
                self._handle_peer(ip, port)
            except Exception:
                pass
            finally:
                self.peer_queue.task_done()

    def _handle_peer(self, ip: str, port: int):
        """Main peer lifecycle: connects, unchokes, and streams piece requests."""
        peer_key = (ip, port)
        conn = None
        if self.on_peer_status:
            self.on_peer_status(ip, port, "Connecting")

        try:
            conn = PeerConnection(
                ip, port, self.summary["info_hash"], self.peer_id, timeout=3.0
            )
            conn.connect()

            with self.lock:
                self._active_connections += 1
                self._peer_fails[peer_key] = 0

            if self.on_peer_status:
                self.on_peer_status(ip, port, "Connected (Choked)")
            conn.send_interested()

            last_keepalive = time.monotonic()
            stuck_choked_time = time.monotonic()
            pieces_from_peer = 0

            while not self._stop:
                with self.lock:
                    if len(self.done) >= self.total_pieces:
                        break

                now = time.monotonic()

                # If peer is choked, wait up to 15s for optimistic unchoke
                if conn.peer_choked:
                    if now - stuck_choked_time > 15.0:
                        # Don't waste worker; cycle to next peer
                        break

                    if now - last_keepalive > 25.0:
                        try:
                            conn.send_message(encode_message(None))
                            last_keepalive = now
                        except Exception:
                            break

                    conn.socket.settimeout(2.0)
                    try:
                        conn.read_message()
                    except (TimeoutError, OSError) as exc:
                        exc_str = str(exc).lower()
                        if "timed out" in exc_str or isinstance(exc, TimeoutError):
                            continue
                        break
                    except Exception:
                        break
                    continue

                # === Unchoked! ===
                stuck_choked_time = time.monotonic()

                if pieces_from_peer == 0:
                    self._update_availability(conn)
                    with self.lock:
                        self._healthy_peers.add(peer_key)
                    if self.on_peer_status:
                        self.on_peer_status(ip, port, "Active / Streaming")

                idx = self._claim_piece(conn)
                if idx is None:
                    # No matching pieces right now; briefly sleep
                    time.sleep(0.2)
                    retries = 0
                    while idx is None and retries < 6 and not self._stop:
                        time.sleep(0.3)
                        idx = self._claim_piece(conn)
                        retries += 1
                    if idx is None:
                        break

                try:
                    data = download_piece_from_peer(
                        peer_conn=conn,
                        piece_index=idx,
                        piece_size=self._piece_size(idx),
                        expected_hash_hex=self.summary["piece_hashes"][idx],
                        inactivity_timeout=12.0,
                        pipeline_size=64,
                    )

                    with self.lock:
                        already_done = idx in self.done
                    if not already_done:
                        self._complete_piece(idx, data)

                    pieces_from_peer += 1

                except ConnectionError:
                    # Peer choked us mid-piece; return piece to pool and wait for unchoke
                    self._release_piece(idx)
                    stuck_choked_time = time.monotonic()
                    continue

                except Exception:
                    self._release_piece(idx)
                    break

        except Exception:
            with self.lock:
                self._peer_fails[peer_key] += 1
                self._healthy_peers.discard(peer_key)

        finally:
            with self.lock:
                if conn and self._active_connections > 0:
                    self._active_connections -= 1
            if self.on_peer_status:
                self.on_peer_status(ip, port, "Disconnected")
            if conn:
                conn.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def download(self) -> bool:
        """
        Run the swarm download until completion or stopped.
        Returns True if all pieces verified on disk.
        """
        self._start_time = time.monotonic()
        self._speed_samples = collections.deque()
        self._speed_samples.append((self._start_time, self.bytes_done))

        if len(self.done) >= self.total_pieces:
            print("All pieces already verified on disk! Nothing to download.")
            return True

        for p in self.peers:
            self.peer_queue.put(p)

        n_workers = min(self.max_workers, max(len(self.peers), 1))
        print(
            f"\nStarting swarm: {len(self.peers)} peers | "
            f"{n_workers} workers | "
            f"{self.total_pieces - len(self.done)} pieces needed | "
            f"saving to '{self.download_dir}/'"
        )
        print("-" * 60)

        # Background disk writer
        disk_thread = threading.Thread(target=self._disk_writer_thread, daemon=True)
        disk_thread.start()

        # Background peer re-announcer
        reannounce_thread = threading.Thread(target=self._reannounce_thread, daemon=True)
        reannounce_thread.start()

        threads = []
        for _ in range(n_workers):
            t = threading.Thread(target=self._worker_thread, daemon=True)
            t.start()
            threads.append(t)

        while not self._stop:
            with self.lock:
                if len(self.done) >= self.total_pieces:
                    break
            time.sleep(0.2)

        # Shutdown disk writer
        self._disk_queue.put(None)

        for t in threads:
            t.join(timeout=1.0)
        disk_thread.join(timeout=2.0)

        # Flush remaining disk writes
        while not self._disk_queue.empty():
            try:
                item = self._disk_queue.get_nowait()
                if item:
                    self.disk_manager.write_piece(item[0], item[1])
            except queue.Empty:
                break

        self.disk_manager.close()

        elapsed = time.monotonic() - self._start_time
        print("\n" + "=" * 60)
        print(f"  Downloaded : {len(self.done)}/{self.total_pieces} pieces")
        print(f"  Total data : {self._fmt(self.bytes_done)}")
        print(f"  Time       : {elapsed:.1f}s")
        if elapsed > 0 and self.bytes_done > 0:
            print(f"  Avg speed  : {self._fmt(self.bytes_done / elapsed)}/s")
        print("=" * 60)
        return len(self.done) == self.total_pieces

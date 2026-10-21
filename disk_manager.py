import os
import threading
import io
import hashlib


class DiskManager:
    """
    Manages disk I/O and file assembly for single-file and multi-file BitTorrent downloads.
    Maps piece offsets across the files in the torrent layout and writes verified pieces.
    
    Optimizations:
    - Persistent file handles (avoid open/close per piece write)
    - Thread-safe writes via per-file locks (not a global lock)
    - Binary search to find overlapping file ranges
    """

    def __init__(self, download_dir: str, files_layout: list[dict], piece_length: int, total_length: int):
        self.download_dir = download_dir
        self.files_layout = files_layout
        self.piece_length = piece_length
        self.total_length = total_length

        # Calculate global start and end byte offsets for each file
        self.file_ranges = []
        current_offset = 0
        for f in self.files_layout:
            f_length = f['length']
            full_path = os.path.join(self.download_dir, f['path'])
            self.file_ranges.append({
                'path': full_path,
                'length': f_length,
                'start': current_offset,
                'end': current_offset + f_length
            })
            current_offset += f_length

        # Ensure output directory exists
        os.makedirs(self.download_dir, exist_ok=True)

        # Persistent file handles and per-file locks for concurrent writes
        self._file_handles: dict[str, io.BufferedRandom] = {}
        self._file_locks: dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()

    def _get_handle(self, path: str) -> tuple[io.BufferedRandom, threading.Lock]:
        """Returns a persistent file handle and its associated lock, creating if needed."""
        if path in self._file_handles:
            return self._file_handles[path], self._file_locks[path]

        with self._global_lock:
            # Double-check after acquiring lock
            if path in self._file_handles:
                return self._file_handles[path], self._file_locks[path]

            dir_name = os.path.dirname(path)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)

            if not os.path.exists(path):
                # Create the file
                with open(path, 'wb') as f:
                    pass

            fh = open(path, 'r+b', buffering=0)  # Unbuffered for seek+write safety
            lock = threading.Lock()
            self._file_handles[path] = fh
            self._file_locks[path] = lock
            return fh, lock

    def write_piece(self, piece_index: int, piece_data: bytes):
        """
        Writes a verified piece to the appropriate file(s) on disk.
        Uses persistent file handles and per-file locks for high concurrency.
        """
        piece_start = piece_index * self.piece_length
        piece_end = piece_start + len(piece_data)

        for item in self.file_ranges:
            # Quick skip: no overlap
            if piece_end <= item['start'] or piece_start >= item['end']:
                continue

            overlap_start = max(piece_start, item['start'])
            overlap_end = min(piece_end, item['end'])

            # Offset in the piece data
            data_start = overlap_start - piece_start
            chunk_to_write = piece_data[data_start:data_start + (overlap_end - overlap_start)]

            # Offset in the destination file
            file_seek_pos = overlap_start - item['start']

            fh, lock = self._get_handle(item['path'])
            with lock:
                fh.seek(file_seek_pos)
                fh.write(chunk_to_write)

    def read_piece(self, piece_index: int, piece_length: int) -> bytes:
        """
        Reads a piece back from disk across the file layout (useful for hash verification or seeding).
        """
        piece_start = piece_index * self.piece_length
        piece_end = min(piece_start + piece_length, self.total_length)
        
        buffer = bytearray()
        for item in self.file_ranges:
            overlap_start = max(piece_start, item['start'])
            overlap_end = min(piece_end, item['end'])
            
            if overlap_start < overlap_end:
                file_seek_pos = overlap_start - item['start']
                read_length = overlap_end - overlap_start
                
                with open(item['path'], 'rb') as f:
                    f.seek(file_seek_pos)
                    chunk = f.read(read_length)
                    buffer.extend(chunk)
                    
        return bytes(buffer)

    def check_existing_pieces(self, piece_hashes: list[str]) -> tuple[set[int], int]:
        """
        Fast scan of existing files on disk. Returns (set_of_verified_piece_indices, bytes_verified).
        Skips verification if target files don't exist yet.
        """
        verified = set()
        bytes_verified = 0

        # Check if at least one file exists
        any_exists = any(os.path.exists(item['path']) for item in self.file_ranges)
        if not any_exists:
            return verified, bytes_verified

        for idx, exp_hash in enumerate(piece_hashes):
            piece_size = self.piece_length
            if idx == len(piece_hashes) - 1:
                rem = self.total_length % self.piece_length
                piece_size = rem if rem else self.piece_length

            try:
                data = self.read_piece(idx, piece_size)
                if len(data) == piece_size and hashlib.sha1(data).hexdigest() == exp_hash.lower():
                    verified.add(idx)
                    bytes_verified += piece_size
            except Exception:
                pass

        return verified, bytes_verified

    def close(self):
        """Close all persistent file handles."""
        with self._global_lock:
            for fh in self._file_handles.values():
                try:
                    fh.close()
                except Exception:
                    pass
            self._file_handles.clear()
            self._file_locks.clear()

    def __del__(self):
        self.close()

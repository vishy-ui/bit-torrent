import hashlib
import time
import socket
from peer import PeerConnection, MSG_PIECE, MSG_UNCHOKE, MSG_CHOKE, MSG_HAVE, MSG_BITFIELD, MSG_EXTENDED

DEFAULT_BLOCK_SIZE = 16384  # 16 KB standard BitTorrent block size


def verify_piece(piece_bytes: bytes, expected_hash_hex: str) -> bool:
    """Verifies SHA-1 hash of downloaded piece bytes against expected hex string."""
    return hashlib.sha1(piece_bytes).hexdigest() == expected_hash_hex.lower()


def peer_has_piece(bitfield: bytes, piece_index: int) -> bool:
    """Checks if a peer has a specific piece index given their bitfield bytes."""
    if not bitfield:
        return False
    byte_idx = piece_index // 8
    bit_idx = 7 - (piece_index % 8)
    if byte_idx < len(bitfield):
        return bool((bitfield[byte_idx] >> bit_idx) & 1)
    return False


def download_piece_from_peer(
    peer_conn: PeerConnection,
    piece_index: int,
    piece_size: int,
    expected_hash_hex: str,
    block_size: int = DEFAULT_BLOCK_SIZE,
    inactivity_timeout: float = 12.0,
    pipeline_size: int = 64,
) -> bytes:
    """
    Downloads a single piece from a peer by pipelining block requests,
    handling packet drops with smart retransmission, and verifying SHA-1.

    Optimizations applied:
    - 64-block pipeline window (1 MB in-flight) for maximum throughput
    - Zero-stall immediate pipeline replenishment upon receiving each block
    - Tight 1.5s socket timeout loop
    - 2.0s retransmission on stalled blocks
    - Preallocated bytearray buffer with direct slice assignment
    - Fast-path PIECE message unpacking
    """
    if not peer_conn.socket:
        raise ConnectionError("Peer socket is closed")

    peer_conn.socket.settimeout(1.5)

    # 1. Express interest to peer if not already
    if not peer_conn.am_interested:
        peer_conn.send_interested()

    # 2. Prepare block request offsets
    num_blocks = (piece_size + block_size - 1) // block_size
    blocks = []
    offset = 0
    for _ in range(num_blocks):
        curr_len = min(block_size, piece_size - offset)
        blocks.append((offset, curr_len))
        offset += curr_len

    total_blocks = len(blocks)
    received_count = 0
    received_set = set()
    pending_blocks = list(blocks)
    requested_blocks: dict[int, tuple[int, float]] = {}  # offset -> (len, req_time)

    # Preallocate assembly buffer
    assembled = bytearray(piece_size)
    last_data_time = time.monotonic()

    def _replenish_pipeline(cur_time: float):
        """Helper to keep in-flight request window filled up to pipeline_size."""
        if peer_conn.peer_choked or not pending_blocks:
            return
        to_send = []
        while pending_blocks and len(requested_blocks) < pipeline_size:
            b_offset, b_len = pending_blocks.pop(0)
            requested_blocks[b_offset] = (b_len, cur_time)
            to_send.append((piece_index, b_offset, b_len))
        if to_send:
            peer_conn.send_requests_batch(to_send)

    # Initial pipeline prime
    _replenish_pipeline(last_data_time)

    while received_count < total_blocks:
        now = time.monotonic()

        # Inactivity check
        if now - last_data_time > inactivity_timeout:
            raise TimeoutError(
                f"Peer stalled on piece {piece_index}: no data for {inactivity_timeout}s "
                f"({received_count}/{total_blocks} blocks)"
            )

        # Retransmit stalled requests (threshold: 2.0s)
        if requested_blocks:
            stalled = [
                bo for bo, (_, req_t) in requested_blocks.items() if now - req_t > 2.0
            ]
            if stalled:
                for bo in stalled:
                    blen, _ = requested_blocks.pop(bo)
                    pending_blocks.append((bo, blen))

        # Keep pipeline full
        _replenish_pipeline(now)

        try:
            msg = peer_conn.read_message()
        except socket.timeout:
            continue
        except OSError:
            # Handle "timed out" on Windows
            continue

        msg_id = msg['id']

        if msg_id == MSG_PIECE:
            if msg.get('index') == piece_index:
                b_offset = msg.get('begin')
                block_data = msg.get('block', b'')
                if b_offset not in received_set:
                    # Write directly into preallocated buffer
                    b_len = len(block_data)
                    assembled[b_offset:b_offset + b_len] = block_data
                    received_set.add(b_offset)
                    received_count += 1
                    requested_blocks.pop(b_offset, None)
                    last_data_time = time.monotonic()
                    # Immediately replenish pipeline to keep bandwidth saturated
                    _replenish_pipeline(last_data_time)

        elif msg_id == MSG_UNCHOKE:
            # Peer unchoked us! Re-queue any in-flight requests and immediately prime
            for bo, (blen, _) in list(requested_blocks.items()):
                pending_blocks.insert(0, (bo, blen))
            requested_blocks.clear()
            _replenish_pipeline(time.monotonic())

        elif msg_id == MSG_CHOKE:
            # Peer choked us; put requested blocks back into pending
            for bo, (blen, _) in list(requested_blocks.items()):
                pending_blocks.insert(0, (bo, blen))
            requested_blocks.clear()
            raise ConnectionError(f"Peer choked during piece {piece_index}")

        elif msg_id in (MSG_HAVE, MSG_BITFIELD, MSG_EXTENDED):
            pass

    # 3. Verify piece hash
    piece_bytes = bytes(assembled)
    if not verify_piece(piece_bytes, expected_hash_hex):
        raise ValueError(
            f"Piece {piece_index} SHA-1 hash mismatch! "
            f"Expected {expected_hash_hex}, got {hashlib.sha1(piece_bytes).hexdigest()}"
        )

    return piece_bytes

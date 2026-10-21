import hashlib
import time
import socket
from peer import PeerConnection, MSG_PIECE, MSG_UNCHOKE, MSG_CHOKE, MSG_HAVE, MSG_BITFIELD

DEFAULT_BLOCK_SIZE = 16384  # 16 KB standard BitTorrent block size

def verify_piece(piece_bytes: bytes, expected_hash_hex: str) -> bool:
    """Verifies SHA-1 hash of downloaded piece bytes against expected hex string."""
    computed_hash = hashlib.sha1(piece_bytes).hexdigest()
    return computed_hash.lower() == expected_hash_hex.lower()


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
    max_wait_seconds: float = 30.0
) -> bytes:
    """
    Downloads a single piece from a peer by requesting 16KB blocks,
    assembling them, and verifying against the expected SHA-1 hash.
    """
    peer_conn.socket.settimeout(max_wait_seconds)

    # 1. Express interest to peer
    if not peer_conn.am_interested:
        peer_conn.send_interested()

    # 2. Prepare block request offsets
    blocks = []
    offset = 0
    while offset < piece_size:
        curr_len = min(block_size, piece_size - offset)
        blocks.append((offset, curr_len))
        offset += curr_len

    received_blocks = {}
    pending_blocks = list(blocks)
    requested_blocks = set()

    pipeline_size = 5  # Number of pipelined requests in flight
    start_time = time.time()

    while len(received_blocks) < len(blocks):
        if time.time() - start_time > max_wait_seconds:
            raise TimeoutError(f"Timed out downloading piece {piece_index} (received {len(received_blocks)}/{len(blocks)} blocks)")

        # If unchoked, pipeline requests
        if not peer_conn.peer_choked:
            while pending_blocks and len(requested_blocks) < pipeline_size:
                b_offset, b_len = pending_blocks.pop(0)
                requested_blocks.add(b_offset)
                peer_conn.send_request(piece_index, b_offset, b_len)

        try:
            msg = peer_conn.read_message()
        except socket.timeout:
            continue

        msg_id = msg['id']

        if msg_id == MSG_PIECE:
            if msg.get('index') == piece_index:
                b_offset = msg.get('begin')
                block_data = msg.get('block', b'')
                if b_offset in requested_blocks or b_offset not in received_blocks:
                    received_blocks[b_offset] = block_data
                    requested_blocks.discard(b_offset)

        elif msg_id == MSG_UNCHOKE:
            # Re-queue any in-flight requests that might have dropped
            for b_offset in list(requested_blocks):
                # find length
                for bo, bl in blocks:
                    if bo == b_offset:
                        pending_blocks.insert(0, (bo, bl))
                        break
            requested_blocks.clear()

        elif msg_id == MSG_CHOKE:
            # Peer choked us; put requested blocks back into pending
            for b_offset in list(requested_blocks):
                for bo, bl in blocks:
                    if bo == b_offset:
                        pending_blocks.insert(0, (bo, bl))
                        break
            requested_blocks.clear()

        elif msg_id == MSG_HAVE:
            pass  # Peer announcing piece possession

        elif msg_id == MSG_BITFIELD:
            pass  # Peer sent full bitfield

    # 3. Assemble piece bytes in sequential order
    assembled = bytearray()
    for b_offset, b_len in blocks:
        if b_offset not in received_blocks:
            raise ValueError(f"Missing block at offset {b_offset} for piece {piece_index}")
        assembled.extend(received_blocks[b_offset])

    piece_bytes = bytes(assembled)

    # 4. Verify piece hash
    if not verify_piece(piece_bytes, expected_hash_hex):
        raise ValueError(
            f"Piece {piece_index} SHA-1 hash mismatch! "
            f"Expected {expected_hash_hex}, got {hashlib.sha1(piece_bytes).hexdigest()}"
        )

    return piece_bytes


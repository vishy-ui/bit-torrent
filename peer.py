import socket
import struct

HANDSHAKE_PSTR = b"BitTorrent protocol"
HANDSHAKE_LENGTH = 68

# Message IDs
MSG_CHOKE = 0
MSG_UNCHOKE = 1
MSG_INTERESTED = 2
MSG_NOT_INTERESTED = 3
MSG_HAVE = 4
MSG_BITFIELD = 5
MSG_REQUEST = 6
MSG_PIECE = 7
MSG_CANCEL = 8
MSG_PORT = 9
MSG_EXTENDED = 20

MSG_NAMES = {
    MSG_CHOKE: "choke",
    MSG_UNCHOKE: "unchoke",
    MSG_INTERESTED: "interested",
    MSG_NOT_INTERESTED: "not_interested",
    MSG_HAVE: "have",
    MSG_BITFIELD: "bitfield",
    MSG_REQUEST: "request",
    MSG_PIECE: "piece",
    MSG_CANCEL: "cancel",
    MSG_PORT: "port",
    MSG_EXTENDED: "extended",
}

# Pre-build frequently used immutable messages (avoid re-encoding each time)
_MSG_INTERESTED = struct.pack("!IB", 1, MSG_INTERESTED)
_MSG_NOT_INTERESTED = struct.pack("!IB", 1, MSG_NOT_INTERESTED)
_MSG_KEEPALIVE = struct.pack("!I", 0)
_MSG_CHOKE_BYTES = struct.pack("!IB", 1, MSG_CHOKE)
_MSG_UNCHOKE_BYTES = struct.pack("!IB", 1, MSG_UNCHOKE)

# Pre-compute handshake prefix (everything before info_hash + peer_id)
_HANDSHAKE_PREFIX = bytes([len(HANDSHAKE_PSTR)]) + HANDSHAKE_PSTR + b'\x00' * 8


def make_handshake(info_hash: bytes, peer_id: bytes) -> bytes:
    """Constructs a 68-byte BitTorrent handshake packet."""
    return _HANDSHAKE_PREFIX + info_hash + peer_id


def parse_handshake(data: bytes) -> dict:
    """Parses a 68-byte BitTorrent handshake packet."""
    if len(data) < HANDSHAKE_LENGTH:
        raise ValueError(f"Handshake data too short: expected {HANDSHAKE_LENGTH} bytes, got {len(data)}")
    
    pstr_len = data[0]
    pstr = data[1:1 + pstr_len]
    
    if pstr != HANDSHAKE_PSTR:
        raise ValueError(f"Invalid protocol string: {pstr}")
        
    reserved = data[1 + pstr_len:1 + pstr_len + 8]
    info_hash = data[1 + pstr_len + 8:1 + pstr_len + 8 + 20]
    peer_id = data[1 + pstr_len + 8 + 20:1 + pstr_len + 8 + 20 + 20]
    
    return {
        'pstr': pstr,
        'reserved': reserved,
        'info_hash': info_hash,
        'peer_id': peer_id
    }


def encode_message(msg_id: int | None, payload: bytes = b'') -> bytes:
    """Encodes a message into BitTorrent peer wire format."""
    if msg_id is None:
        return _MSG_KEEPALIVE
    
    length = 1 + len(payload)
    return struct.pack("!IB", length, msg_id) + payload


def create_choke_msg() -> bytes:
    return _MSG_CHOKE_BYTES

def create_unchoke_msg() -> bytes:
    return _MSG_UNCHOKE_BYTES

def create_interested_msg() -> bytes:
    return _MSG_INTERESTED

def create_not_interested_msg() -> bytes:
    return _MSG_NOT_INTERESTED

def create_have_msg(piece_index: int) -> bytes:
    return encode_message(MSG_HAVE, struct.pack("!I", piece_index))

def create_request_msg(index: int, begin: int, length: int) -> bytes:
    return struct.pack("!IBIII", 13, MSG_REQUEST, index, begin, length)

def create_piece_msg(index: int, begin: int, block: bytes) -> bytes:
    return encode_message(MSG_PIECE, struct.pack("!II", index, begin) + block)

def create_cancel_msg(index: int, begin: int, length: int) -> bytes:
    return struct.pack("!IBIII", 13, MSG_CANCEL, index, begin, length)

def create_port_msg(listen_port: int) -> bytes:
    return encode_message(MSG_PORT, struct.pack("!H", listen_port))


def parse_message(data: bytes) -> dict:
    """Parses message body (message ID + payload) into a dictionary.
    Optimized: fast-path for PIECE messages (the hot path during download)."""
    if not data:
        return {'id': None, 'name': 'keep-alive', 'payload': b''}
    
    msg_id = data[0]

    # Fast path: PIECE is the most common message during download
    if msg_id == MSG_PIECE and len(data) >= 9:
        idx, begin = struct.unpack("!II", data[1:9])
        return {
            'id': MSG_PIECE,
            'name': 'piece',
            'payload': data[1:],
            'index': idx,
            'begin': begin,
            'block': data[9:],
        }

    payload = data[1:]
    name = MSG_NAMES.get(msg_id, "unknown")
    
    result = {
        'id': msg_id,
        'name': name,
        'payload': payload
    }
    
    if msg_id == MSG_HAVE:
        if len(payload) == 4:
            result['piece_index'] = struct.unpack("!I", payload)[0]
    elif msg_id == MSG_BITFIELD:
        result['bitfield'] = payload
    elif msg_id == MSG_REQUEST:
        if len(payload) == 12:
            idx, begin, length = struct.unpack("!III", payload)
            result['index'] = idx
            result['begin'] = begin
            result['length'] = length
    elif msg_id == MSG_CANCEL:
        if len(payload) == 12:
            idx, begin, length = struct.unpack("!III", payload)
            result['index'] = idx
            result['begin'] = begin
            result['length'] = length
    elif msg_id == MSG_PORT:
        if len(payload) == 2:
            result['listen_port'] = struct.unpack("!H", payload)[0]
            
    return result


class PeerConnection:
    """Manages a TCP peer connection under BitTorrent Peer Wire Protocol."""
    
    def __init__(self, ip: str, port: int, info_hash: bytes, peer_id: bytes, timeout: float = 5.0):
        self.ip = ip
        self.port = port
        self.info_hash = info_hash
        self.peer_id = peer_id
        self.timeout = timeout
        self.socket = None
        self.peer_handshake = None
        self.peer_choked = True
        self.peer_interested = False
        self.am_choked = True
        self.am_interested = False
        self.bitfield = None
        self.have_pieces = set()
        # Reusable 4-byte buffer for reading message lengths
        self._len_buf = bytearray(4)
        self._len_view = memoryview(self._len_buf)

    def has_piece(self, piece_index: int) -> bool:
        """Checks if peer has announced this piece via HAVE or BITFIELD."""
        if piece_index in self.have_pieces:
            return True
        if self.bitfield:
            byte_idx = piece_index // 8
            bit_idx = 7 - (piece_index % 8)
            if byte_idx < len(self.bitfield):
                return bool((self.bitfield[byte_idx] >> bit_idx) & 1)
        return False

    def connect(self):
        """Establishes TCP connection and performs BitTorrent Handshake."""
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Performance tuning: disable Nagle's algorithm and increase buffers
        try:
            self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2097152)  # 2 MB
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 524288)   # 512 KB
        except Exception:
            pass
        self.socket.settimeout(self.timeout)
        self.socket.connect((self.ip, self.port))
        
        # Send Handshake
        self.socket.sendall(make_handshake(self.info_hash, self.peer_id))
        
        # Read Handshake Response
        resp_bytes = self._read_exact(HANDSHAKE_LENGTH)
        self.peer_handshake = parse_handshake(resp_bytes)
        
        # Verify Info Hash
        if self.peer_handshake['info_hash'] != self.info_hash:
            raise ValueError(
                f"Info hash mismatch: expected {self.info_hash.hex()}, got {self.peer_handshake['info_hash'].hex()}"
            )

    def _read_exact(self, n: int) -> bytes:
        """Helper to read exactly n bytes from socket using preallocated buffer."""
        buf = bytearray(n)
        view = memoryview(buf)
        pos = 0
        while pos < n:
            chunk_size = self.socket.recv_into(view[pos:])
            if not chunk_size:
                raise ConnectionError(f"Connection closed by peer while trying to read {n} bytes.")
            pos += chunk_size
        return bytes(buf)

    def _read_into_reusable(self, buf: bytearray, view: memoryview, n: int):
        """Reads exactly n bytes into a reusable preallocated buffer."""
        pos = 0
        while pos < n:
            chunk_size = self.socket.recv_into(view[pos:n])
            if not chunk_size:
                raise ConnectionError("Connection closed by peer.")
            pos += chunk_size

    def send_message(self, msg_bytes: bytes):
        """Sends raw encoded message to peer."""
        if not self.socket:
            raise ConnectionError("Socket is not connected.")
        self.socket.sendall(msg_bytes)

    def read_message(self) -> dict:
        """Reads next message from peer wire protocol.
        Optimized with reusable length buffer to avoid allocation per message."""
        # Read 4-byte length prefix into reusable buffer
        self._read_into_reusable(self._len_buf, self._len_view, 4)
        length = struct.unpack("!I", self._len_buf)[0]
        
        if length == 0:
            return {'id': None, 'name': 'keep-alive', 'payload': b''}
            
        message_bytes = self._read_exact(length)
        parsed = parse_message(message_bytes)
        
        # Update internal connection state based on message type
        msg_id = parsed['id']
        if msg_id == MSG_CHOKE:
            self.peer_choked = True
        elif msg_id == MSG_UNCHOKE:
            self.peer_choked = False
        elif msg_id == MSG_INTERESTED:
            self.peer_interested = True
        elif msg_id == MSG_NOT_INTERESTED:
            self.peer_interested = False
        elif msg_id == MSG_BITFIELD:
            self.bitfield = parsed.get('bitfield')
        elif msg_id == MSG_HAVE:
            p_idx = parsed.get('piece_index')
            if p_idx is not None:
                self.have_pieces.add(p_idx)
            
        return parsed

    def send_interested(self):
        """Sends 'interested' message to peer."""
        self.send_message(_MSG_INTERESTED)
        self.am_interested = True

    def send_not_interested(self):
        """Sends 'not_interested' message to peer."""
        self.send_message(_MSG_NOT_INTERESTED)
        self.am_interested = False

    def send_request(self, index: int, begin: int, length: int):
        """Sends block request to peer."""
        self.send_message(create_request_msg(index, begin, length))

    def send_requests_batch(self, requests: list[tuple[int, int, int]]):
        """Sends multiple block requests in a single TCP write."""
        if not self.socket or not requests:
            return
        batch = b"".join(
            struct.pack("!IBIII", 13, MSG_REQUEST, idx, begin, length)
            for idx, begin, length in requests
        )
        self.socket.sendall(batch)

    def close(self):
        """Closes TCP connection."""
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass
            self.socket = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

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
}


def make_handshake(info_hash: bytes, peer_id: bytes) -> bytes:
    """Constructs a 68-byte BitTorrent handshake packet."""
    if len(info_hash) != 20:
        raise ValueError("info_hash must be exactly 20 bytes")
    if len(peer_id) != 20:
        raise ValueError("peer_id must be exactly 20 bytes")
    
    pstr_len = bytes([len(HANDSHAKE_PSTR)])
    reserved = b'\x00' * 8
    return pstr_len + HANDSHAKE_PSTR + reserved + info_hash + peer_id


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
        # Keep-alive message
        return struct.pack("!I", 0)
    
    length = 1 + len(payload)
    return struct.pack("!IB", length, msg_id) + payload


def create_choke_msg() -> bytes:
    return encode_message(MSG_CHOKE)

def create_unchoke_msg() -> bytes:
    return encode_message(MSG_UNCHOKE)

def create_interested_msg() -> bytes:
    return encode_message(MSG_INTERESTED)

def create_not_interested_msg() -> bytes:
    return encode_message(MSG_NOT_INTERESTED)

def create_have_msg(piece_index: int) -> bytes:
    return encode_message(MSG_HAVE, struct.pack("!I", piece_index))

def create_request_msg(index: int, begin: int, length: int) -> bytes:
    return encode_message(MSG_REQUEST, struct.pack("!III", index, begin, length))

def create_piece_msg(index: int, begin: int, block: bytes) -> bytes:
    return encode_message(MSG_PIECE, struct.pack("!II", index, begin) + block)

def create_cancel_msg(index: int, begin: int, length: int) -> bytes:
    return encode_message(MSG_CANCEL, struct.pack("!III", index, begin, length))

def create_port_msg(listen_port: int) -> bytes:
    return encode_message(MSG_PORT, struct.pack("!H", listen_port))


def parse_message(data: bytes) -> dict:
    """Parses message body (message ID + payload) into a dictionary."""
    if not data:
        return {'id': None, 'name': 'keep-alive', 'payload': b''}
    
    msg_id = data[0]
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
    elif msg_id == MSG_PIECE:
        if len(payload) >= 8:
            idx, begin = struct.unpack("!II", payload[:8])
            result['index'] = idx
            result['begin'] = begin
            result['block'] = payload[8:]
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

    def connect(self):
        """Establishes TCP connection and performs BitTorrent Handshake."""
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(self.timeout)
        self.socket.connect((self.ip, self.port))
        
        # Send Handshake
        handshake_msg = make_handshake(self.info_hash, self.peer_id)
        self.socket.sendall(handshake_msg)
        
        # Read Handshake Response
        resp_bytes = self._read_exact(HANDSHAKE_LENGTH)
        self.peer_handshake = parse_handshake(resp_bytes)
        
        # Verify Info Hash
        if self.peer_handshake['info_hash'] != self.info_hash:
            raise ValueError(
                f"Info hash mismatch: expected {self.info_hash.hex()}, got {self.peer_handshake['info_hash'].hex()}"
            )

    def _read_exact(self, n: int) -> bytes:
        """Helper to read exactly n bytes from socket."""
        buffer = bytearray()
        while len(buffer) < n:
            chunk = self.socket.recv(n - len(buffer))
            if not chunk:
                raise ConnectionError(f"Connection closed by peer while trying to read {n} bytes.")
            buffer.extend(chunk)
        return bytes(buffer)

    def send_message(self, msg_bytes: bytes):
        """Sends raw encoded message to peer."""
        if not self.socket:
            raise ConnectionError("Socket is not connected.")
        self.socket.sendall(msg_bytes)

    def read_message(self) -> dict:
        """Reads next message from peer wire protocol."""
        length_bytes = self._read_exact(4)
        length = struct.unpack("!I", length_bytes)[0]
        
        if length == 0:
            return {'id': None, 'name': 'keep-alive', 'payload': b''}
            
        message_bytes = self._read_exact(length)
        parsed = parse_message(message_bytes)
        
        # Update internal connection state based on message type
        if parsed['id'] == MSG_CHOKE:
            self.peer_choked = True
        elif parsed['id'] == MSG_UNCHOKE:
            self.peer_choked = False
        elif parsed['id'] == MSG_INTERESTED:
            self.peer_interested = True
        elif parsed['id'] == MSG_NOT_INTERESTED:
            self.peer_interested = False
        elif parsed['id'] == MSG_BITFIELD:
            self.bitfield = parsed.get('bitfield')
            
        return parsed

    def send_interested(self):
        """Sends 'interested' message to peer."""
        self.send_message(create_interested_msg())
        self.am_interested = True

    def send_not_interested(self):
        """Sends 'not_interested' message to peer."""
        self.send_message(create_not_interested_msg())
        self.am_interested = False

    def send_request(self, index: int, begin: int, length: int):
        """Sends block request to peer."""
        self.send_message(create_request_msg(index, begin, length))

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

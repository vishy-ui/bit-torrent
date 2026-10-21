import urllib.parse
import urllib.request
import random
import string
import socket
import struct
from parser import bdecode

# Curated Tier-1 public trackers to augment torrents that have offline or few trackers
PUBLIC_FALLBACK_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://explodie.org:6969/announce",
    "udp://tracker.dler.org:6969/announce",
    "udp://p4p.arenabg.com:1337/announce",
    "http://tracker.acgnx.se/announce",
]


def is_valid_peer_ip(ip: str) -> bool:
    """Filters out invalid, localhost, multicast, and private RFC-1918 IPs."""
    if not ip or not isinstance(ip, str):
        return False
    parts = ip.split('.')
    if len(parts) != 4:
        return False
    try:
        octets = [int(p) for p in parts]
    except ValueError:
        return False
    if any(o < 0 or o > 255 for o in octets):
        return False

    o1, o2 = octets[0], octets[1]
    # 0.0.0.0/8, 127.0.0.0/8 (loopback)
    if o1 == 0 or o1 == 127:
        return False
    # 10.0.0.0/8, 192.168.0.0/16, 169.254.0.0/16
    if o1 == 10 or (o1 == 192 and o2 == 168) or (o1 == 169 and o2 == 254):
        return False
    # 172.16.0.0/12
    if o1 == 172 and (16 <= o2 <= 31):
        return False
    # 224.0.0.0/4 (multicast / reserved)
    if o1 >= 224:
        return False
    return True


def generate_peer_id() -> bytes:
    """Generates a standard 20-byte peer ID formatted like modern BitTorrent clients."""
    random_chars = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
    return f"-AG0100-{random_chars}".encode('utf-8')


def get_all_trackers(summary: dict) -> list[str]:
    """
    Returns a unified, deduplicated list of trackers combining:
    1. The primary announce URL
    2. Any announce-list tiers
    3. Reliable public fallback trackers
    """
    trackers = []
    seen = set()

    def _add(url: str):
        if not url:
            return
        clean = url.strip()
        if not clean or clean in seen:
            return
        # Normalize trailing slash if needed
        seen.add(clean)
        trackers.append(clean)

    # 1. Primary announce
    _add(summary.get("announce", ""))

    # 2. Announce-list tiers
    for t in summary.get("announce_list") or []:
        _add(t)

    # 3. Fallback public trackers
    for fb in PUBLIC_FALLBACK_TRACKERS:
        _add(fb)

    return trackers


def parse_compact_peers(peers_binary) -> list[tuple[str, int]]:
    """
    Parses compact 6-byte peer representations (4-bytes IP, 2-bytes Port).
    Filters out invalid and private IPs.
    """
    peers = []
    if isinstance(peers_binary, list):
        for p in peers_binary:
            try:
                ip = p.get(b'ip', b'').decode('utf-8')
                port = p.get(b'port', 0)
                if ip and port > 0 and is_valid_peer_ip(ip):
                    peers.append((ip, port))
            except Exception:
                continue
        return peers

    if not isinstance(peers_binary, (bytes, bytearray)):
        return peers

    n = len(peers_binary)
    for i in range(0, n - 5, 6):
        chunk = peers_binary[i:i + 6]
        ip = f"{chunk[0]}.{chunk[1]}.{chunk[2]}.{chunk[3]}"
        port = (chunk[4] << 8) | chunk[5]
        if port > 0 and is_valid_peer_ip(ip):
            peers.append((ip, port))
    return peers


def get_peers_from_http_tracker(announce_url: str, info_hash_bytes: bytes, total_length: int, peer_id: bytes, port: int = 6881) -> list[tuple[str, int]]:
    """Fetches peer list from an HTTP/HTTPS tracker with numwant=200."""
    params = {
        'info_hash': info_hash_bytes,
        'peer_id': peer_id,
        'port': port,
        'uploaded': 0,
        'downloaded': 0,
        'left': total_length,
        'compact': 1,
        'event': 'started',
        'numwant': 200,
    }

    url = announce_url + ('&' if '?' in announce_url else '?') + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': 'uTorrent/3550'})

    with urllib.request.urlopen(req, timeout=5) as response:
        response_bytes = response.read()

    tracker_dict = bdecode(response_bytes)

    if b'failure reason' in tracker_dict:
        raise Exception(f"Tracker error: {tracker_dict[b'failure reason'].decode('utf-8', 'ignore')}")

    return parse_compact_peers(tracker_dict.get(b'peers', b''))


def get_peers_from_udp_tracker(announce_url: str, info_hash_bytes: bytes, total_length: int, peer_id: bytes, port: int = 6881) -> list[tuple[str, int]]:
    """Fetches peer list from a UDP tracker using binary socket packets."""
    parsed = urllib.parse.urlparse(announce_url)
    host = parsed.hostname
    if not host:
        return []
    udp_port = parsed.port or 80

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(3.5)

    try:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
        except Exception:
            pass

        # Step 1: Connect Request
        protocol_id = 0x41727101980
        action_connect = 0
        transaction_id = random.randint(0, 2**31 - 1)

        req = struct.pack("!QII", protocol_id, action_connect, transaction_id)
        sock.sendto(req, (host, udp_port))

        resp, _ = sock.recvfrom(65536)
        if len(resp) < 16:
            return []

        resp_action, resp_trans_id = struct.unpack("!II", resp[:8])
        connection_id = resp[8:16]
        if resp_trans_id != transaction_id or resp_action != 0:
            return []

        # Step 2: Announce Request
        action_announce = 1
        transaction_id = random.randint(0, 2**31 - 1)
        key = random.randint(0, 2**31 - 1)

        req = (
            connection_id +
            struct.pack("!II", action_announce, transaction_id) +
            info_hash_bytes +
            peer_id +
            struct.pack("!QQQ", 0, total_length, 0) +  # downloaded, left, uploaded
            struct.pack("!I", 2) +                      # event: started
            struct.pack("!I", 0) +                      # IP address (0 for default)
            struct.pack("!I", key) +                    # key
            struct.pack("!i", 200) +                    # num_want: request 200 peers
            struct.pack("!H", port)                     # port
        )
        sock.sendto(req, (host, udp_port))

        resp, _ = sock.recvfrom(65536)
        if len(resp) < 20:
            return []

        resp_action, resp_trans_id = struct.unpack("!II", resp[:8])
        if resp_trans_id != transaction_id or resp_action != 1:
            return []

        return parse_compact_peers(resp[20:])

    finally:
        sock.close()


def get_peers(announce_url: str, info_hash_bytes: bytes, total_length: int, peer_id: bytes, port: int = 6881) -> list[tuple[str, int]]:
    """Main entrypoint to get peers from HTTP/HTTPS or UDP tracker."""
    if announce_url.startswith('http'):
        return get_peers_from_http_tracker(announce_url, info_hash_bytes, total_length, peer_id, port)
    elif announce_url.startswith('udp'):
        return get_peers_from_udp_tracker(announce_url, info_hash_bytes, total_length, peer_id, port)
    else:
        raise ValueError(f"Unsupported tracker protocol: {announce_url}")

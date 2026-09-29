import urllib.parse
import urllib.request
import random
import string
import socket
from parser import bdecode

def generate_peer_id():
    """Generates a standard 20-byte peer ID."""
    random_chars = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
    return f"-PY0001-{random_chars}".encode('utf-8')

def parse_compact_peers(peers_binary):
    """Parses compact 6-byte peer representations (4-bytes IP, 2-bytes Port)."""
    peers = []
    if isinstance(peers_binary, list):
        for p in peers_binary:
            ip = p.get(b'ip', b'').decode('utf-8')
            port = p.get(b'port', 0)
            if ip and port:
                peers.append((ip, port))
        return peers

    for i in range(0, len(peers_binary), 6):
        chunk = peers_binary[i:i+6]
        if len(chunk) == 6:
            ip = ".".join(str(b) for b in chunk[:4])
            port = int.from_bytes(chunk[4:6], byteorder='big')
            peers.append((ip, port))
    return peers

def get_peers_from_http_tracker(announce_url, info_hash_bytes, total_length, peer_id, port=6881):
    """Fetches peer list from an HTTP/HTTPS tracker."""
    params = {
        'info_hash': info_hash_bytes,
        'peer_id': peer_id,
        'port': port,
        'uploaded': 0,
        'downloaded': 0,
        'left': total_length,
        'compact': 1,
        'event': 'started'
    }

    url = announce_url + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': 'Python-TorrentClient/1.0'})
    
    with urllib.request.urlopen(req, timeout=10) as response:
        response_bytes = response.read()
    
    tracker_dict = bdecode(response_bytes)
    
    if b'failure reason' in tracker_dict:
        raise Exception(f"Tracker error: {tracker_dict[b'failure reason'].decode('utf-8', 'ignore')}")

    return parse_compact_peers(tracker_dict.get(b'peers', b''))

def get_peers_from_udp_tracker(announce_url, info_hash_bytes, total_length, peer_id, port=6881):
    """Fetches peer list from a UDP tracker using binary socket packets."""
    parsed = urllib.parse.urlparse(announce_url)
    host = parsed.hostname
    udp_port = parsed.port or 80

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(5)

    try:
        # Step 1: Connect Request
        protocol_id = 0x41727101980
        action_connect = 0
        transaction_id = random.randint(0, 2**31 - 1)
        
        req = protocol_id.to_bytes(8, 'big') + action_connect.to_bytes(4, 'big') + transaction_id.to_bytes(4, 'big')
        sock.sendto(req, (host, udp_port))
        
        resp, _ = sock.recvfrom(2048)
        if len(resp) < 16:
            return []
        
        resp_trans_id = int.from_bytes(resp[4:8], byteorder='big')
        connection_id = resp[8:16]
        if resp_trans_id != transaction_id:
            return []

        # Step 2: Announce Request
        action_announce = 1
        transaction_id = random.randint(0, 2**31 - 1)
        
        req = (
            connection_id +
            action_announce.to_bytes(4, 'big') +
            transaction_id.to_bytes(4, 'big') +
            info_hash_bytes +
            peer_id +
            (0).to_bytes(8, 'big') +            # downloaded
            total_length.to_bytes(8, 'big') +    # left
            (0).to_bytes(8, 'big') +            # uploaded
            (2).to_bytes(4, 'big') +            # event: started
            (0).to_bytes(4, 'big') +            # IP address (0 for default)
            random.randint(0, 2**31 - 1).to_bytes(4, 'big') + # key
            (-1).to_bytes(4, 'big', signed=True) +             # num_want (-1 default)
            port.to_bytes(2, 'big')
        )
        sock.sendto(req, (host, udp_port))

        resp, _ = sock.recvfrom(2048)
        if len(resp) < 20:
            return []

        resp_trans_id = int.from_bytes(resp[4:8], byteorder='big')
        if resp_trans_id != transaction_id:
            return []

        return parse_compact_peers(resp[20:])

    finally:
        sock.close()

def get_peers(announce_url, info_hash_bytes, total_length, peer_id, port=6881):
    """Main function to get peers from HTTP or UDP tracker."""
    if announce_url.startswith('http'):
        return get_peers_from_http_tracker(announce_url, info_hash_bytes, total_length, peer_id, port)
    elif announce_url.startswith('udp'):
        return get_peers_from_udp_tracker(announce_url, info_hash_bytes, total_length, peer_id, port)
    else:
        raise ValueError(f"Unsupported tracker protocol: {announce_url}")



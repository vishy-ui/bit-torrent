from parser import bdecode, bencode
import hashlib

def calculate_info_hash(info_dict):
    """Calculates SHA-1 hash of bencoded info dictionary."""
    return hashlib.sha1(bencode(info_dict)).digest()

def extract_piece_hashes(pieces_bytes):
    """Chunks pieces binary into 20-byte SHA-1 hex strings."""
    return [pieces_bytes[i:i+20].hex() for i in range(0, len(pieces_bytes), 20)]

def get_torrent_summary(torrent_file_path):
    """Parses a torrent file and extracts metadata."""
    with open(torrent_file_path, 'rb') as f:
        torrent_data = f.read()
    
    decoded = bdecode(torrent_data)
    info_dict = decoded[b'info']
    
    # Calculate Total Length
    if b'length' in info_dict:
        total_length = info_dict[b'length']
    else:
        total_length = sum(f[b'length'] for f in info_dict.get(b'files', []))
            
    info_hash = calculate_info_hash(info_dict)
    
    # Extract announce and announce-list (multiple backup trackers)
    announce_url = decoded.get(b'announce', b'').decode('utf-8', 'ignore')
    announce_list = []
    if b'announce-list' in decoded:
        for tier in decoded[b'announce-list']:
            for tracker in tier:
                url_str = tracker.decode('utf-8', 'ignore')
                if url_str not in announce_list:
                    announce_list.append(url_str)
    if announce_url and announce_url not in announce_list:
        announce_list.insert(0, announce_url)
    if not announce_url and announce_list:
        announce_url = announce_list[0]
    
    return {
        'announce': announce_url,
        'announce_list': announce_list,
        'name': info_dict.get(b'name', b'').decode('utf-8', 'ignore'),
        'piece_length': info_dict.get(b'piece length', 0),
        'total_length': total_length,
        'info_hash': info_hash,
        'info_hash_hex': info_hash.hex(),
        'piece_hashes': extract_piece_hashes(info_dict.get(b'pieces', b''))
    }


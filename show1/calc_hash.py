from parser import bdecode, bencode
import hashlib

def calculate_info_hash(info_dict):
    """Calculates the SHA-1 hash of the bencoded info dictionary."""
    info_bencoded = bencode(info_dict)
    info_hash = hashlib.sha1(info_bencoded).digest()
    return info_hash

def extract_piece_hashes(pieces_bytes):
    """Chunks the pieces binary string into a list of 20-byte SHA-1 hex strings."""
    if len(pieces_bytes) % 20 != 0:
        raise ValueError("Invalid pieces length. Must be a multiple of 20 bytes.")
    
    hashes = []
    for i in range(0, len(pieces_bytes), 20):
        chunk = pieces_bytes[i:i+20]
        hashes.append(chunk.hex())
    return hashes

def get_torrent_summary(torrent_file_path):
    """Parses a torrent file and extracts key metadata for presentation."""
    with open(torrent_file_path, 'rb') as f:
        torrent_data = f.read()
    
    decoded = bdecode(torrent_data)
    
    if b'info' not in decoded:
        raise ValueError("Torrent file does not contain 'info' dictionary")
    
    info_dict = decoded[b'info']
    
    # Extract Metadatas
    announce_url = decoded.get(b'announce', b'').decode('utf-8', 'ignore')
    name = info_dict.get(b'name', b'').decode('utf-8', 'ignore')
    piece_length = info_dict.get(b'piece length', 0)
    
    # Calculate Total Length
    total_length = 0
    if b'length' in info_dict:
        total_length = info_dict[b'length']
    elif b'files' in info_dict:
        for f in info_dict[b'files']:
            total_length += f[b'length']
            
    # Hashes
    info_hash = calculate_info_hash(info_dict)
    piece_hashes = extract_piece_hashes(info_dict.get(b'pieces', b''))
    
    return {
        'announce': announce_url,
        'name': name,
        'piece_length': piece_length,
        'total_length': total_length,
        'info_hash_hex': info_hash.hex(),
        'piece_hashes': piece_hashes
    }

if __name__ == '__main__':
    print("")
    print("SHA-1 HASHER & TORRENT INFO HASH")
    
    # Part 1: Basic Hashing Explanation
    sample_text = b"Hello BitTorrent"
    sample_hash = hashlib.sha1(sample_text).hexdigest()
    print("\n1. Standard SHA-1 Hashing Demo:")
    print("   Input Text :", sample_text)
    print("   SHA-1 Hash :", sample_hash)

    # Part 2: Real Torrent Info Hash & Piece Hashes
    torrent_file = 'one_piece.torrent'
    try:
        print(f"\n2. Torrent Info Hash Demo ('{torrent_file}'):")
        summary = get_torrent_summary(torrent_file)
        
        print("   File Name     :", summary['name'])
        print("   INFO HASH     :", summary['info_hash_hex'])
        print("   Total Pieces  :", len(summary['piece_hashes']))
        
        print("\n3. First 3 Piece SHA-1 Hashes (20 bytes each):")
        for i in range(min(3, len(summary['piece_hashes']))):
            print(f"   Piece #{i} : {summary['piece_hashes'][i]}")

        print("\nSHA-1 Hash Calculation Successful!")

    except FileNotFoundError:
        print(f"\nError: Could not find '{torrent_file}' for testing.")
    except Exception as e:
        print(f"\nError: {e}")

    print("")
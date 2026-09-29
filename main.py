import sys
import os
from calc_hash import get_torrent_summary
from tracker import generate_peer_id, get_peers

def format_size(size_in_bytes):
    """Converts bytes to a human-readable format."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_in_bytes < 1024.0:
            return f"{size_in_bytes:.2f} {unit}"
        size_in_bytes /= 1024.0


def main():
    torrent_file = sys.argv[1] if len(sys.argv) > 1 else 'spidy.torrent'
    
    if not os.path.exists(torrent_file):
        print(f"Error: Could not find '{torrent_file}'.")
        print("Usage: python main.py <path_to_torrent_file>")
        return

    try:
        summary = get_torrent_summary(torrent_file)
        
        print(f"Torrent File   : {torrent_file}")
        print(f"File Name      : {summary['name']}")
        print(f"Tracker URL    : {summary['announce']}")
        print(f"Total Size     : {format_size(summary['total_length'])}")
        print(f"Piece Length   : {format_size(summary['piece_length'])}")
        print(f"Total Pieces   : {len(summary['piece_hashes'])}")
        print(f"Info Hash (SHA1): {summary['info_hash_hex']}")

        peer_id = generate_peer_id()
        trackers = summary.get('announce_list') or [summary['announce']]
        
        peers = []
        for tracker_url in trackers:
            try:
                peers = get_peers(
                    announce_url=tracker_url,
                    info_hash_bytes=summary['info_hash'],
                    total_length=summary['total_length'],
                    peer_id=peer_id
                )
                if peers:
                    break
            except Exception:
                continue

        print(f"Peers Found    : {len(peers)}")
        if peers:
            print("Peers          :")
            for ip, port in peers[:5]:
                print(f"  - {ip}:{port}")
            if len(peers) > 5:
                print(f"  ... and {len(peers) - 5} more.")
        else:
            print("Peers          : None found or trackers offline")

    except Exception as e:
        print(f"Failed to process torrent: {e}")

if __name__ == '__main__':
    main()
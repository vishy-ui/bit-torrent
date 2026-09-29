import sys
import os
from calc_hash import get_torrent_summary

def format_size(size_in_bytes):
    """Converts bytes to a human-readable format."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_in_bytes < 1024.0:
            return f"{size_in_bytes:.2f} {unit}"
        size_in_bytes /= 1024.0

def main():
    # Allow passing a torrent file as an argument, default to test.torrent
    torrent_file = sys.argv[1] if len(sys.argv) > 1 else 'spidy.torrent'
    
    if not os.path.exists(torrent_file):
        print(f"Error: Could not find '{torrent_file}'.")
        print("Please provide a valid .torrent file, e.g., 'python main.py one_piece.torrent'")
        return

    print("")
    print("----TORRENT METADATA & HASH REPORT----")
    print("")

    try:
        summary = get_torrent_summary(torrent_file)
        
        print(f"Torrent File   : {torrent_file}")
        print(f"File Name      : {summary['name']}")
        print(f"Tracker URL    : {summary['announce']}")
        print(f"Total Size     : {format_size(summary['total_length'])}")
        print(f"Piece Length   : {format_size(summary['piece_length'])}")
        print(f"Total Pieces   : {len(summary['piece_hashes'])} pieces")
        
        print(f"\nINFO HASH (SHA-1) : {summary['info_hash_hex']}")
        
        print("\nPIECE HASHES (First 5 pieces):")
        hashes = summary['piece_hashes']
        display_count = min(5, len(hashes))
        for i in range(display_count):
            print(f"  Piece #{i:03d} : {hashes[i]}")
            
        if len(hashes) > 5:
            print(f"  ... and {len(hashes) - 5} more pieces.")

    except Exception as e:
        print(f"Failed to parse torrent: {e}")

if __name__ == '__main__':
    main()
import sys
import os
from calc_hash import get_torrent_summary
from tracker import generate_peer_id, get_peers
from swarm_downloader import SwarmDownloader


def fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0:
            return f"{n:.2f} {unit}"
        n /= 1024.0


def main():
    torrent_file = sys.argv[1] if len(sys.argv) > 1 else "one_piece.torrent"

    if not os.path.exists(torrent_file):
        print(f"Error: '{torrent_file}' not found.")
        print("Usage: python main.py <path_to_torrent_file>")
        return

    print("\n" + "=" * 60)
    print("  BitTorrent Client")
    print("=" * 60)

    summary = get_torrent_summary(torrent_file)

    print(f"  File      : {torrent_file}")
    print(f"  Name      : {summary['name']}")
    print(f"  Size      : {fmt_size(summary['total_length'])}")
    print(f"  Pieces    : {len(summary['piece_hashes'])} x {fmt_size(summary['piece_length'])}")
    print(f"  Info Hash : {summary['info_hash_hex']}")
    print(f"  Tracker   : {summary['announce']}")

    peer_id = generate_peer_id()
    trackers = summary.get("announce_list") or [summary["announce"]]

    peers = []
    print("\nQuerying trackers...")
    for url in trackers:
        try:
            found = get_peers(
                announce_url=url,
                info_hash_bytes=summary["info_hash"],
                total_length=summary["total_length"],
                peer_id=peer_id,
            )
            if found:
                peers.extend(p for p in found if p not in peers)
                print(f"  {url}  ->  {len(found)} peers")
            else:
                print(f"  {url}  ->  0 peers")
        except Exception as e:
            print(f"  {url}  ->  failed ({type(e).__name__})")

    print(f"\nTotal unique peers: {len(peers)}")
    if not peers:
        print("No peers found. Exiting.")
        return

    for ip, port in peers[:5]:
        print(f"  {ip}:{port}")
    if len(peers) > 5:
        print(f"  ... and {len(peers) - 5} more")

    download_dir = os.path.join(
        os.path.dirname(os.path.abspath(torrent_file)), "downloads"
    )

    downloader = SwarmDownloader(
        summary=summary,
        peers=peers,
        peer_id=peer_id,
        download_dir=download_dir,
        max_workers=30,
    )

    success = downloader.download()

    if success:
        print(f"\nAll pieces saved to: {download_dir}/")
    else:
        saved = len(downloader.done)
        total = downloader.total_pieces
        print(f"\nPartial download: {saved}/{total} pieces saved to: {download_dir}/")
        print("Re-run to resume (already-downloaded pieces are on disk).")


if __name__ == "__main__":
    main()

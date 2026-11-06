# BitTorrent Client (Python)

A lightweight BitTorrent client implemented in pure Python from scratch without external dependencies.

## Features

- **Bencode Encoder & Decoder (`parser.py`)**
  - Full implementation of integer, byte-string, list, and dictionary parsing.
- **Metadata & Info-Hash Calculation (`calc_hash.py`)**
  - Extracts torrent metadata (name, piece size, total size, announce URLs).
  - Calculates 20-byte SHA-1 Info Hash and extracts SHA-1 piece hashes.
  - Multi-tracker fallback using `announce-list`.
- **Tracker Communication & Peer Discovery (`tracker.py`)**
  - Supports **HTTP/HTTPS** and **UDP** tracker protocols over socket connection.
  - Generates standard 20-byte client Peer IDs (`-PY0001-...`).
  - Decodes compact 6-byte binary peer representations into IPv4 addresses and TCP ports.

## Installation & Setup

### Automated Setup (Installs Python if missing, sets up `.venv` & dependencies)

- **Windows Batch (Double-click or CMD)**:
  ```cmd
  install.bat
  ```
- **PowerShell**:
  ```powershell
  .\install.ps1
  ```

*The installer will automatically detect if Python 3.10+ is installed; if not found, it downloads and installs Python silently for you, creates a virtual environment (`.venv`), and installs required dependencies (`PySide6`).*

### Launching the Application

- **Desktop GUI (qBittorrent clone)**:
  ```cmd
  run_gui.bat
  ```
  *Or directly: `.\.venv\Scripts\python.exe gui3.py`*

- **CLI Swarm Downloader**:
  ```cmd
  .\.venv\Scripts\python.exe main.py spidy.torrent
  ```

## Usage

Run `main.py` with any `.torrent` file:

```bash
python main.py spidy.torrent
```

### Example Output

```text
Torrent File   : spidy.torrent
File Name      : Marvels SpiderMan 2 [DODI Repack]
Tracker URL    : udp://9.rarbg.to:2870/announce
Total Size     : 94.63 GB
Piece Length   : 4.00 MB
Total Pieces   : 24226
Info Hash (SHA1): 7cb185e2c2dd6908f41d445ccd6cb106fab15b16
Peers Found    : 174
Peers          :
  - 5.31.200.107:19698
  - 5.253.205.182:53558
  - 27.61.93.160:57056
  - 39.50.156.197:14642
  - 41.109.24.27:14188
  ... and 169 more.
```

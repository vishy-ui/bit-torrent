import os

class DiskManager:
    """
    Manages disk I/O and file assembly for single-file and multi-file BitTorrent downloads.
    Maps piece offsets across the files in the torrent layout and writes verified pieces.
    """
    def __init__(self, download_dir: str, files_layout: list[dict], piece_length: int, total_length: int):
        self.download_dir = download_dir
        self.files_layout = files_layout
        self.piece_length = piece_length
        self.total_length = total_length
        
        # Calculate global start and end byte offsets for each file
        self.file_ranges = []
        current_offset = 0
        for f in self.files_layout:
            f_length = f['length']
            full_path = os.path.join(self.download_dir, f['path'])
            self.file_ranges.append({
                'path': full_path,
                'length': f_length,
                'start': current_offset,
                'end': current_offset + f_length
            })
            current_offset += f_length

        # Ensure output directory exists
        os.makedirs(self.download_dir, exist_ok=True)

    def write_piece(self, piece_index: int, piece_data: bytes):
        """
        Writes a verified piece to the appropriate file(s) on disk.
        Correctly splits piece data across file boundaries if a piece spans multiple files.
        Creates and expands files on-demand without needing 100GB full preallocation upfront.
        """
        piece_start = piece_index * self.piece_length
        piece_end = piece_start + len(piece_data)
        
        for item in self.file_ranges:
            # Check for overlap between piece and file
            overlap_start = max(piece_start, item['start'])
            overlap_end = min(piece_end, item['end'])
            
            if overlap_start < overlap_end:
                # Offset in the piece data
                data_start = overlap_start - piece_start
                data_end = overlap_end - piece_start
                chunk_to_write = piece_data[data_start:data_end]
                
                # Offset in the destination file
                file_seek_pos = overlap_start - item['start']
                
                dir_name = os.path.dirname(item['path'])
                if dir_name:
                    os.makedirs(dir_name, exist_ok=True)
                
                # Open or create file
                if not os.path.exists(item['path']):
                    with open(item['path'], 'wb') as f:
                        pass
                
                with open(item['path'], 'r+b') as f:
                    f.seek(file_seek_pos)
                    f.write(chunk_to_write)

    def read_piece(self, piece_index: int, piece_length: int) -> bytes:
        """
        Reads a piece back from disk across the file layout (useful for hash verification or seeding).
        """
        piece_start = piece_index * self.piece_length
        piece_end = min(piece_start + piece_length, self.total_length)
        
        buffer = bytearray()
        for item in self.file_ranges:
            overlap_start = max(piece_start, item['start'])
            overlap_end = min(piece_end, item['end'])
            
            if overlap_start < overlap_end:
                file_seek_pos = overlap_start - item['start']
                read_length = overlap_end - overlap_start
                
                with open(item['path'], 'rb') as f:
                    f.seek(file_seek_pos)
                    chunk = f.read(read_length)
                    buffer.extend(chunk)
                    
        return bytes(buffer)

from typing import List, Dict, Any
import codecs
import os

def read_text_header(path: str) -> Dict[str, Any]:
    """
    Reads a SEG-Y text header (3200 bytes) from the given file path.
    Auto-detects ASCII vs EBCDIC (cp037/cp500).
    Returns: {encoding, lines: List[str], raw: bytes}
    """
    with open(path, 'rb') as f:
        raw = f.read(3200)
        if len(raw) != 3200:
            raise ValueError("SEG-Y text header must be 3200 bytes")

    # Try ASCII first
    try:
        text = raw.decode('ascii')
        encoding = 'ascii'
    except UnicodeDecodeError:
        # Try EBCDIC (cp037, then cp500)
        try:
            text = raw.decode('cp037')
            encoding = 'cp037'
        except UnicodeDecodeError:
            text = raw.decode('cp500')
            encoding = 'cp500'

    # Split into 40 lines of 80 chars
    lines = [text[i*80:(i+1)*80] for i in range(40)]
    return {'encoding': encoding, 'lines': lines, 'raw': raw}

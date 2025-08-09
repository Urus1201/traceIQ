import os
import tempfile
from segy.header_io import read_text_header

def make_cp037_header():
    """Generate a valid 3200-byte EBCDIC (cp037) textual header.
    Lines are exactly 80 chars, with classic "Cnn " prefix.
    """
    lines = []
    for i in range(40):
        prefix = f"C{(i+1):02d} "  # 4 chars
        content = (f"EBCDIC TEST LINE {i+1}").ljust(76)
        line = (prefix + content)[:80]
        assert len(line) == 80
        lines.append(line)
    text = ''.join(lines)
    raw = text.encode('cp037')
    assert len(raw) == 3200
    return raw, lines

def test_read_text_header_cp037():
    raw, expected_lines = make_cp037_header()
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(raw)
        tmp.flush()
        tmp_path = tmp.name
    try:
        result = read_text_header(tmp_path)
        assert result['encoding'] == 'cp037'
        assert result['lines'] == expected_lines
        assert len(result['lines']) == 40
        assert result['raw'] == raw
    finally:
        os.unlink(tmp_path)

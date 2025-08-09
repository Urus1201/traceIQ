from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class BinaryHeaderStub:
    sample_interval_us: Optional[int]
    samples_per_trace: Optional[int]
    format_code: Optional[int]


def read_binary_header(path: str) -> BinaryHeaderStub:
    """Read minimal SEG-Y binary header fields using segyio if available.

    Falls back to None values when segyio isn't installed or reading fails.
    """
    try:
        import segyio  # type: ignore

        # Open in read-only, ignore geometry to avoid heavy IO
        with segyio.open(path, mode="r", strict=False, ignore_geometry=True) as f:  # type: ignore[attr-defined]
            try:
                si = int(f.bin[segyio.BinField.Interval])  # microseconds
            except Exception:
                si = None
            try:
                spt = int(f.bin[segyio.BinField.Samples])
            except Exception:
                spt = None
            try:
                fmt = int(f.bin[segyio.BinField.Format])
            except Exception:
                fmt = None
            return BinaryHeaderStub(sample_interval_us=si, samples_per_trace=spt, format_code=fmt)
    except Exception:
        # segyio not installed or file could not be opened/read
        return BinaryHeaderStub(sample_interval_us=None, samples_per_trace=None, format_code=None)


def to_jsonable(stub: BinaryHeaderStub) -> dict:
    """Helper to convert the dataclass to a JSON-serializable dict."""
    return asdict(stub)

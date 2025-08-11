from __future__ import annotations

from typing import Dict, Tuple

# Minimal, programmatic EPSG catalog pieces we need

# Families and how to compute EPSG from UTM zone
# Note: For north-only families we ignore 'S' hemisphere when generating.
FAMILIES = {
    "WGS84": {"north": 32600, "south": 32700, "label": "WGS84"},
    "NAD83": {"north": 26900, "south": None, "label": "NAD83"},
    "NAD27": {"north": 26700, "south": None, "label": "NAD27"},
    "ED50": {"north": 23000, "south": None, "label": "ED50"},
    "ETRS89": {"north": 25800, "south": None, "label": "ETRS89"},
}


def utm_epsg(datum: str, zone: int, hemi: str | None) -> int | None:
    fam = FAMILIES.get(datum)
    if not fam:
        return None
    if hemi == "S":
        base = fam.get("south")
    else:
        base = fam.get("north")
    if base is None:
        return None
    return base + int(zone)


def utm_label(datum: str, zone: int, hemi: str | None) -> str:
    hemich = "S" if hemi == "S" else "N"
    return f"{datum} / UTM zone {int(zone)}{hemich}"


__all__ = ["utm_epsg", "utm_label", "FAMILIES"]

from __future__ import annotations

from typing import Any, List, Optional
from pydantic import BaseModel, Field, ConfigDict


class FieldEvidence(BaseModel):
    """A value extracted from the textual header with provenance."""

    value: Any | None = Field(default=None, description="Extracted value")
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Confidence score in [0,1]"
    )
    line_refs: List[int] = Field(
        default_factory=list, description="1-based line numbers from 40×80 header"
    )


class HeaderJSON(BaseModel):
    """Structured representation of SEG-Y textual header fields."""

    survey_name: Optional[FieldEvidence] = None
    area: Optional[FieldEvidence] = None
    contractor: Optional[FieldEvidence] = None
    acquisition_year: Optional[FieldEvidence] = None
    sample_interval_ms: Optional[FieldEvidence] = None
    record_length_ms: Optional[FieldEvidence] = None
    inline_spacing_m: Optional[FieldEvidence] = None
    crossline_spacing_m: Optional[FieldEvidence] = None
    bin_size_m: Optional[FieldEvidence] = None
    geometry: Optional[FieldEvidence] = None
    source_type: Optional[FieldEvidence] = None
    receiver_type: Optional[FieldEvidence] = None
    datum: Optional[FieldEvidence] = None
    srd_m: Optional[FieldEvidence] = None
    crs_hint: Optional[FieldEvidence] = None
    vessel: Optional[FieldEvidence] = None
    notes: Optional[FieldEvidence] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "survey_name": {
                    "value": "ACME_2020_NORTHSEA",
                    "confidence": 0.95,
                    "line_refs": [1],
                },
                "area": {"value": "North Sea", "confidence": 0.9, "line_refs": [1]},
                "contractor": {
                    "value": "Acme Geo",
                    "confidence": 0.85,
                    "line_refs": [2],
                },
                "acquisition_year": {
                    "value": 2020,
                    "confidence": 0.9,
                    "line_refs": [3],
                },
                "sample_interval_ms": {
                    "value": 2.0,
                    "confidence": 0.9,
                    "line_refs": [6],
                },
                "record_length_ms": {
                    "value": 4000.0,
                    "confidence": 0.9,
                    "line_refs": [6],
                },
                "inline_spacing_m": {
                    "value": 25.0,
                    "confidence": 0.7,
                    "line_refs": [8],
                },
                "crossline_spacing_m": {
                    "value": 25.0,
                    "confidence": 0.7,
                    "line_refs": [8],
                },
                "bin_size_m": {
                    "value": 12.5,
                    "confidence": 0.7,
                    "line_refs": [8],
                },
                "geometry": {
                    "value": "3D Towed Streamer",
                    "confidence": 0.8,
                    "line_refs": [4],
                },
                "source_type": {
                    "value": "Airgun",
                    "confidence": 0.8,
                    "line_refs": [5],
                },
                "receiver_type": {
                    "value": "Streamer",
                    "confidence": 0.8,
                    "line_refs": [5],
                },
                "datum": {
                    "value": "MSL",
                    "confidence": 0.6,
                    "line_refs": [10],
                },
                "srd_m": {"value": 0.0, "confidence": 0.6, "line_refs": [10]},
                "crs_hint": {
                    "value": "UTM 31N (EPSG:32631)",
                    "confidence": 0.5,
                    "line_refs": [12],
                },
                "vessel": {
                    "value": "MV Discovery",
                    "confidence": 0.7,
                    "line_refs": [9],
                },
                "notes": {
                    "value": "Derived heuristically from textual header.",
                    "confidence": 0.3,
                    "line_refs": [15, 16],
                },
            }
        }
    )

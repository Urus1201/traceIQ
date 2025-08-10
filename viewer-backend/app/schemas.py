from __future__ import annotations

from typing import Any, List, Optional, Literal
from pydantic import BaseModel, Field, ConfigDict, field_validator


class FieldEvidence(BaseModel):
    """A value extracted from the textual header with provenance."""

    value: Any | None = Field(default=None, description="Extracted value")
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Confidence score in [0,1]"
    )
    line_refs: List[int] = Field(
        default_factory=list, description="1-based line numbers from 40×80 header"
    )

    @field_validator("line_refs")
    @classmethod
    def _validate_line_refs(cls, v: List[int]) -> List[int]:
        # enforce 1..40, unique, sorted
        v = [int(x) for x in v if 1 <= int(x) <= 40]
        v = sorted(dict.fromkeys(v))
        return v


class HeaderJSON(BaseModel):
    """Structured representation of SEG-Y textual header fields."""

    # Core identification
    survey_name: Optional[FieldEvidence] = None
    area: Optional[FieldEvidence] = None
    contractor: Optional[FieldEvidence] = None
    company: Optional[FieldEvidence] = None
    client: Optional[FieldEvidence] = None
    acquisition_year: Optional[FieldEvidence] = None

    # Core acquisition parameters
    sample_interval_ms: Optional[FieldEvidence] = None
    record_length_ms: Optional[FieldEvidence] = None
    samples_per_trace: Optional[FieldEvidence] = None
    data_traces_per_record: Optional[FieldEvidence] = None
    auxiliary_traces_per_record: Optional[FieldEvidence] = None

    # Format/system hints
    recording_format: Optional[FieldEvidence] = None
    measurement_system: Optional[FieldEvidence] = None  # "METRIC" / "FEET" (normalized)

    # Geometry & logistics (nice-to-haves)
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
                "survey_name": {"value": "ACME_2020_NORTHSEA", "confidence": 0.95, "line_refs": [1]},
                "area": {"value": "North Sea", "confidence": 0.9, "line_refs": [1]},
                "company": {"value": "Acme Oil", "confidence": 0.85, "line_refs": [2]},
                "acquisition_year": {"value": 2020, "confidence": 0.9, "line_refs": [3]},
                "sample_interval_ms": {"value": 2.0, "confidence": 0.9, "line_refs": [6]},
                "samples_per_trace": {"value": 750, "confidence": 0.9, "line_refs": [6]},
                "record_length_ms": {"value": 1500.0, "confidence": 0.9, "line_refs": [6]},
                "data_traces_per_record": {"value": 240, "confidence": 0.8, "line_refs": [5]},
                "auxiliary_traces_per_record": {"value": 4, "confidence": 0.7, "line_refs": [5]},
                "recording_format": {"value": "SEGY", "confidence": 0.75, "line_refs": [7]},
                "measurement_system": {"value": "METRIC", "confidence": 0.65, "line_refs": [7]},
            }
        }
    )


class ProvenanceEntry(BaseModel):
    field: str
    source: Literal["baseline", "llm", "merged_agree"] = Field(
        description="Source of chosen value"
    )
    baseline_conf: Optional[float] = None
    llm_conf: Optional[float] = None
    chosen_conf: Optional[float] = None
    line_refs: List[int] = Field(default_factory=list)


class ParseResponse(BaseModel):
    header: HeaderJSON
    provenance: List[ProvenanceEntry] = Field(default_factory=list)

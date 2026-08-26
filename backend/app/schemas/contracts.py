from __future__ import annotations
from typing import Any, Dict, List, Literal, Optional, Tuple
from pydantic import BaseModel, ConfigDict, Field

class BBox(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    xmin: float = Field(...)
    ymin: float = Field(...)
    xmax: float = Field(...)
    ymax: float = Field(...)

class OCRToken(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    text: str = Field(...)
    bbox: BBox = Field(...)
    confidence: float = Field(..., ge=0.0, le=1.0)
    language: str = Field(...)

class OCROutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    tokens: List[OCRToken] = Field(default_factory=list)
    full_text: str = Field(...)
    image_dimensions: Tuple[int, int] = Field(...)

class ExtractedField(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    field_name: str = Field(...)
    raw_value: Optional[str] = Field(default=None)
    normalized_value: Optional[Any] = Field(default=None)
    bbox: Optional[BBox] = Field(default=None)
    confidence: float = Field(..., ge=0.0, le=1.0)
    method: Literal["regex", "llm", "none"] = Field(...)

class FieldMappingOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    fields: Dict[str, ExtractedField] = Field(default_factory=dict)

class Violation(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    field_name: str = Field(...)
    issue: str = Field(...)
    severity: Literal["critical", "major", "minor"] = Field(...)
    bbox: Optional[BBox] = Field(default=None)
    legal_reference: Optional[str] = Field(default=None)

class ComplianceResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    is_compliant: bool = Field(...)
    violations: List[Violation] = Field(default_factory=list)
    score: float = Field(..., ge=0.0, le=100.0)
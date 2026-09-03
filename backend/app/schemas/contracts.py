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
	image_index: int = Field(default=0)

class ComplianceResult(BaseModel):
	model_config = ConfigDict(from_attributes=True)
	is_compliant: bool = Field(...)
	violations: List[Violation] = Field(default_factory=list)
	score: float = Field(..., ge=0.0, le=100.0)

class ViolationOut(BaseModel):
	model_config = ConfigDict(from_attributes=True)
	field_name: str
	issue: str
	severity: str
	bbox: Optional[List[float]] = None
	legal_reference: Optional[str] = None
	image_index: int = 0

class ScanInitResponse(BaseModel):
	model_config = ConfigDict(from_attributes=True)
	scan_id: str
	status: str
	image_paths: List[str] = Field(default_factory=list)
	is_compliant: bool
	score: float
	violations: List[ViolationOut] = Field(default_factory=list)
	extracted_fields: List[Dict[str, Any]] = Field(default_factory=list)

class ScanHistoryItem(BaseModel):
	model_config = ConfigDict(from_attributes=True)
	id: str
	created_at: str
	status: str
	is_compliant: Optional[bool] = None
	compliance_score: Optional[float] = None
	product_id: Optional[str] = None
	image_count: int = 0
	primary_image_path: Optional[str] = None

class ScanHistoryResponse(BaseModel):
	model_config = ConfigDict(from_attributes=True)
	total: int
	limit: int
	offset: int
	items: List[ScanHistoryItem] = Field(default_factory=list)

class ScanDetailResponse(BaseModel):
	model_config = ConfigDict(from_attributes=True)
	id: str
	product_id: Optional[str] = None
	image_paths: List[str] = Field(default_factory=list)
	status: str
	is_compliant: Optional[bool] = None
	compliance_score: Optional[float] = None
	raw_ocr: Optional[Any] = None
	extracted_fields: Optional[Any] = None
	created_at: str
	violations: List[Dict[str, Any]] = Field(default_factory=list)

class FontCheckRequest(BaseModel):
	model_config = ConfigDict(from_attributes=True)
	tap_x: int
	tap_y: int
	coin_key: str = "5_rupee"
	net_quantity_g_or_ml: Optional[float] = None
	image_index: int = 0

class FontCheckResponse(BaseModel):
	model_config = ConfigDict(from_attributes=True)
	scan_id: str
	available: bool
	message: Optional[str] = None
	result: Optional[Dict[str, Any]] = None
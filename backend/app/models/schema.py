from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Any, List, Optional

from sqlalchemy import DateTime, Float, ForeignKey, String, Boolean, JSON
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator, CHAR

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

class GUID(TypeDecorator):
    """Platform-independent UUID type compatible with SQLite and Postgres."""
    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value: Optional[Any], dialect) -> Optional[str]:
        if value is None:
            return None
        if dialect.name == "postgresql":
            return str(value)
        if not isinstance(value, uuid.UUID):
            return str(uuid.UUID(str(value)))
        return str(value)

    def process_result_value(self, value: Optional[Any], dialect) -> Optional[uuid.UUID]:
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))

class Base(DeclarativeBase):
    pass

class Product(Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    brand: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    scan_results: Mapped[List["ScanResult"]] = relationship(
        "ScanResult",
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

class ScanResult(Base):
    __tablename__ = "scan_results"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    product_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(), ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    image_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    is_compliant: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    compliance_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    raw_ocr: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    extracted_fields: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    product: Mapped[Optional["Product"]] = relationship(
        "Product", back_populates="scan_results", lazy="selectin"
    )
    violations: Mapped[List["ViolationRecord"]] = relationship(
        "ViolationRecord",
        back_populates="scan_result",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

class ViolationRecord(Base):
    __tablename__ = "violations"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    scan_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("scan_results.id", ondelete="CASCADE"), nullable=False
    )
    field_name: Mapped[str] = mapped_column(String(255), nullable=False)
    issue: Mapped[str] = mapped_column(String(1024), nullable=False)
    severity: Mapped[str] = mapped_column(String(50), nullable=False)
    bbox: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    legal_reference: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    scan_result: Mapped["ScanResult"] = relationship(
        "ScanResult", back_populates="violations", lazy="selectin"
    )
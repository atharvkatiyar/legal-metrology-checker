from __future__ import annotations
import json
import uuid
from datetime import datetime, timezone
from typing import Any, List, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import CHAR, TypeDecorator


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


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    officer_id: Mapped[str] = mapped_column(
        String(100), unique=True, index=True, nullable=False
    )
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    region: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    scans: Mapped[List["ScanResult"]] = relationship(
        "ScanResult", back_populates="officer", lazy="selectin"
    )


class Product(Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    brand: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
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
    officer_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    image_paths: Mapped[List[str]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    is_compliant: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    compliance_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    raw_ocr: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    extracted_fields: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    sync_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="pending_sync"
    )
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    product: Mapped[Optional["Product"]] = relationship(
        "Product", back_populates="scan_results", lazy="selectin"
    )
    officer: Mapped[Optional["User"]] = relationship(
        "User", back_populates="scans", lazy="selectin"
    )
    violations: Mapped[List["ViolationRecord"]] = relationship(
        "ViolationRecord",
        back_populates="scan_result",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    @property
    def image_path(self) -> Optional[str]:
        """Backward-compat accessor: first staged image, for modules
        (e.g. font_size_adapter callers) that still expect a single path."""
        if self.image_paths:
            return self.image_paths[0]
        return None

    @image_path.setter
    def image_path(self, value: Any) -> None:
        """Backward-compat setter: parses JSON string from router.py or accepts list directly."""
        if not value:
            self.image_paths = []
            return

        if isinstance(value, list):
            self.image_paths = value
        elif isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    self.image_paths = parsed
                else:
                    self.image_paths = [value]
            except ValueError:
                self.image_paths = [value]
        else:
            self.image_paths = [str(value)]


class ViolationRecord(Base):
    __tablename__ = "violations"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    scan_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("scan_results.id", ondelete="CASCADE"), nullable=False
    )
    field_name: Mapped[str] = mapped_column(String(255), nullable=False)
    issue: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(50), nullable=False)
    violation_category: Mapped[str] = mapped_column(
        String(50), nullable=False, default="TEXT_CONTENT"
    )
    measured_value: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    bbox: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    legal_reference: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    image_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    scan_result: Mapped["ScanResult"] = relationship(
        "ScanResult", back_populates="violations", lazy="selectin"
    )
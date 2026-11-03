"""Definisi skema model database SQLAlchemy untuk ADMS."""
import uuid
from datetime import datetime, timezone
from typing import Optional, Any
from sqlalchemy import (
    Column,
    String,
    Boolean,
    Integer,
    BigInteger,
    DateTime,
    Text,
    LargeBinary,
    ForeignKey,
    UniqueConstraint,
    Index,
    Uuid,
    JSON,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def utcnow() -> datetime:
    """Mengembalikan waktu UTC saat ini dengan timezone aware."""
    return datetime.now(timezone.utc)


class Device(Base):
    """Informasi mesin absensi ZKTeco yang terdaftar."""
    __tablename__ = "devices"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    serial_number = Column(String(100), unique=True, nullable=False, index=True)
    name = Column(String(150), nullable=True)
    model = Column(String(100), nullable=True)
    firmware = Column(String(100), nullable=True)
    ip_last = Column(String(50), nullable=True)
    tz_offset_minutes = Column(Integer, default=420, nullable=False)  # default UTC+7
    is_master = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    capabilities = Column(JSON, default=dict, nullable=False)
    
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    last_attlog_stamp = Column(String(50), nullable=True)
    last_operlog_stamp = Column(String(50), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    commands = relationship("DeviceCommand", back_populates="device", cascade="all, delete-orphan")


class RawRequest(Base):
    """Audit log mentah untuk semua request iClock yang masuk (append-only)."""
    __tablename__ = "raw_requests"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    device_sn = Column(String(100), nullable=True, index=True)
    method = Column(String(10), nullable=False)
    path = Column(String(255), nullable=False)
    query = Column(Text, nullable=True)
    headers = Column(JSON, nullable=True)
    body = Column(Text, nullable=True)  # text payload atau base64 jika biner
    body_sha256 = Column(String(64), nullable=True, index=True)
    received_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    parse_status = Column(String(20), default="pending", nullable=False)  # pending, success, partial, failed
    parse_error = Column(Text, nullable=True)


class Employee(Base):
    """Data karyawan yang disinkronisasi ke mesin absensi."""
    __tablename__ = "employees"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pin = Column(String(50), unique=True, nullable=False, index=True)
    full_name = Column(String(150), nullable=False)
    card_no = Column(String(50), nullable=True, index=True)
    password_hash = Column(String(255), nullable=True)
    privilege = Column(Integer, default=0, nullable=False)  # 0: normal, 14: admin
    is_active = Column(Boolean, default=True, nullable=False)
    needs_review = Column(Boolean, default=False, nullable=False)  # True jika auto-dibuat dari mesin
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    templates = relationship("BiometricTemplate", back_populates="employee", cascade="all, delete-orphan")
    sync_states = relationship("EmployeeDeviceSync", back_populates="employee", cascade="all, delete-orphan")


class BiometricTemplate(Base):
    """Template biometrik sidik jari dan wajah terenkripsi."""
    __tablename__ = "biometric_templates"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_id = Column(Uuid(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String(20), nullable=False)  # 'finger' atau 'face'
    slot = Column(Integer, default=0, nullable=False)  # FID / Index (0-9)
    format_version = Column(String(50), nullable=True)  # contoh: '12.0', 'ZKFP10'
    template = Column(LargeBinary, nullable=False)  # bytes terenkripsi AES-GCM
    template_sha256 = Column(String(64), nullable=False)
    source_device_sn = Column(String(100), nullable=True)
    version = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    employee = relationship("Employee", back_populates="templates")

    __table_args__ = (
        UniqueConstraint("employee_id", "kind", "slot", "template_sha256", name="uq_biometric_template"),
    )


class AttendanceLog(Base):
    """Log kehadiran dari mesin absensi dengan garansi anti-redundan."""
    __tablename__ = "attlog"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_sn = Column(String(100), nullable=False, index=True)
    pin = Column(String(50), nullable=False, index=True)
    punched_at = Column(DateTime(timezone=True), nullable=False, index=True)  # UTC aware
    punched_at_local = Column(DateTime, nullable=False)                       # Naive dari mesin
    status = Column(Integer, default=0, nullable=False)                       # 0: IN, 1: OUT, dll
    verify_type = Column(Integer, default=0, nullable=False)                  # 1: FP, 2: PW, 3: Card, 15: Face
    workcode = Column(String(50), default="0", nullable=True)
    raw_line = Column(Text, nullable=True)
    raw_request_id = Column(BigInteger, nullable=True)
    received_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    tz_offset_at_receive = Column(Integer, default=420, nullable=False)

    __table_args__ = (
        UniqueConstraint("device_sn", "pin", "punched_at", name="uq_attlog_device_pin_punched_at"),
    )


class AttendanceLogConflict(Base):
    """Log catatan konflik ketika punch time sama tapi status/verify berbeda."""
    __tablename__ = "attlog_conflicts"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    existing_attlog_id = Column(Uuid(as_uuid=True), nullable=True)
    device_sn = Column(String(100), nullable=False)
    pin = Column(String(50), nullable=False)
    punched_at = Column(DateTime(timezone=True), nullable=False)
    conflict_data = Column(JSON, nullable=True)
    raw_line = Column(Text, nullable=True)
    raw_request_id = Column(BigInteger, nullable=True)
    received_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class DeviceCommand(Base):
    """Antrian perintah yang dikirim ke mesin via polling getrequest."""
    __tablename__ = "device_commands"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    device_sn = Column(String(100), ForeignKey("devices.serial_number", ondelete="CASCADE"), nullable=False, index=True)
    command_type = Column(String(50), nullable=False)  # UPDATE_USER, UPDATE_TMP, DELETE_USER, INFO, REBOOT, etc
    payload = Column(Text, nullable=False)
    idempotency_key = Column(String(150), nullable=False)
    state = Column(String(20), default="pending", nullable=False, index=True)  # pending, sent, acked, failed, dead, cancelled
    attempts = Column(Integer, default=0, nullable=False)
    max_attempts = Column(Integer, default=5, nullable=False)
    next_attempt_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    acked_at = Column(DateTime(timezone=True), nullable=True)
    return_code = Column(Integer, nullable=True)
    last_error = Column(Text, nullable=True)
    created_by = Column(String(100), default="system", nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    device = relationship("Device", back_populates="commands")

    __table_args__ = (
        UniqueConstraint("device_sn", "idempotency_key", name="uq_device_command_idempotency"),
    )


class EmployeeDeviceSync(Base):
    """Status sinkronisasi karyawan dan biometrik per mesin."""
    __tablename__ = "employee_device_sync"

    employee_id = Column(Uuid(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), primary_key=True)
    device_sn = Column(String(100), primary_key=True)
    user_synced_at = Column(DateTime(timezone=True), nullable=True)
    finger_version_synced = Column(Integer, default=0, nullable=False)
    face_version_synced = Column(Integer, default=0, nullable=False)
    state = Column(String(30), default="pending", nullable=False)  # pending, synced, incompatible, failed

    employee = relationship("Employee", back_populates="sync_states")


class OutboxEvent(Base):
    """Transactional Outbox untuk pengiriman event ke sistem hilir (HR/Payroll)."""
    __tablename__ = "outbox_events"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type = Column(String(50), nullable=False)  # ATTLOG_CREATED, EMPLOYEE_ENROLLED, etc
    aggregate_id = Column(String(100), nullable=False)
    payload = Column(JSON, nullable=False)
    idempotency_key = Column(String(150), unique=True, nullable=False, index=True)
    state = Column(String(20), default="pending", nullable=False, index=True)  # pending, delivered, failed, dead
    attempts = Column(Integer, default=0, nullable=False)
    max_attempts = Column(Integer, default=10, nullable=False)
    next_attempt_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

from sqlalchemy import (
    create_engine,
    Integer,
    String,
    Float,
    Date,
    DateTime,
    Column,
    text,   # 👈 add this
)
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.sql import func

DB_URL = "sqlite:///./app/data/data.db"
engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class Employee(Base):
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    employee_id = Column(String, index=True)
    name = Column(String)
    cg_email = Column(String, index=True)
    citi_email = Column(String, index=True)
    region_code = Column(String)
    region_name = Column(String)
    default_project_code = Column(String, index=True)
    billing_rate = Column(Float, default=0.0)

    role = Column(String, nullable=True)
    manager = Column(String, nullable=True)

    # New: annual leave allowance in days (e.g. 12 or 15)
    annual_leave_allowance = Column(Integer, default=15)

    status = Column(String, default="Active")  # Active / Inactive
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ReconEntry(Base):
    __tablename__ = "recon_entries"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    employee_id = Column(String, index=True)
    month = Column(String, index=True)  # YYYY-MM
    name = Column(String)
    cg_email = Column(String)
    citi_email = Column(String, index=True)

    region_code = Column(String)
    region_name = Column(String)

    project_name = Column(String)
    project_code = Column(String, index=True)

    billing_rate = Column(Float, default=0.0)

    total_hours_cg = Column(Float, default=0.0)
    submitted_hours_cg = Column(Float, default=0.0)
    submitted_on_cg = Column(String, nullable=True)
    status_cg = Column(String)

    total_hours_citi = Column(Float, default=0.0)
    submitted_hours_citi = Column(Float, default=0.0)
    holidays = Column(String, nullable=True)
    status_citi = Column(String)

    expected_hours = Column(Float, default=0.0)
    reconciled_hours = Column(Float, default=0.0)
    reconciled_status = Column(String)

    reminders = Column(Integer, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CGDaily(Base):
    __tablename__ = "cg_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    citi_email = Column(String, index=True)
    date = Column(Date, index=True)
    hours = Column(Float, default=0.0)
    project_code = Column(String, index=True)


class CITIDaily(Base):
    __tablename__ = "citi_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    citi_email = Column(String, index=True)
    date = Column(Date, index=True)
    hours = Column(Float, default=0.0)
    project_code = Column(String, index=True)


class TimeOff(Base):
    __tablename__ = "time_off"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    # optional FK to employees.id (not enforced)
    employee_id = Column(Integer, nullable=True)
    citi_email = Column(String, index=True)

    start_date = Column(Date, index=True)
    end_date = Column(Date, index=True)
    days = Column(Float, default=0.0)  # working days between start & end (excl. weekends)

    leave_type = Column(String)  # e.g. 'Planned', 'Sick', 'Unpaid'
    reason = Column(String)
    status = Column(String, default="Pending")  # Pending / Approved / Rejected

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


def init_db():
    # Create any missing tables
    Base.metadata.create_all(bind=engine)

    # === Lightweight migration for existing databases ===
    # 1) Ensure employees.annual_leave_allowance exists
    with engine.connect() as conn:
        res = conn.execute(text("PRAGMA table_info(employees)"))
        cols = [row[1] for row in res]  # row[1] is column name
        if "annual_leave_allowance" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE employees "
                    "ADD COLUMN annual_leave_allowance INTEGER DEFAULT 15"
                )
            )

    # 2) Ensure time_off table exists (if you added it after initial DB)
    Base.metadata.create_all(bind=engine)


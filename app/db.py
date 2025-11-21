from sqlalchemy import create_engine, Integer, String, Float, Date, DateTime, Column
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.sql import func

DB_URL = "sqlite:///./app/data/data.db"
engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


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
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


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


def init_db():
    Base.metadata.create_all(bind=engine)

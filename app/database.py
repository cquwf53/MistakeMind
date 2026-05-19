from collections.abc import Generator
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def ensure_compatible_schema() -> None:
    inspector = inspect(engine)
    if "mistakes" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("mistakes")}
        if "source_path" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE mistakes ADD COLUMN source_path VARCHAR(255) NOT NULL DEFAULT ''"))
    if "mistake_analyses" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("mistake_analyses")}
        with engine.begin() as connection:
            if "ai_model_used" not in columns:
                connection.execute(text("ALTER TABLE mistake_analyses ADD COLUMN ai_model_used VARCHAR(120) NOT NULL DEFAULT ''"))
            if "ai_model_mode" not in columns:
                connection.execute(text("ALTER TABLE mistake_analyses ADD COLUMN ai_model_mode VARCHAR(30) NOT NULL DEFAULT 'auto'"))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

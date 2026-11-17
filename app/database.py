from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

Path("data").mkdir(parents=True, exist_ok=True)

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    pool_pre_ping=True,
    pool_size=5,  # Bounded connections per container; works for SQLite and PostgreSQL.
    max_overflow=5,
    pool_timeout=15,
    pool_recycle=1800,
)

if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)

    if settings.database_url.startswith("sqlite"):
        with engine.begin() as connection:
            document_columns = {column["name"] for column in inspect(engine).get_columns("documents")}
            if "rag_status" not in document_columns:
                connection.execute(
                    text("ALTER TABLE documents ADD COLUMN rag_status VARCHAR(30) DEFAULT 'pending'")
                )

            if inspect(engine).has_table("llm_call_logs"):
                log_columns = {column["name"] for column in inspect(engine).get_columns("llm_call_logs")}
                if "provider" not in log_columns:
                    connection.execute(
                        text("ALTER TABLE llm_call_logs ADD COLUMN provider VARCHAR(30) DEFAULT 'ollama'")
                    )
            migrations = {
                "study_plans": {"minutes_per_day": "INTEGER NOT NULL DEFAULT 45"},
                "card_review_states": {"step": "INTEGER"},
                "study_cards": {
                    "archived": "BOOLEAN NOT NULL DEFAULT 0",
                    "version_number": "INTEGER NOT NULL DEFAULT 1",
                    "concept_key": "VARCHAR(160)",
                },
                "document_chunks": {"page_number": "INTEGER"},
                "exercises": {
                    "purpose": "VARCHAR(30) NOT NULL DEFAULT 'practice'",
                    "bloom_level": "VARCHAR(30) NOT NULL DEFAULT 'understand'",
                    "topic": "VARCHAR(160) NOT NULL DEFAULT ''",
                    "hints": "JSON NOT NULL DEFAULT '[]'",
                    "source_excerpt": "TEXT NOT NULL DEFAULT ''",
                    "objective": "TEXT NOT NULL DEFAULT ''",
                    "remediation_error_id": "INTEGER",
                },
                "exercise_attempts": {
                    "score": "FLOAT NOT NULL DEFAULT 0",
                    "confidence": "FLOAT NOT NULL DEFAULT 3",
                    "error_type": "VARCHAR(50)",
                    "response_time_seconds": "FLOAT NOT NULL DEFAULT 0",
                    "feedback_data": "JSON NOT NULL DEFAULT '{}'",
                },
                "topic_mastery": {
                    "confidence_score": "FLOAT NOT NULL DEFAULT 0",
                    "retention_score": "FLOAT NOT NULL DEFAULT 0",
                    "transfer_score": "FLOAT NOT NULL DEFAULT 0",
                    "forgetting_rate": "FLOAT NOT NULL DEFAULT 0",
                    "last_practiced_at": "DATETIME",
                },
                "learning_events": {"confidence": "FLOAT", "session_id": "INTEGER"},
                "knowledge_concepts": {"lesson_language": "VARCHAR(10) NOT NULL DEFAULT 'pt-BR'"},
                "whatsapp_profiles": {"daily_goal_minutes": "INTEGER NOT NULL DEFAULT 30"},
                "whatsapp_messages": {
                    "priority": "INTEGER NOT NULL DEFAULT 0",
                    "processing_started_at": "DATETIME",
                    "processed_at": "DATETIME",
                },
            }
            inspector = inspect(engine)
            for table_name, columns in migrations.items():
                if not inspector.has_table(table_name):
                    continue
                existing = {column["name"] for column in inspector.get_columns(table_name)}
                for column_name, column_type in columns.items():
                    if column_name not in existing:
                        connection.execute(
                            text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
                        )

    # Create indexes after additive columns exist on upgraded installations.
    indexes = (
        "CREATE INDEX IF NOT EXISTS ix_whatsapp_messages_queue ON whatsapp_messages (direction, status, priority, created_at, id)",
        "CREATE INDEX IF NOT EXISTS ix_whatsapp_messages_fifo ON whatsapp_messages (direction, status, created_at, id)",
        "CREATE INDEX IF NOT EXISTS ix_whatsapp_jobs_queue ON whatsapp_document_jobs (status, id)",
        "CREATE INDEX IF NOT EXISTS ix_whatsapp_outbox_due ON whatsapp_outbox (status, next_attempt_at, id)",
        "CREATE INDEX IF NOT EXISTS ix_document_chunks_doc_order ON document_chunks (document_id, chunk_index)",
    )
    with engine.begin() as connection:
        for statement in indexes:
            connection.execute(text(statement))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

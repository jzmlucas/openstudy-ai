from sqlalchemy import create_engine, inspect, text

from app import database


def test_init_db_adds_whatsapp_queue_columns_before_queue_indexes(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE whatsapp_messages (
                id INTEGER NOT NULL PRIMARY KEY,
                external_message_id VARCHAR(180),
                phone_e164 VARCHAR(20) NOT NULL,
                direction VARCHAR(12) NOT NULL,
                message_type VARCHAR(40),
                text TEXT,
                payload JSON,
                status VARCHAR(20),
                error_message TEXT,
                created_at DATETIME
            )
        """))

    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database.settings, "database_url", str(engine.url))

    database.init_db()

    columns = {column["name"] for column in inspect(engine).get_columns("whatsapp_messages")}
    indexes = {index["name"] for index in inspect(engine).get_indexes("whatsapp_messages")}
    assert {"priority", "processing_started_at", "processed_at"} <= columns
    assert "ix_whatsapp_messages_queue" in indexes

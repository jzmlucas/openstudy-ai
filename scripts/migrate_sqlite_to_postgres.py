"""One-way, offline copy of OpenStudy data to an EMPTY PostgreSQL database.

Run from the PostgreSQL image after stopping all OpenStudy SQLite writers.
Does not delete or mutate the source SQLite DB or the uploads directory.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import SQLAlchemyError

from app import models  # noqa: F401 - registers all mappings
from app.database import Base


def migrate(sqlite_path: Path, target_url: str) -> dict[str, int]:
    if not sqlite_path.is_file():
        raise RuntimeError(f"Banco SQLite não encontrado: {sqlite_path}")
    if not target_url.startswith("postgresql+"):
        raise RuntimeError("A URL de destino precisa ser PostgreSQL; recuso alterar um banco SQLite.")
    # sqlite3 read-only URI prevents accidentally creating an empty file.
    source = create_engine(f"sqlite:///file:{sqlite_path.resolve()}?mode=ro&uri=true")
    destination = create_engine(target_url, pool_pre_ping=True)
    counts: dict[str, int] = {}
    try:
        # Fail fast, before ANY writes, if a pre-existing target contains rows.
        Base.metadata.create_all(destination)
        with destination.connect() as conn:
            occupied = [table.name for table in Base.metadata.sorted_tables if conn.execute(select(func.count()).select_from(table)).scalar_one()]
        if occupied:
            raise RuntimeError("Destino contém dados! Migração cancelada para evitar duplicação: " + ", ".join(occupied))

        with source.connect() as src, destination.begin() as dest:
            for table in Base.metadata.sorted_tables:
                exists = src.exec_driver_sql(
                    "SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?", (table.name,)
                ).scalar_one()
                if not exists:
                    counts[table.name] = 0
                    continue
                result = src.execute(select(table))
                number = 0
                while batch := result.mappings().fetchmany(200):
                    # Values are bound; no SQL interpolation of user data.
                    dest.execute(table.insert(), [dict(row) for row in batch])
                    number += len(batch)
                counts[table.name] = number
            # Repair autoincrement sequences; PostgreSQL doesn't auto-adjust when copying explicit IDs.
            for table in Base.metadata.sorted_tables:
                if "id" not in table.c or not table.c.id.primary_key:
                    continue
                name = table.name  # Only ORM-authored SQL identifiers, never input.
                sequence = dest.execute(text(f"SELECT pg_get_serial_sequence('public.{name}', 'id')")).scalar_one()
                if sequence:
                    dest.execute(text(
                        f"SELECT setval(pg_get_serial_sequence('public.{name}', 'id'), "
                        f"GREATEST(COALESCE((SELECT MAX(id) FROM {name}), 0), 1), "
                        f"COALESCE((SELECT MAX(id) FROM {name}), 0) > 0)"
                    ))
        # Verify counts after committed inserts.
        with destination.connect() as conn:
            for table in Base.metadata.sorted_tables:
                actual = conn.execute(select(func.count()).select_from(table)).scalar_one()
                if actual != counts[table.name]:
                    raise RuntimeError(f"Falha de verificação de linhas em {table.name}: {actual} != {counts[table.name]}")
        return counts
    finally:
        source.dispose()
        destination.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", default="/app/data/openstudy.db", type=Path)
    parser.add_argument("--target", default="", help="Se vazio, usa DATABASE_URL do ambiente")
    args = parser.parse_args()
    from app.config import settings
    target = args.target or settings.database_url
    try:
        summary = migrate(args.sqlite, target)
    except (OSError, RuntimeError, SQLAlchemyError) as exc:
        print(f"ERRO: migração NÃO concluída: {exc}", file=sys.stderr)
        sys.exit(1)
    print("Migração verificada: " + ", ".join(f"{k}={v}" for k, v in summary.items()))

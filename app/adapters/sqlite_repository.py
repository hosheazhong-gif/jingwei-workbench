from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from app import SCHEMA_VERSION

_MIGRATION_TARGET_VERSION = re.compile(r"(?:_to)?_v(\d+_\d+)$")


class SqliteRepository:
    """SQLite 持久化边界；业务层不直接拼接 SQL。"""

    def __init__(self, database_path: Path | str, files_root: Path | str | None = None):
        self.database_path = Path(database_path)
        if files_root is None:
            self.files_root = self.database_path.parent / f"{self.database_path.stem}-files"
        else:
            self.files_root = Path(files_root)

    def _open(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        cursor = connection.execute("PRAGMA foreign_keys = ON")
        cursor.close()
        return connection

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """打开连接并在退出时提交、关闭。sqlite3.Connection 的 with 不会关闭连接。"""
        connection = self._open()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connect() as connection:
            yield connection

    def migrate(self) -> None:
        migration_dir = Path(__file__).resolve().parents[1] / "migrations"
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    migration_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            applied = {
                row["migration_id"]
                for row in connection.execute("SELECT migration_id FROM schema_migrations")
            }
            for path in sorted(migration_dir.glob("*.sql")):
                if path.stem in applied:
                    continue
                script = path.read_text(encoding="utf-8")
                connection.executescript(script)
                connection.execute(
                    """
                    INSERT INTO schema_migrations
                    (migration_id, schema_version, applied_at)
                    VALUES (?, ?, ?)
                    """,
                    (
                        path.stem,
                        _schema_version_for_migration(path.stem),
                        datetime.now(UTC).isoformat(),
                    ),
                )

    def has_project(self, project_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
        return row is not None

    def get_source(self, source_id: str) -> dict[str, object] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM sources WHERE id = ?",
                (source_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_source_ids(self, project_id: str) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id FROM sources WHERE project_id = ? ORDER BY rowid",
                (project_id,),
            ).fetchall()
        return [row["id"] for row in rows]

    def allocate_source_id(self, project_id: str | None = None) -> str:
        """全局分配 Source ID；主键跨项目唯一，不能按题目从 S-001 重数。"""
        with self.connect() as connection:
            used = {row["id"] for row in connection.execute("SELECT id FROM sources")}
        index = 1
        while f"S-{index:03d}" in used:
            index += 1
        return f"S-{index:03d}"

    def insert_source(self, values: dict[str, object]) -> None:
        payload = dict(values)
        payload.setdefault("research_question_id", None)
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO sources (
                    id, project_id, kind, title, file_name, availability,
                    snapshot_path, content_hash, supersedes_source_id, limitation,
                    analysis_role, delivery_use, schema_version, created_at, updated_at,
                    institution, published_at, original_url, original_path,
                    permission, sensitivity, source_quality, research_question_id
                ) VALUES (
                    :id, :project_id, :kind, :title, :file_name, :availability,
                    :snapshot_path, :content_hash, :supersedes_source_id, :limitation,
                    :analysis_role, :delivery_use, :schema_version, :created_at, :updated_at,
                    :institution, :published_at, :original_url, :original_path,
                    :permission, :sensitivity, :source_quality, :research_question_id
                )
                """,
                payload,
            )


def _schema_version_for_migration(stem: str) -> str:
    if "_to_v" in stem:
        return stem.split("_to_v", 1)[1].replace("_", ".")
    match = _MIGRATION_TARGET_VERSION.search(stem)
    if match:
        return match.group(1).replace("_", ".")
    return SCHEMA_VERSION

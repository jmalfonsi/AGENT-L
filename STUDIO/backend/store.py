from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .config import DB_PATH, ensure_data_dirs


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self) -> None:
        ensure_data_dirs()
        self._init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        # WAL et `busy_timeout` : les runs écrivent depuis des threads de
        # fond pendant que l'interface lit. Sans eux, deux exécutions
        # simultanées suffisent à sortir un « database is locked ».
        db = sqlite3.connect(DB_PATH, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA busy_timeout = 10000")
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def _init_schema(self) -> None:
        with self.connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    slug TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    path TEXT NOT NULL,
                    config_json TEXT NOT NULL DEFAULT '{}',
                    skills_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS skills (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    slug TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL DEFAULT '',
                    domain TEXT NOT NULL DEFAULT 'general',
                    content TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'custom',
                    customized INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    -- Nullable : la rédaction d'un skill n'appartient à
                    -- aucun projet, et doit pourtant laisser une trace.
                    project_id TEXT,
                    command TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    duration_ms INTEGER,
                    exit_code INTEGER,
                    output TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_runs_project_started
                    ON runs(project_id, started_at DESC);
                """
            )
            # Bases créées avant l'édition des skills système.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(skills)")}
            if "customized" not in columns:
                db.execute("ALTER TABLE skills ADD COLUMN customized INTEGER NOT NULL DEFAULT 0")
            self._allow_runs_without_project(db)

    @staticmethod
    def _allow_runs_without_project(db: sqlite3.Connection) -> None:
        """Rend `runs.project_id` nullable sur une base antérieure.

        SQLite ne sait pas relâcher un NOT NULL : la table est reconstruite,
        ce qui n'arrive qu'une fois.
        """
        info = {row["name"]: row for row in db.execute("PRAGMA table_info(runs)")}
        if "project_id" not in info or not info["project_id"]["notnull"]:
            return
        db.execute("PRAGMA foreign_keys = OFF")
        db.executescript(
            """
            CREATE TABLE runs_migrated (
                id TEXT PRIMARY KEY,
                project_id TEXT,
                command TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                duration_ms INTEGER,
                exit_code INTEGER,
                output TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );
            INSERT INTO runs_migrated SELECT id, project_id, command, status,
                started_at, finished_at, duration_ms, exit_code, output,
                metadata_json FROM runs;
            DROP TABLE runs;
            ALTER TABLE runs_migrated RENAME TO runs;
            CREATE INDEX IF NOT EXISTS idx_runs_project_started
                ON runs(project_id, started_at DESC);
            """
        )
        db.execute("PRAGMA foreign_keys = ON")

    @staticmethod
    def _decode(row: sqlite3.Row | None, fields: tuple[str, ...]) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        for field in fields:
            result[field.removesuffix("_json")] = json.loads(result.pop(field) or "null")
        return result

    def list_projects(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
        return [self._decode(row, ("config_json", "skills_json")) for row in rows]  # type: ignore[misc]

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return self._decode(row, ("config_json", "skills_json"))

    def create_project(self, data: dict[str, Any]) -> dict[str, Any]:
        project_id = data.get("id") or uuid.uuid4().hex
        stamp = now_iso()
        with self.connect() as db:
            db.execute(
                """INSERT INTO projects
                   (id, name, slug, description, status, path, config_json, skills_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    project_id, data["name"], data["slug"], data.get("description", ""),
                    data.get("status", "active"), data["path"],
                    json.dumps(data.get("config", {}), ensure_ascii=False),
                    json.dumps(data.get("skills", []), ensure_ascii=False), stamp, stamp,
                ),
            )
        return self.get_project(project_id)  # type: ignore[return-value]

    def update_project(self, project_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {"name", "description", "status", "config", "skills"}
        assignments: list[str] = []
        values: list[Any] = []
        for key, value in changes.items():
            if key not in allowed:
                continue
            column = f"{key}_json" if key in {"config", "skills"} else key
            assignments.append(f"{column} = ?")
            values.append(json.dumps(value, ensure_ascii=False) if key in {"config", "skills"} else value)
        if not assignments:
            return self.get_project(project_id)
        assignments.append("updated_at = ?")
        values.extend([now_iso(), project_id])
        with self.connect() as db:
            db.execute(f"UPDATE projects SET {', '.join(assignments)} WHERE id = ?", values)
        return self.get_project(project_id)

    def delete_project(self, project_id: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM projects WHERE id = ?", (project_id,))

    def list_skills(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM skills ORDER BY source, name").fetchall()]

    def get_skill(self, skill_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
        return dict(row) if row else None

    def get_skills(self, skill_ids: list[str]) -> list[dict[str, Any]]:
        """Une seule requête, dans l'ordre demandé par l'appelant."""
        if not skill_ids:
            return []
        placeholders = ",".join("?" * len(skill_ids))
        with self.connect() as db:
            rows = db.execute(
                f"SELECT * FROM skills WHERE id IN ({placeholders})", skill_ids
            ).fetchall()
        by_id = {row["id"]: dict(row) for row in rows}
        return [by_id[skill_id] for skill_id in skill_ids if skill_id in by_id]

    def seed_skill(self, data: dict[str, Any]) -> dict[str, Any]:
        """Installe un skill livré sans écraser une version personnalisée.

        Le semis tourne à chaque démarrage : sans ce garde-fou, toute
        édition d'un skill système disparaîtrait au redémarrage suivant,
        en silence.
        """
        stamp = now_iso()
        with self.connect() as db:
            row = db.execute("SELECT * FROM skills WHERE slug = ?", (data["slug"],)).fetchone()
            if row is None:
                db.execute(
                    """INSERT INTO skills
                       (id, name, slug, description, domain, content, source,
                        customized, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                    (
                        data.get("id") or uuid.uuid4().hex, data["name"], data["slug"],
                        data.get("description", ""), data.get("domain", "general"),
                        data["content"], data.get("source", "system"), stamp, stamp,
                    ),
                )
            elif not row["customized"]:
                db.execute(
                    """UPDATE skills SET name = ?, description = ?, domain = ?,
                       content = ?, source = ?, updated_at = ? WHERE slug = ?""",
                    (
                        data["name"], data.get("description", ""), data.get("domain", "general"),
                        data["content"], data.get("source", "system"), stamp, data["slug"],
                    ),
                )
            row = db.execute("SELECT * FROM skills WHERE slug = ?", (data["slug"],)).fetchone()
        return dict(row)  # type: ignore[arg-type]

    def upsert_skill(self, data: dict[str, Any]) -> dict[str, Any]:
        skill_id = data.get("id") or uuid.uuid4().hex
        stamp = now_iso()
        with self.connect() as db:
            db.execute(
                """INSERT INTO skills
                   (id, name, slug, description, domain, content, source, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(slug) DO UPDATE SET
                     name=excluded.name, description=excluded.description,
                     domain=excluded.domain, content=excluded.content,
                     source=excluded.source, updated_at=excluded.updated_at""",
                (
                    skill_id, data["name"], data["slug"], data.get("description", ""),
                    data.get("domain", "general"), data["content"], data.get("source", "custom"),
                    data.get("created_at", stamp), stamp,
                ),
            )
            if "customized" in data:
                db.execute("UPDATE skills SET customized = ? WHERE slug = ?",
                           (1 if data["customized"] else 0, data["slug"]))
            row = db.execute("SELECT * FROM skills WHERE slug = ?", (data["slug"],)).fetchone()
        return dict(row)  # type: ignore[arg-type]

    def reset_skill(self, slug: str, content: str) -> dict[str, Any] | None:
        """Ramène un skill à sa version livrée et lève la marque d'édition."""
        with self.connect() as db:
            db.execute(
                "UPDATE skills SET content = ?, customized = 0, updated_at = ? WHERE slug = ?",
                (content, now_iso(), slug),
            )
            row = db.execute("SELECT * FROM skills WHERE slug = ?", (slug,)).fetchone()
        return dict(row) if row else None

    def delete_skill(self, skill_id: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM skills WHERE id = ? AND source != 'system'", (skill_id,))

    def create_run(self, project_id: str | None, command: str) -> dict[str, Any]:
        run_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute(
                "INSERT INTO runs (id, project_id, command, status, started_at) VALUES (?, ?, ?, 'running', ?)",
                (run_id, project_id, command, now_iso()),
            )
        return self.get_run(run_id)  # type: ignore[return-value]

    def finish_run(self, run_id: str, **data: Any) -> dict[str, Any] | None:
        with self.connect() as db:
            db.execute(
                """UPDATE runs SET status=?, finished_at=?, duration_ms=?, exit_code=?, output=?, metadata_json=?
                   WHERE id=?""",
                (
                    data["status"], now_iso(), data.get("duration_ms"), data.get("exit_code"),
                    data.get("output", ""), json.dumps(data.get("metadata", {}), ensure_ascii=False), run_id,
                ),
            )
        return self.get_run(run_id)

    def interrupt_orphan_runs(self) -> int:
        """Clôt les runs qu'un arrêt du serveur a laissés « en cours ».

        Le registre des runs vit en mémoire : après un redémarrage, plus rien
        ne pouvait terminer ces lignes. L'interface les montrait en cours pour
        toujours, et leur flux attendait une fin qui ne viendrait pas.
        """
        with self.connect() as db:
            cursor = db.execute(
                """UPDATE runs SET status = 'interrupted', finished_at = ?,
                   output = output || ? WHERE status = 'running'""",
                (now_iso(), "\n[interrompu : le serveur s'est arrêté pendant ce run]\n"),
            )
            return cursor.rowcount

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return self._decode(row, ("metadata_json",))

    def list_runs(self, project_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            if project_id:
                rows = db.execute(
                    "SELECT * FROM runs WHERE project_id=? ORDER BY started_at DESC LIMIT ?",
                    (project_id, limit),
                ).fetchall()
            else:
                rows = db.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._decode(row, ("metadata_json",)) for row in rows]  # type: ignore[misc]

    def run_ids(self, project_id: str) -> list[tuple[str, str]]:
        """Identifiants et statuts des runs d'un projet, du plus récent au plus ancien."""
        with self.connect() as db:
            return [(row["id"], row["status"]) for row in db.execute(
                "SELECT id, status FROM runs WHERE project_id = ? "
                "ORDER BY started_at DESC, rowid DESC", (project_id,))]

    def stats(self) -> dict[str, int]:
        with self.connect() as db:
            projects = db.execute("SELECT COUNT(*) FROM projects WHERE status='active'").fetchone()[0]
            skills = db.execute("SELECT COUNT(*) FROM skills").fetchone()[0]
            runs = db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            passed = db.execute("SELECT COUNT(*) FROM runs WHERE status='passed'").fetchone()[0]
        return {"projects": projects, "skills": skills, "runs": runs, "passed": passed}


"""Módulo de Persistencia y Base de Datos Ligera sin Costos Fijos (`core/database.py`).

Gestiona usuarios de Google OAuth, huellas SHA-256 de documentos para control anti-abuso,
sesiones activas, historial de causas ciudadanas y movimientos procesales.
Diseñado para funcionar sin costos fijos: SQLite en almacenamiento local / volumen Render,
o PostgreSQL en Supabase / Neon mediante DATABASE_URL.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Literal

DB_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "drh_production.db"


class DrHDatabase:
    """Acceso a datos ACID sin dependencias externas pesadas."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else DB_DEFAULT_PATH
        self.init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def init_schema(self) -> None:
        """Crea las tablas necesarias para Google OAuth, anti-abuso y causas."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    google_id TEXT UNIQUE NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    picture TEXT DEFAULT '',
                    free_consultations_used INTEGER NOT NULL DEFAULT 0,
                    has_paid_pass INTEGER NOT NULL DEFAULT 0,
                    created_at_iso TEXT NOT NULL,
                    updated_at_iso TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS document_fingerprints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sha256_hash TEXT NOT NULL,
                    user_email TEXT NOT NULL,
                    is_free_tier INTEGER NOT NULL DEFAULT 1,
                    created_at_iso TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_doc_hash ON document_fingerprints(sha256_hash);

                CREATE TABLE IF NOT EXISTS user_sessions (
                    token TEXT PRIMARY KEY,
                    user_email TEXT NOT NULL,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    expires_at_iso TEXT NOT NULL,
                    created_at_iso TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_cases (
                    case_id TEXT PRIMARY KEY,
                    user_email TEXT NOT NULL,
                    title TEXT NOT NULL,
                    detected_fuero TEXT NOT NULL,
                    province_jurisdiction TEXT NOT NULL,
                    status_color TEXT NOT NULL, -- VERDE, AMARILLO, ROJO
                    do_not_sign_yet INTEGER NOT NULL DEFAULT 0,
                    last_summary TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    created_at_iso TEXT NOT NULL,
                    updated_at_iso TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_user_cases_email ON user_cases(user_email);

                CREATE TABLE IF NOT EXISTS case_movements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT NOT NULL,
                    movement_number INTEGER NOT NULL,
                    movement_date TEXT NOT NULL,
                    pages_count INTEGER NOT NULL,
                    novedad_summary TEXT NOT NULL,
                    previous_status_color TEXT NOT NULL,
                    new_status_color TEXT NOT NULL,
                    risk_alert_text TEXT DEFAULT '',
                    questions_generated_json TEXT NOT NULL DEFAULT '[]',
                    created_at_iso TEXT NOT NULL,
                    FOREIGN KEY (case_id) REFERENCES user_cases(case_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_case_movements_case ON case_movements(case_id);
                """
            )

    def reset_for_tests(self) -> None:
        """Limpia las tablas para tests unitarios e integración."""
        with self._connect() as conn:
            conn.executescript(
                """
                DELETE FROM case_movements;
                DELETE FROM user_cases;
                DELETE FROM user_sessions;
                DELETE FROM document_fingerprints;
                DELETE FROM users;
                """
            )

    # --- USUARIOS & GOOGLE OAUTH ---
    def upsert_google_user(
        self,
        *,
        google_id: str,
        email: str,
        name: str,
        picture: str = "",
    ) -> dict[str, Any]:
        """Crea o actualiza un usuario autenticado con Google OAuth."""
        now = datetime.now(timezone.utc).isoformat()
        clean_email = email.strip().lower()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ? OR google_id = ?",
                (clean_email, google_id),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO users (
                        google_id, email, name, picture, free_consultations_used,
                        has_paid_pass, created_at_iso, updated_at_iso
                    ) VALUES (?, ?, ?, ?, 0, 0, ?, ?)
                    """,
                    (google_id, clean_email, name.strip(), picture, now, now),
                )
            else:
                conn.execute(
                    """
                    UPDATE users
                    SET google_id = ?, name = ?, picture = ?, updated_at_iso = ?
                    WHERE id = ?
                    """,
                    (google_id, name.strip(), picture, now, row["id"]),
                )
            return dict(conn.execute("SELECT * FROM users WHERE email = ?", (clean_email,)).fetchone())

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        clean_email = email.strip().lower()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE email = ?", (clean_email,)).fetchone()
            return dict(row) if row else None

    def increment_free_consultations(self, email: str) -> int:
        clean_email = email.strip().lower()
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE users
                SET free_consultations_used = free_consultations_used + 1, updated_at_iso = ?
                WHERE email = ?
                """,
                (now, clean_email),
            )
            row = conn.execute("SELECT free_consultations_used FROM users WHERE email = ?", (clean_email,)).fetchone()
            return int(row["free_consultations_used"]) if row else 1

    def set_user_paid_pass(self, email: str, has_paid: bool = True) -> None:
        clean_email = email.strip().lower()
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE users
                SET has_paid_pass = ?, updated_at_iso = ?
                WHERE email = ?
                """,
                (1 if has_paid else 0, now, clean_email),
            )

    # --- FILTRO CRIPTOGRÁFICO ANTI-ABUSO (SHA-256) ---
    def is_document_already_processed_free(self, sha256_hash: str) -> bool:
        """Determina si el archivo ya fue procesado bajo una prueba gratuita."""
        clean_hash = sha256_hash.strip().lower()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM document_fingerprints
                WHERE sha256_hash = ? AND is_free_tier = 1
                LIMIT 1
                """,
                (clean_hash,),
            ).fetchone()
            return row is not None

    def record_document_fingerprint(
        self,
        *,
        sha256_hash: str,
        user_email: str,
        is_free_tier: bool,
    ) -> None:
        clean_hash = sha256_hash.strip().lower()
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO document_fingerprints (sha256_hash, user_email, is_free_tier, created_at_iso)
                VALUES (?, ?, ?, ?)
                """,
                (clean_hash, user_email.strip().lower(), 1 if is_free_tier else 0, now),
            )

    # --- SESIONES DE USUARIO ---
    def create_session(
        self,
        *,
        token: str,
        user_email: str,
        is_admin: bool = False,
        expires_at_iso: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_sessions (token, user_email, is_admin, expires_at_iso, created_at_iso)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(token) DO UPDATE SET
                    user_email = excluded.user_email,
                    is_admin = excluded.is_admin,
                    expires_at_iso = excluded.expires_at_iso
                """,
                (token, user_email.strip().lower(), 1 if is_admin else 0, expires_at_iso, now),
            )

    def get_session(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM user_sessions
                WHERE token = ? AND expires_at_iso > ?
                """,
                (token.strip(), now),
            ).fetchone()
            return dict(row) if row else None

    def delete_session(self, token: str) -> bool:
        if not token:
            return False
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM user_sessions WHERE token = ?", (token.strip(),))
            return cur.rowcount > 0

    # --- CAUSAS Y HISTORIAL ---
    def create_case(
        self,
        *,
        case_id: str,
        user_email: str,
        title: str,
        detected_fuero: str,
        province_jurisdiction: str,
        status_color: Literal["VERDE", "AMARILLO", "ROJO"],
        do_not_sign_yet: bool,
        last_summary: str,
        report_json: str,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_cases (
                    case_id, user_email, title, detected_fuero, province_jurisdiction,
                    status_color, do_not_sign_yet, last_summary, report_json,
                    created_at_iso, updated_at_iso
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    user_email.strip().lower(),
                    title.strip(),
                    detected_fuero,
                    province_jurisdiction,
                    status_color,
                    1 if do_not_sign_yet else 0,
                    last_summary,
                    report_json,
                    now,
                    now,
                ),
            )
            return dict(conn.execute("SELECT * FROM user_cases WHERE case_id = ?", (case_id,)).fetchone())

    def get_user_cases(self, user_email: str) -> list[dict[str, Any]]:
        clean_email = user_email.strip().lower()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT case_id, user_email, title, detected_fuero, province_jurisdiction,
                       status_color, do_not_sign_yet, last_summary, created_at_iso, updated_at_iso
                FROM user_cases
                WHERE user_email = ?
                ORDER BY updated_at_iso DESC
                """,
                (clean_email,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_case_by_id(self, case_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM user_cases WHERE case_id = ?", (case_id,)).fetchone()
            return dict(row) if row else None

    def add_case_movement(
        self,
        *,
        case_id: str,
        movement_date: str,
        pages_count: int,
        novedad_summary: str,
        previous_status_color: str,
        new_status_color: Literal["VERDE", "AMARILLO", "ROJO"],
        risk_alert_text: str = "",
        questions_generated: list[str] | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        questions_json = json.dumps(questions_generated or [], ensure_ascii=False)
        with self._connect() as conn:
            # Obtener número de movimiento siguiente
            count_row = conn.execute(
                "SELECT COUNT(*) as total FROM case_movements WHERE case_id = ?",
                (case_id,),
            ).fetchone()
            mov_num = int(count_row["total"]) + 1

            conn.execute(
                """
                INSERT INTO case_movements (
                    case_id, movement_number, movement_date, pages_count,
                    novedad_summary, previous_status_color, new_status_color,
                    risk_alert_text, questions_generated_json, created_at_iso
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    mov_num,
                    movement_date,
                    pages_count,
                    novedad_summary,
                    previous_status_color,
                    new_status_color,
                    risk_alert_text,
                    questions_json,
                    now,
                ),
            )
            # Actualizar estado de la causa principal
            do_not_sign = 1 if new_status_color == "ROJO" else 0
            conn.execute(
                """
                UPDATE user_cases
                SET status_color = ?, do_not_sign_yet = ?, last_summary = ?, updated_at_iso = ?
                WHERE case_id = ?
                """,
                (new_status_color, do_not_sign, novedad_summary, now, case_id),
            )
            mov_row = conn.execute(
                "SELECT * FROM case_movements WHERE case_id = ? AND movement_number = ?",
                (case_id, mov_num),
            ).fetchone()
            return dict(mov_row)

    def get_case_movements(self, case_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, case_id, movement_number, movement_date, pages_count,
                       novedad_summary, previous_status_color, new_status_color,
                       risk_alert_text, questions_generated_json, created_at_iso
                FROM case_movements
                WHERE case_id = ?
                ORDER BY movement_number ASC
                """,
                (case_id,),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["questions_generated"] = json.loads(d["questions_generated_json"])
                result.append(d)
            return result

"""Sincronización Externa No Bloqueante de Métricas Anónimas (`core/metrics_sync.py`).

Persiste el estado consolidado de `daily_metrics` y `daily_metric_visitors` (100% Zero PII)
fuera del disco efímero de Render para evitar pérdida de estadísticas ante reinicios de CPU:

1. Prioridad 1 (Cero cuentas nuevas): GitHub REST API contra un repositorio PRIVADO de la cuenta
   `drhargentina-afk` (`METRICS_GITHUB_REPO` + `METRICS_GITHUB_TOKEN`), guardando `metrics_snapshot.json`.
   Incluye bloqueo de seguridad para impedir que se suban snapshots al repositorio público de código.
2. Soporte adicional para archivo externo/volumen (`METRICS_SNAPSHOT_FILE`) o PostgreSQL (`DATABASE_URL`
   / `METRICS_DATABASE_URL` con esquema `postgresql://`).
3. Restauración automática en `startup` de FastAPI haciendo merge seguro (`MAX` / sin pisar filas más nuevas).
4. Backup asíncrono en segundo plano con debounce y timeout estricto de 5 segundos para jamás bloquear
   ni arrojar errores al usuario final.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
import threading
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from core.database import DrHDatabase

_sync_logger = logging.getLogger("dr_h.metrics_sync")

DEFAULT_GITHUB_OWNER = "drhargentina-afk"
FORBIDDEN_PUBLIC_REPOS = frozenset(
    {
        "dr-h-argentina",
        "drhargentina-afk/dr-h-argentina",
    }
)
DEFAULT_SNAPSHOT_FILENAME = "metrics_snapshot.json"
DEFAULT_GITHUB_BRANCH = "main"
DEFAULT_GDRIVE_METRICS_FOLDER_ID = "1rqzCXkNMEgGNqmR9eLL9KZiKYGkWqNbr"
NETWORK_TIMEOUT_SECONDS = 5.0
DEFAULT_DEBOUNCE_SECONDS = 2.0


def build_metrics_csv_from_snapshot(snapshot: dict[str, Any]) -> str:
    """Construye el archivo CSV histórico y acumulado a partir de un snapshot de métricas (Zero PII)."""
    raw_rows = snapshot.get("daily_metrics")
    rows = [r for r in raw_rows if isinstance(r, dict)] if isinstance(raw_rows, list) else []
    rows_sorted = sorted(rows, key=lambda x: str(x.get("metric_date") or ""), reverse=True)

    metric_cols = (
        "visits_without_query",
        "users_single_query",
        "users_two_queries",
        "users_three_plus_queries",
        "total_queries_executed",
        "social_shares",
        "payment_clicks_micro",
        "payment_clicks_monthly",
        "payments_confirmed",
    )
    totals = {col: sum(max(0, int(r.get(col) or 0)) for r in rows_sorted) for col in metric_cols}

    headers = [
        "Fecha",
        "Visitas_sin_consulta",
        "1_consulta",
        "2_consultas_Social",
        "3_o_mas_Comercial",
        "Total_IA",
        "Compartio",
        "Clic_Micro_4k",
        "Clic_Mensual_20k",
        "Pagos_confirmados",
    ]
    lines = [",".join(headers)]
    lines.append(
        ",".join(
            [
                "TOTAL_HISTORICO_ACUMULADO",
                *(str(totals[c]) for c in metric_cols),
            ]
        )
    )
    for r in rows_sorted:
        lines.append(
            ",".join(
                [
                    str(r.get("metric_date") or ""),
                    *(str(max(0, int(r.get(c) or 0))) for c in metric_cols),
                ]
            )
        )
    return "\r\n".join(lines) + "\r\n"


def _normalize_github_repo(raw_repo: str) -> str:
    """Normaliza 'owner/repo' y bloquea explícitamente el repositorio público de código."""
    clean = str(raw_repo or "").strip().strip("/")
    if not clean:
        return ""
    if "/" not in clean:
        clean = f"{DEFAULT_GITHUB_OWNER}/{clean}"
    if clean.lower() in FORBIDDEN_PUBLIC_REPOS:
        _sync_logger.warning(
            "METRICS_GITHUB_REPO apunta al repositorio público (%s). "
            "Se aborta sincronización por seguridad: usá un repositorio privado (ej. drhargentina-afk/dr-h-metrics-private).",
            clean,
        )
        return ""
    return clean


class MetricsSyncManager:
    """Coordinador thread-safe y no bloqueante de persistencia externa para daily_metrics."""

    def __init__(
        self,
        *,
        github_token: str | None = None,
        github_repo: str | None = None,
        github_path: str | None = None,
        github_branch: str | None = None,
        snapshot_file: str | Path | None = None,
        postgres_url: str | None = None,
        debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
        timeout_seconds: float = NETWORK_TIMEOUT_SECONDS,
    ) -> None:
        self._github_token_override = github_token
        self._github_repo_override = github_repo
        self._github_path_override = github_path
        self._github_branch_override = github_branch
        self._snapshot_file_override = str(snapshot_file) if snapshot_file else None
        self._postgres_url_override = postgres_url
        self._debounce_seconds = max(0.05, float(debounce_seconds))
        self._timeout_seconds = max(1.0, min(5.0, float(timeout_seconds)))

        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._cached_github_sha: str | None = None

    def _get_github_config(self) -> tuple[str, str, str, str]:
        """Obtiene (token, owner/repo, file_path, branch) desde overrides o variables de entorno."""
        token = (
            self._github_token_override
            if self._github_token_override is not None
            else os.environ.get("METRICS_GITHUB_TOKEN", "")
        ).strip()
        raw_repo = (
            self._github_repo_override
            if self._github_repo_override is not None
            else os.environ.get("METRICS_GITHUB_REPO", "")
        ).strip()
        repo = _normalize_github_repo(raw_repo)
        file_path = (
            self._github_path_override
            if self._github_path_override is not None
            else os.environ.get("METRICS_GITHUB_PATH", DEFAULT_SNAPSHOT_FILENAME)
        ).strip().lstrip("/") or DEFAULT_SNAPSHOT_FILENAME
        branch = (
            self._github_branch_override
            if self._github_branch_override is not None
            else os.environ.get("METRICS_GITHUB_BRANCH", DEFAULT_GITHUB_BRANCH)
        ).strip() or DEFAULT_GITHUB_BRANCH
        return token, repo, file_path, branch

    def _get_snapshot_file_path(self) -> Path | None:
        """Devuelve la ruta de archivo de snapshot externo si está configurada."""
        raw = (
            self._snapshot_file_override
            if self._snapshot_file_override is not None
            else os.environ.get("METRICS_SNAPSHOT_FILE", "")
        ).strip()
        return Path(raw) if raw else None

    def _get_postgres_url(self) -> str:
        """Devuelve DATABASE_URL / METRICS_DATABASE_URL si corresponde a una conexión PostgreSQL."""
        raw = (
            self._postgres_url_override
            if self._postgres_url_override is not None
            else (os.environ.get("METRICS_DATABASE_URL") or os.environ.get("DATABASE_URL") or "")
        ).strip()
        if raw.lower().startswith(("postgres://", "postgresql://")):
            return raw
        return ""

    def is_configured(self) -> bool:
        """Indica si algún backend externo (GitHub privado, archivo o PostgreSQL) está activo."""
        token, repo, _, _ = self._get_github_config()
        if token and repo:
            return True
        if self._get_snapshot_file_path() is not None:
            return True
        if self._get_postgres_url():
            return True
        return False

    # ------------------------------------------------------------------
    # GITHUB REST API (REPOSITORIO PRIVADO)
    # ------------------------------------------------------------------

    def _github_contents_url(self, repo: str, file_path: str, branch: str | None = None) -> str:
        encoded_path = "/".join(urllib.parse.quote(part, safe="") for part in file_path.split("/") if part)
        base = f"https://api.github.com/repos/{repo}/contents/{encoded_path}"
        if branch:
            return f"{base}?ref={urllib.parse.quote(branch, safe='')}"
        return base

    def _github_headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "DrH-Argentina-MetricsSync/1.0",
            "Content-Type": "application/json; charset=utf-8",
        }

    def fetch_github_snapshot(self) -> dict[str, Any] | None:
        """Descarga metrics_snapshot.json desde el repositorio privado de GitHub con timeout de 5s."""
        token, repo, file_path, branch = self._get_github_config()
        if not token or not repo:
            return None

        url = self._github_contents_url(repo, file_path, branch=branch)
        req = urllib.request.Request(url, headers=self._github_headers(token), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_seconds) as resp:
                raw_body = resp.read().decode("utf-8")
                payload = json.loads(raw_body)
                sha = str(payload.get("sha") or "").strip()
                if sha:
                    self._cached_github_sha = sha
                encoded_content = str(payload.get("content") or "").strip()
                if not encoded_content:
                    return None
                decoded_bytes = base64.b64decode(encoded_content)
                snapshot = json.loads(decoded_bytes.decode("utf-8"))
                return snapshot if isinstance(snapshot, dict) else None
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                _sync_logger.info("Snapshot remoto aún no existe en GitHub (%s/%s); se creará en el primer backup.", repo, file_path)
            else:
                _sync_logger.warning("GitHub API HTTP %s al leer snapshot de métricas: %s", exc.code, exc.reason)
            return None
        except Exception as exc:
            _sync_logger.warning("No se pudo descargar snapshot de métricas desde GitHub: %s", exc)
            return None

    def push_github_snapshot(self, snapshot: dict[str, Any]) -> bool:
        """Sube metrics_snapshot.json al repositorio privado de GitHub en <= 5s sin bloquear."""
        token, repo, file_path, branch = self._get_github_config()
        if not token or not repo:
            return False

        # Si no conocemos el SHA del archivo remoto, intentamos consultarlo primero
        if not self._cached_github_sha:
            self.fetch_github_snapshot()

        json_bytes = json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        b64_content = base64.b64encode(json_bytes).decode("ascii")
        url = self._github_contents_url(repo, file_path)

        for attempt in range(2):
            body_dict: dict[str, Any] = {
                "message": "chore(metrics): update anonymous daily_metrics snapshot",
                "content": b64_content,
                "branch": branch,
            }
            if self._cached_github_sha:
                body_dict["sha"] = self._cached_github_sha

            req = urllib.request.Request(
                url,
                data=json.dumps(body_dict).encode("utf-8"),
                headers=self._github_headers(token),
                method="PUT",
            )
            try:
                with urllib.request.urlopen(req, timeout=self._timeout_seconds) as resp:
                    raw_resp = resp.read().decode("utf-8")
                    resp_data = json.loads(raw_resp) if raw_resp else {}
                    content_obj = resp_data.get("content") if isinstance(resp_data, dict) else None
                    if isinstance(content_obj, dict) and content_obj.get("sha"):
                        self._cached_github_sha = str(content_obj["sha"])
                    return True
            except urllib.error.HTTPError as exc:
                if exc.code in (409, 422) and attempt == 0:
                    # SHA desactualizado: refrescar una vez y reintentar
                    self._cached_github_sha = None
                    self.fetch_github_snapshot()
                    continue
                _sync_logger.warning("GitHub API HTTP %s al guardar snapshot de métricas: %s", exc.code, exc.reason)
                return False
            except Exception as exc:
                _sync_logger.warning("Error subiendo snapshot de métricas a GitHub: %s", exc)
                return False
        return False

    # ------------------------------------------------------------------
    # ARCHIVO DE SNAPSHOT EXTERNO / LOCAL Y POSTGRESQL OPCIONAL
    # ------------------------------------------------------------------

    def _fetch_file_snapshot(self) -> dict[str, Any] | None:
        path = self._get_snapshot_file_path()
        if path is None or not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception as exc:
            _sync_logger.warning("Error leyendo archivo de snapshot %s: %s", path, exc)
            return None

    def _push_file_snapshot(self, snapshot: dict[str, Any]) -> bool:
        path = self._get_snapshot_file_path()
        if path is None:
            return False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(".json.tmp")
            tmp_path.write_text(
                json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            tmp_path.replace(path)
            return True
        except Exception as exc:
            _sync_logger.warning("Error escribiendo archivo de snapshot %s: %s", path, exc)
            return False

    def _fetch_postgres_snapshot(self) -> dict[str, Any] | None:
        pg_url = self._get_postgres_url()
        if not pg_url:
            return None
        try:
            import psycopg2  # type: ignore[import-untyped]
        except ImportError:
            return None

        try:
            conn = psycopg2.connect(pg_url, connect_timeout=int(self._timeout_seconds))
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS drh_metrics_snapshots (
                            snapshot_key TEXT PRIMARY KEY,
                            payload_json TEXT NOT NULL,
                            updated_at_iso TEXT NOT NULL
                        );
                        """
                    )
                    cur.execute(
                        "SELECT payload_json FROM drh_metrics_snapshots WHERE snapshot_key = 'primary';"
                    )
                    row = cur.fetchone()
                    conn.commit()
                    if row and row[0]:
                        parsed = json.loads(str(row[0]))
                        return parsed if isinstance(parsed, dict) else None
            finally:
                conn.close()
        except Exception as exc:
            _sync_logger.warning("Error leyendo snapshot de métricas desde PostgreSQL: %s", exc)
        return None

    def _push_postgres_snapshot(self, snapshot: dict[str, Any]) -> bool:
        pg_url = self._get_postgres_url()
        if not pg_url:
            return False
        try:
            import psycopg2  # type: ignore[import-untyped]
        except ImportError:
            return False

        try:
            payload_str = json.dumps(snapshot, ensure_ascii=False)
            now_iso = str(snapshot.get("exported_at_iso") or "")
            conn = psycopg2.connect(pg_url, connect_timeout=int(self._timeout_seconds))
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS drh_metrics_snapshots (
                            snapshot_key TEXT PRIMARY KEY,
                            payload_json TEXT NOT NULL,
                            updated_at_iso TEXT NOT NULL
                        );
                        """
                    )
                    cur.execute(
                        """
                        INSERT INTO drh_metrics_snapshots (snapshot_key, payload_json, updated_at_iso)
                        VALUES ('primary', %s, %s)
                        ON CONFLICT (snapshot_key) DO UPDATE SET
                            payload_json = EXCLUDED.payload_json,
                            updated_at_iso = EXCLUDED.updated_at_iso;
                        """,
                        (payload_str, now_iso),
                    )
                    conn.commit()
                    return True
            finally:
                conn.close()
        except Exception as exc:
            _sync_logger.warning("Error guardando snapshot de métricas en PostgreSQL: %s", exc)
            return False

    # ------------------------------------------------------------------
    # API PÚBLICA: RESTORE EN STARTUP Y BACKUP ASÍNCRONO CON DEBOUNCE
    # ------------------------------------------------------------------

    def restore_on_startup(self, target_db: DrHDatabase) -> dict[str, int]:
        """Restaura hacia SQLite local todos los snapshots externos disponibles sin pisar filas más nuevas."""
        combined_stats = {"daily_metrics_restored": 0, "visitors_restored": 0}
        if not self.is_configured():
            return combined_stats

        for fetcher in (self._fetch_file_snapshot, self._fetch_postgres_snapshot, self.fetch_github_snapshot):
            try:
                snap = fetcher()
                if isinstance(snap, dict):
                    stats = target_db.restore_metrics_snapshot(snap)
                    combined_stats["daily_metrics_restored"] = max(
                        combined_stats["daily_metrics_restored"],
                        int(stats.get("daily_metrics_restored", 0)),
                    )
                    combined_stats["visitors_restored"] = max(
                        combined_stats["visitors_restored"],
                        int(stats.get("visitors_restored", 0)),
                    )
            except Exception as exc:
                _sync_logger.warning("Error restaurando snapshot de métricas en startup: %s", exc)

        return combined_stats

    def push_monthly_csv_snapshot(
        self,
        snapshot: dict[str, Any],
        month_key: str | None = None,
    ) -> bool:
        """Guarda copia mensual en CSV en el repo privado y en el webhook/carpeta de Google Drive si está activo."""
        csv_text = build_metrics_csv_from_snapshot(snapshot)
        exported_iso = str(snapshot.get("exported_at_iso") or "")
        resolved_month = (month_key or exported_iso[:7] or "historico").strip()[:7]
        csv_filename = f"dr_h_metricas_{resolved_month}.csv"
        ok_any = False

        # 1. Subir copia CSV mensual al repositorio privado de GitHub (metricas_mensuales/dr_h_metricas_YYYY-MM.csv)
        token, repo, _, branch = self._get_github_config()
        if token and repo:
            gh_csv_path = f"metricas_mensuales/{csv_filename}"
            url = self._github_contents_url(repo, gh_csv_path, branch=branch)
            existing_sha = ""
            try:
                get_req = urllib.request.Request(url, headers=self._github_headers(token), method="GET")
                with urllib.request.urlopen(get_req, timeout=self._timeout_seconds) as resp:
                    info = json.loads(resp.read().decode("utf-8"))
                    existing_sha = str(info.get("sha") or "").strip()
            except Exception:
                existing_sha = ""

            put_url = self._github_contents_url(repo, gh_csv_path)
            put_body: dict[str, Any] = {
                "message": f"chore(metrics): backup monthly CSV {csv_filename}",
                "content": base64.b64encode(csv_text.encode("utf-8")).decode("ascii"),
                "branch": branch,
            }
            if existing_sha:
                put_body["sha"] = existing_sha
            try:
                put_req = urllib.request.Request(
                    put_url,
                    data=json.dumps(put_body).encode("utf-8"),
                    headers=self._github_headers(token),
                    method="PUT",
                )
                with urllib.request.urlopen(put_req, timeout=self._timeout_seconds):
                    ok_any = True
            except Exception as exc:
                _sync_logger.warning("Aviso guardando CSV mensual en GitHub privado: %s", exc)

        # 2. Enviar copia CSV a la carpeta de Google Drive de dr.h.argentina@gmail.com si hay webhook configurado
        gdrive_webhook = os.environ.get("METRICS_GDRIVE_WEBHOOK_URL", "").strip()
        if gdrive_webhook:
            folder_id = os.environ.get("METRICS_GDRIVE_FOLDER_ID", DEFAULT_GDRIVE_METRICS_FOLDER_ID).strip()
            payload_bytes = json.dumps(
                {
                    "folder_id": folder_id,
                    "filename": csv_filename,
                    "mime_type": "text/csv",
                    "csv_content": csv_text,
                },
                ensure_ascii=False,
            ).encode("utf-8")
            try:
                req = urllib.request.Request(
                    gdrive_webhook,
                    data=payload_bytes,
                    headers={"Content-Type": "application/json; charset=utf-8"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=self._timeout_seconds):
                    ok_any = True
            except Exception as exc:
                _sync_logger.warning("Aviso enviando CSV mensual a Google Drive webhook: %s", exc)

        return ok_any

    def backup_now(self, source_db: DrHDatabase) -> bool:
        """Ejecuta el respaldo externo inmediatamente (usado por el worker en segundo plano y tests)."""
        if not self.is_configured():
            return False
        try:
            snapshot = source_db.export_metrics_snapshot()
        except Exception as exc:
            _sync_logger.warning("Error exportando snapshot local de métricas: %s", exc)
            return False

        ok_file = self._push_file_snapshot(snapshot)
        ok_pg = self._push_postgres_snapshot(snapshot)
        ok_gh = self.push_github_snapshot(snapshot)
        if ok_gh or os.environ.get("METRICS_GDRIVE_WEBHOOK_URL", "").strip():
            self.push_monthly_csv_snapshot(snapshot)
        return bool(ok_file or ok_pg or ok_gh)

    def trigger_background_backup(self, source_db: DrHDatabase) -> None:
        """Agenda un backup asíncrono con debounce en un hilo daemon sin bloquear jamás al usuario."""
        if not self.is_configured():
            return

        def _worker() -> None:
            try:
                self.backup_now(source_db)
            except Exception as exc:
                _sync_logger.warning("Fallo silencioso en backup asíncrono de métricas: %s", exc)

        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            timer = threading.Timer(self._debounce_seconds, _worker)
            timer.daemon = True
            self._timer = timer
            timer.start()


metrics_sync_manager = MetricsSyncManager()

"""Helper compartido de pruebas: obtiene un token Bearer de Administrador vía `POST /auth/login`.

Las credenciales se leen de la misma configuración que usa el servidor (`ADMIN_USER` / `ADMIN_PASSWORD`
desde el entorno o `.env`); nunca se escriben en el código de las pruebas.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from main import orchestrator


def admin_credentials() -> tuple[str, str]:
    """Devuelve (usuario, contraseña) del Administrador configurado en el servidor."""
    cfg = orchestrator.security_config
    return cfg.admin_user, cfg.admin_password.get_secret_value()


def get_admin_headers(client: TestClient) -> dict[str, str]:
    """Inicia sesión como Administrador y devuelve la cabecera Authorization lista para usar."""
    username, password = admin_credentials()
    resp = client.post("/auth/login", json={"username": username, "password": password})
    if resp.status_code != 200:
        raise AssertionError(f"Login de Administrador falló en pruebas: HTTP {resp.status_code} {resp.text}")
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

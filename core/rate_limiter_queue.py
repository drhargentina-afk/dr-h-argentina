"""Gestor de Cola con Enfriamiento Automático para la API Gratuita de Google AI Studio (`core/rate_limiter_queue.py`).

Intercepta de forma transparente los límites de cuota gratuita (`HTTP 429 Too Many Requests` /
`RESOURCE_EXHAUSTED` de Google AI Studio):
1. Controla la ventana deslizante de peticiones por minuto (`max_rpm`) encolando en orden FIFO.
2. Si la API devuelve `429 Too Many Requests`, calcula con exactitud `Decimal` una pausa de enfriamiento
   exponencial (`base_cooling_seconds * (2 ** attempt)`), aguarda el enfriamiento y reintenta la tarea
   automáticamente sin derribar la aplicación ni generar costos para el ciudadano o la plataforma.
3. Expone métricas auditables en tiempo real (`QueueStatusSnapshot`) para el monitor de salud y la PWA.
"""
from __future__ import annotations

import asyncio
from collections import deque
from decimal import Decimal, ROUND_HALF_UP
import threading
import time
from typing import Callable, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class RateLimitExceededError(RuntimeError):
    """Excepción representativa de un error HTTP 429 Too Many Requests / RESOURCE_EXHAUSTED."""

    def __init__(self, message: str = "HTTP 429 Too Many Requests: Free Tier Quota Reached", retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status_code = 429
        self.retry_after = retry_after


class QueueStatusSnapshot(BaseModel):
    """Estado instantáneo del Gestor de Cola y Enfriamiento Automático de la API Gratuita."""

    model_config = ConfigDict(extra="forbid")

    tier_mode: str = Field(default="GOOGLE_AI_STUDIO_FREE_TIER_GUARD", description="Modo operativo sin costo.")
    max_requests_per_minute: int = Field(..., ge=1, description="Tope de peticiones por minuto configurado.")
    requests_in_last_60s: int = Field(..., ge=0, description="Peticiones ejecutadas en la ventana actual de 60s.")
    current_queue_depth: int = Field(..., ge=0, description="Cantidad de tareas esperando turno en la cola.")
    total_requests_processed: int = Field(..., ge=0, description="Total de operaciones completadas con éxito.")
    total_429_intercepted: int = Field(
        ...,
        ge=0,
        description="Cantidad de errores HTTP 429 interceptados y recuperados por enfriamiento.",
    )
    total_cooling_wait_seconds: Decimal = Field(
        ...,
        ge=Decimal("0.00"),
        description="Tiempo total acumulado de enfriamiento preventivo/reactivo en segundos (Decimal).",
    )
    last_cooling_event_reason: str | None = Field(
        default=None,
        description="Detalle del último evento de enfriamiento aplicado.",
    )


class RateLimitQueueHandler:
    """Manejador de cola FIFO y backoff exponencial ante HTTP 429 en la capa gratuita de Google AI Studio."""

    def __init__(
        self,
        max_rpm: int = 15,
        max_retries: int = 5,
        base_cooling_seconds: Decimal = Decimal("1.50"),
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        if max_rpm <= 0:
            raise ValueError("max_rpm debe ser mayor a cero.")
        if max_retries < 1:
            raise ValueError("max_retries debe ser al menos 1.")
        if base_cooling_seconds <= Decimal("0.00"):
            raise ValueError("base_cooling_seconds debe ser positivo.")

        self.max_rpm = max_rpm
        self.max_retries = max_retries
        self.base_cooling_seconds = base_cooling_seconds.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        self._sleep_fn = sleep_fn or time.sleep

        self._lock = threading.Lock()
        self._timestamps_window: deque[float] = deque()
        self._current_queue_depth: int = 0
        self._total_processed: int = 0
        self._total_429_intercepted: int = 0
        self._total_cooling_seconds: Decimal = Decimal("0.00")
        self._last_cooling_reason: str | None = None

    @staticmethod
    def is_rate_limit_429_error(exc: BaseException) -> bool:
        """Detecta si una excepción corresponde a un límite de cuota HTTP 429 / RESOURCE_EXHAUSTED."""
        if isinstance(exc, RateLimitExceededError):
            return True
        status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        if status_code == 429:
            return True
        response = getattr(exc, "response", None)
        if response is not None and getattr(response, "status_code", None) == 429:
            return True
        msg = str(exc).upper()
        return any(
            token in msg
            for token in (
                "429",
                "TOO MANY REQUESTS",
                "RESOURCE_EXHAUSTED",
                "QUOTA EXCEEDED",
                "RATE_LIMIT_EXCEEDED",
            )
        )

    def calculate_backoff_seconds(self, attempt_index: int, explicit_retry_after: float | None = None) -> Decimal:
        """Calcula con exactitud Decimal la pausa de enfriamiento exponencial para el intento dado.

        Fórmula matemática:
        - `wait = base_cooling_seconds * (2 ** attempt_index)`
        - Si el servidor informó `Retry-After` mayor al cálculo, se respeta ese piso.
        """
        if attempt_index < 0:
            raise ValueError("attempt_index no puede ser negativo.")
        multiplier = Decimal(2 ** attempt_index)
        calculated = (self.base_cooling_seconds * multiplier).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if explicit_retry_after is not None and explicit_retry_after > 0:
            retry_dec = Decimal(str(explicit_retry_after)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if retry_dec > calculated:
                return retry_dec
        return calculated

    def _prune_old_timestamps(self, now: float) -> None:
        while self._timestamps_window and (now - self._timestamps_window[0]) >= 60.0:
            self._timestamps_window.popleft()

    def _acquire_rpm_slot_sync(self) -> None:
        """Verifica la ventana de 60 segundos y aplica enfriamiento preventivo si se alcanzó `max_rpm`."""
        with self._lock:
            now = time.monotonic()
            self._prune_old_timestamps(now)
            if len(self._timestamps_window) >= self.max_rpm:
                oldest = self._timestamps_window[0]
                wait_needed = max(0.05, 60.0 - (now - oldest))
                wait_dec = Decimal(str(round(wait_needed, 2))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                self._total_cooling_seconds = (self._total_cooling_seconds + wait_dec).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                self._last_cooling_reason = (
                    f"Enfriamiento preventivo de ventana RPM ({self.max_rpm} req/min): pausa de {wait_dec}s."
                )
            else:
                wait_needed = 0.0

        if wait_needed > 0.0:
            self._sleep_fn(wait_needed)

        with self._lock:
            now2 = time.monotonic()
            self._prune_old_timestamps(now2)
            self._timestamps_window.append(now2)

    def execute_with_cooling(
        self,
        operation: Callable[..., T],
        *args: object,
        **kwargs: object,
    ) -> T:
        """Encola y ejecuta una función sincrónica, aplicando enfriamiento exponencial si ocurre HTTP 429."""
        with self._lock:
            self._current_queue_depth += 1

        try:
            self._acquire_rpm_slot_sync()
            last_exc: BaseException | None = None

            for attempt in range(self.max_retries + 1):
                try:
                    result = operation(*args, **kwargs)
                    with self._lock:
                        self._total_processed += 1
                    return result
                except BaseException as exc:
                    if not self.is_rate_limit_429_error(exc) or attempt >= self.max_retries:
                        raise
                    last_exc = exc
                    retry_after = getattr(exc, "retry_after", None)
                    wait_dec = self.calculate_backoff_seconds(
                        attempt_index=attempt,
                        explicit_retry_after=retry_after if isinstance(retry_after, (int, float)) else None,
                    )
                    with self._lock:
                        self._total_429_intercepted += 1
                        self._total_cooling_seconds = (self._total_cooling_seconds + wait_dec).quantize(
                            Decimal("0.01"), rounding=ROUND_HALF_UP
                        )
                        self._last_cooling_reason = (
                            f"HTTP 429 interceptado en intento #{attempt + 1}: enfriamiento automático de {wait_dec}s "
                            f"aplicado con costo $0.00."
                        )
                    self._sleep_fn(float(wait_dec))

            if last_exc is not None:
                raise last_exc
            raise RuntimeError("Fallo inesperado en la cola de enfriamiento.")
        finally:
            with self._lock:
                self._current_queue_depth = max(0, self._current_queue_depth - 1)

    async def execute_async_with_cooling(
        self,
        async_operation: Callable[..., object],
        *args: object,
        **kwargs: object,
    ) -> object:
        """Encola y ejecuta una corrutina asincrónica con enfriamiento exponencial ante HTTP 429."""
        with self._lock:
            self._current_queue_depth += 1

        try:
            self._acquire_rpm_slot_sync()
            for attempt in range(self.max_retries + 1):
                try:
                    result = await async_operation(*args, **kwargs)  # type: ignore[misc]
                    with self._lock:
                        self._total_processed += 1
                    return result
                except BaseException as exc:
                    if not self.is_rate_limit_429_error(exc) or attempt >= self.max_retries:
                        raise
                    retry_after = getattr(exc, "retry_after", None)
                    wait_dec = self.calculate_backoff_seconds(
                        attempt_index=attempt,
                        explicit_retry_after=retry_after if isinstance(retry_after, (int, float)) else None,
                    )
                    with self._lock:
                        self._total_429_intercepted += 1
                        self._total_cooling_seconds = (self._total_cooling_seconds + wait_dec).quantize(
                            Decimal("0.01"), rounding=ROUND_HALF_UP
                        )
                        self._last_cooling_reason = (
                            f"HTTP 429 asíncrono interceptado en intento #{attempt + 1}: pausa de {wait_dec}s."
                        )
                    await asyncio.sleep(float(wait_dec))
            raise RuntimeError("Se agotaron los reintentos de enfriamiento asíncrono.")
        finally:
            with self._lock:
                self._current_queue_depth = max(0, self._current_queue_depth - 1)

    def get_status_snapshot(self) -> QueueStatusSnapshot:
        """Devuelve una instantánea tipada con el estado actual de la cola y métricas de enfriamiento."""
        with self._lock:
            self._prune_old_timestamps(time.monotonic())
            return QueueStatusSnapshot(
                tier_mode="GOOGLE_AI_STUDIO_FREE_TIER_GUARD",
                max_requests_per_minute=self.max_rpm,
                requests_in_last_60s=len(self._timestamps_window),
                current_queue_depth=self._current_queue_depth,
                total_requests_processed=self._total_processed,
                total_429_intercepted=self._total_429_intercepted,
                total_cooling_wait_seconds=self._total_cooling_seconds,
                last_cooling_event_reason=self._last_cooling_reason,
            )

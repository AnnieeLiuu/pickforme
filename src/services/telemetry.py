from __future__ import annotations

import contextvars
import logging
from typing import Any, Optional

from src.config import settings

logger = logging.getLogger(__name__)

# Task-local / Thread-local context tracking for active trace and span
_current_trace: contextvars.ContextVar[Optional[Any]] = contextvars.ContextVar("_current_trace", default=None)
_current_span: contextvars.ContextVar[Optional[Any]] = contextvars.ContextVar("_current_span", default=None)


class NoOpSpan:
    """Null-object pattern for Span/Trace when Langfuse is disabled or errors occur."""

    def __init__(self, name: str = "noop") -> None:
        self.name = name

    def end(self, **kwargs: Any) -> NoOpSpan:
        return self

    def update(self, **kwargs: Any) -> NoOpSpan:
        return self

    def span(self, *args: Any, **kwargs: Any) -> NoOpSpan:
        return self

    def generation(self, *args: Any, **kwargs: Any) -> NoOpSpan:
        return self

    def score(self, *args: Any, **kwargs: Any) -> NoOpSpan:
        return self

    def __enter__(self) -> NoOpSpan:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass

    async def __aenter__(self) -> NoOpSpan:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass


class TelemetryManager:
    """Production telemetry facade integrating Langfuse with graceful degradation.

    Strictly satisfies:
    1. Zero live network calls during tests (ENVIRONMENT == 'test' or missing keys).
    2. ContextVars for non-invasive span propagation without modifying service signatures.
    3. Fail-open resilience: Telemetry failures never affect core business logic.
    """

    def __init__(self) -> None:
        self._client: Optional[Any] = None
        self._initialized = False

    def _get_client(self) -> Optional[Any]:
        if not settings.is_langfuse_enabled():
            return None

        if not self._initialized:
            try:
                from langfuse import Langfuse

                self._client = Langfuse(
                    public_key=settings.LANGFUSE_PUBLIC_KEY,
                    secret_key=settings.LANGFUSE_SECRET_KEY,
                    host=settings.LANGFUSE_HOST,
                )
                self._initialized = True
                logger.info(
                    "Langfuse client initialized successfully for host: %s",
                    settings.LANGFUSE_HOST,
                )
            except Exception as e:
                logger.warning(
                    "Failed to initialize Langfuse client (degraded to NoOp): %s",
                    e,
                    exc_info=True,
                )
                self._client = None
                self._initialized = True

        return self._client

    def start_trace(
        self,
        name: str,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        input_data: Optional[Any] = None,
        tags: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Any:
        """Starts a top-level Trace for a user turn or returns a NoOpSpan."""
        # Always reset active child span when a new trace starts
        _current_span.set(None)

        client = self._get_client()
        if not client:
            noop = NoOpSpan(name=name)
            _current_trace.set(noop)
            return noop

        try:
            trace = client.trace(
                name=name,
                session_id=session_id,
                user_id=user_id,
                input=input_data,
                tags=tags or ["fsm", settings.ENVIRONMENT],
                metadata=metadata or {},
            )
            _current_trace.set(trace)
            return trace
        except Exception as e:
            logger.warning("Error starting Langfuse trace %s: %s", name, e, exc_info=False)
            noop = NoOpSpan(name=name)
            _current_trace.set(noop)
            return noop

    def start_span(
        self,
        name: str,
        input_data: Optional[Any] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Any:
        """Starts a child span under the active span or trace."""
        client = self._get_client()
        parent = _current_span.get() or _current_trace.get()

        if not client or not parent or isinstance(parent, NoOpSpan):
            noop = NoOpSpan(name=name)
            _current_span.set(noop)
            return noop

        try:
            span = parent.span(
                name=name,
                input=input_data,
                metadata=metadata or {},
            )
            _current_span.set(span)
            return span
        except Exception as e:
            logger.warning("Error starting Langfuse span %s: %s", name, e, exc_info=False)
            noop = NoOpSpan(name=name)
            _current_span.set(noop)
            return noop

    def end_span(
        self,
        span: Any,
        output_data: Optional[Any] = None,
        status_message: Optional[str] = None,
        level: Optional[str] = None,
    ) -> None:
        """Ends an active span safely."""
        if not span or isinstance(span, NoOpSpan):
            _current_span.set(None)
            return

        try:
            kwargs: dict[str, Any] = {}
            if output_data is not None:
                kwargs["output"] = output_data
            if status_message is not None:
                kwargs["status_message"] = status_message
            if level is not None:
                kwargs["level"] = level
            span.end(**kwargs)
        except Exception as e:
            logger.warning("Error ending Langfuse span: %s", e, exc_info=False)
        finally:
            _current_span.set(None)

    def end_trace(
        self,
        trace: Any,
        output_data: Optional[Any] = None,
    ) -> None:
        """Updates and concludes the top-level trace."""
        if not trace or isinstance(trace, NoOpSpan):
            _current_trace.set(None)
            return

        try:
            if output_data is not None:
                trace.update(output=output_data)
        except Exception as e:
            logger.warning("Error concluding Langfuse trace: %s", e, exc_info=False)
        finally:
            _current_trace.set(None)

    def record_generation(
        self,
        name: str,
        model: str,
        prompt: Any,
        completion: Any,
        model_parameters: Optional[dict[str, Any]] = None,
        usage: Optional[dict[str, int]] = None,
        level: str = "DEFAULT",
        status_message: Optional[str] = None,
        latency_ms: Optional[float] = None,
    ) -> None:
        """Records a model generation observation attached to current active span/trace."""
        client = self._get_client()
        parent = _current_span.get() or _current_trace.get()

        if not client or not parent or isinstance(parent, NoOpSpan):
            return

        try:
            metadata: dict[str, Any] = {}
            if latency_ms is not None:
                metadata["latency_ms"] = latency_ms

            parent.generation(
                name=name,
                model=model,
                input=prompt,
                output=completion,
                model_parameters=model_parameters or {},
                usage=usage,
                level=level,
                status_message=status_message,
                metadata=metadata,
            )
        except Exception as e:
            logger.warning("Error recording Langfuse generation: %s", e, exc_info=False)

    def flush(self) -> None:
        """Flushes buffered events before application shutdown."""
        if self._client:
            try:
                self._client.flush()
            except Exception as e:
                logger.warning("Error flushing Langfuse client on shutdown: %s", e, exc_info=False)


# Global singleton instance
telemetry = TelemetryManager()

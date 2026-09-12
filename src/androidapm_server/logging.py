"""Structured application logging without telemetry payloads or credentials."""

from __future__ import annotations

import logging
import sys
from copy import copy
from typing import Any

import structlog
from structlog.typing import EventDict, WrappedLogger

MAX_EXCEPTION_FRAMES = 32


def _exception_details(error: BaseException) -> dict[str, Any]:
    """Keep code locations, never exception messages, source lines, locals, or SQL."""
    frames: list[dict[str, str | int]] = []
    traceback = error.__traceback__
    while traceback is not None and len(frames) < MAX_EXCEPTION_FRAMES:
        frames.append(
            {
                "function": traceback.tb_frame.f_code.co_name,
                "line": traceback.tb_lineno,
            }
        )
        traceback = traceback.tb_next
    return {"type": type(error).__name__, "frames": frames}


def _safe_exception(_logger: WrappedLogger, _method_name: str, event_dict: EventDict) -> EventDict:
    """Replace every structlog exception with a bounded, message-free diagnostic."""
    info = event_dict.pop("exc_info", None)
    if info is True:
        info = sys.exc_info()
    error = info[1] if isinstance(info, tuple) and len(info) == 3 else info
    if isinstance(error, BaseException):
        event_dict["exception"] = _exception_details(error)
    return event_dict


class SafeExceptionFormatter(logging.Formatter):
    """Protect stdlib/ASGI exception logs as well as the application logger."""

    def format(self, record: logging.LogRecord) -> str:
        """Discard another handler's cached, possibly sensitive exception text."""
        safe_record = copy(record)
        safe_record.exc_text = None
        return super().format(safe_record)

    def formatException(self, ei: Any) -> str:
        """Avoid exception stringification, including chained/grouped DB errors."""
        return str(_exception_details(ei[1]))


def configure_logging(level: str) -> None:
    """Configure stdlib and structlog for JSON production output."""
    logging.basicConfig(stream=sys.stdout, level=level.upper(), format="%(message)s")
    # Uvicorn can log a handled 500 again outside the application boundary.
    formatter = SafeExceptionFormatter("%(message)s")
    for logger in (
        logging.getLogger(),
        logging.getLogger("uvicorn"),
        logging.getLogger("uvicorn.error"),
        logging.getLogger("uvicorn.access"),
    ):
        for handler in logger.handlers:
            handler.setFormatter(formatter)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            _safe_exception,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

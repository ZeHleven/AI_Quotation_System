import contextvars
import json
import logging
import re
import sys
import time
from typing import Any, Dict

from app.core.config import settings


trace_id_var = contextvars.ContextVar("trace_id", default="-")

_SENSITIVE_QUERY_VALUE = re.compile(
    r"(?i)([?&](?:access_token|api[_-]?key|token|secret|signature|sign|password)=)"
    r"([^&\s\"'<>]+)"
)
_AUTHORIZATION_VALUE = re.compile(
    r"(?i)(\bAuthorization\s*[:=]\s*)(?:Bearer\s+)?([^\s,;\"']+)"
)
_BEARER_VALUE = re.compile(r"(?i)(\bBearer\s+)([^\s,;\"']+)")
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:x-webhook-secret|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|jwt[_-]?secret(?:[_-]?key)?|alert[_-]?dingtalk[_-]?secret|"
    r"secret(?:[_-]?key)?|password)"
    r"\b[\"']?\s*[:=]\s*[\"']?)([^\s,;\"'}]+)"
)


def redact_log_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    sanitized = _SENSITIVE_QUERY_VALUE.sub(r"\1[REDACTED]", value)
    sanitized = _AUTHORIZATION_VALUE.sub(r"\1[REDACTED]", sanitized)
    sanitized = _BEARER_VALUE.sub(r"\1[REDACTED]", sanitized)
    return _SENSITIVE_ASSIGNMENT.sub(r"\1[REDACTED]", sanitized)


class TraceIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = trace_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": getattr(record, "trace_id", "-"),
            "message": redact_log_text(record.getMessage()),
        }
        if record.exc_info:
            payload["exception"] = redact_log_text(self.formatException(record.exc_info))
        for key in ("method", "path", "status_code", "duration_ms", "username", "event"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = redact_log_text(value)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(TraceIdFilter())

    root.handlers.clear()
    root.addHandler(handler)

    # httpx INFO messages include complete request URLs. Webhook providers
    # commonly put credentials in the query string, so retain only warnings.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_trace_id() -> str:
    return trace_id_var.get()


def set_trace_id(trace_id: str):
    return trace_id_var.set(trace_id)


def reset_trace_id(token) -> None:
    trace_id_var.reset(token)

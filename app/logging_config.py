import logging
import logging.config
import os
import re
from typing import Any, Iterable


class _SecretMaskingFilter(logging.Filter):
    """Redacts secrets from log messages and arguments.

    Masks common secret patterns such as tokens, API keys, Authorization headers,
    access URLs and webhook secrets. Applies to both record.msg and record.args.
    """

    SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
        # Authorization headers
        (re.compile(r"(Authorization\s*:\s*)(Bearer\s+[A-Za-z0-9._\-]+)", re.IGNORECASE), r"\1***"),
        # Key-value with known secret names
        (
            re.compile(
                r"\b(TELEGRAM_BOT_TOKEN|YOOKASSA_API_KEY|PAYMENT_WEBHOOK_SECRET|DB_ENCRYPTION_KEY|X-API-Key|api_key|apiKey)\b(\s*[:=]\s*)([^\s,;]+)",
                re.IGNORECASE,
            ),
            r"\1\2***",
        ),
        # accessUrl fields inside JSON/text
        (
            re.compile(r"(\baccessUrl\b\s*[:=]\s*[\"'])(.*?)([\"'])", re.IGNORECASE),
            r"\1***\3",
        ),
        # vless/ss/outline style URLs (coarse masking)
        (
            re.compile(r"\b(vless://|ss://|outline://)[^\s]+", re.IGNORECASE),
            r"***",
        ),
    ]

    def _mask(self, text: str) -> str:
        masked = text
        for pattern, repl in self.SECRET_PATTERNS:
            masked = pattern.sub(repl, masked)
        return masked

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            # Mask the main message
            if isinstance(record.msg, str):
                record.msg = self._mask(record.msg)

            # Mask any string arguments
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: self._mask(v) if isinstance(v, str) else v for k, v in record.args.items()}
                elif isinstance(record.args, tuple):
                    record.args = tuple(self._mask(a) if isinstance(a, str) else a for a in record.args)  # type: ignore[assignment]
        except Exception:
            # Never break logging due to masking issues
            pass
        return True


def setup_logging(level: str = "INFO", redirect_print: bool = True) -> None:
    effective_level = os.getenv("LOG_LEVEL", level).upper()
    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s %(levelname)s [%(name)s] %(message)s",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "level": effective_level,
            },
        },
        "root": {
            "handlers": ["console"],
            "level": effective_level,
        },
    }
    logging.config.dictConfig(config)

    # Attach secret masking filter to all handlers
    secret_filter = _SecretMaskingFilter()
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        handler.addFilter(secret_filter)

    # Optionally redirect print() to logging (INFO)
    if redirect_print:
        try:
            import builtins  # type: ignore

            def _print_to_logger(*args: Iterable[Any], **kwargs: Any) -> None:  # type: ignore[override]
                message = " ".join(str(a) for a in args)
                logging.getLogger("stdout").info(message)

            builtins.print = _print_to_logger  # type: ignore[assignment]
        except Exception:
            # If redirect fails, continue without it
            pass




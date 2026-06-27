"""
PCS Business OS - Structured Logging and Telemetry Subsystem
File: /app/core/logging_config.py
"""

import json
import logging
import os
import sys
from datetime import datetime
from typing import Any, Dict, Optional


class JSONFormatter(logging.Formatter):
    """
    Production-grade JSON Formatter for cloud observability engines (e.g., Google Cloud Logging).
    Encodes standard Python log records into structured, high-fidelity JSON lines.
    """
    def __init__(self, **kwargs):
        super().__init__()
        self.service_name = kwargs.get("service_name", "pcs-business-os")
        self.environment = kwargs.get("environment", "production")

    def format(self, record: logging.LogRecord) -> str:
        # Build standard structured log payload
        log_payload: Dict[str, Any] = {
            "timestamp": datetime.utcfromtimestamp(record.created).isoformat() + "Z",
            "severity": record.levelname,
            "message": record.getMessage(),
            "service": self.service_name,
            "environment": self.environment,
            "logger": record.name,
            "source_location": {
                "file": record.pathname,
                "line": record.lineno,
                "function": record.funcName
            }
        }

        # Handle exception information if present
        if record.exc_info:
            log_payload["exception"] = self.formatException(record.exc_info)

        # Inject additional context fields passed via extra={} dictionary
        if hasattr(record, "request_id"):
            log_payload["request_id"] = record.request_id
        if hasattr(record, "user_id"):
            log_payload["user_id"] = record.user_id
        if hasattr(record, "client_ip"):
            log_payload["client_ip"] = record.client_ip
        if hasattr(record, "process_time"):
            log_payload["process_time"] = record.process_time

        return json.dumps(log_payload)


class ConsoleFormatter(logging.Formatter):
    """
    Human-readable development console formatter featuring clean, colored output blocks.
    """
    # ANSI color escape codes for terminal environments
    COLORS = {
        "DEBUG": "\033[36m",      # Cyan
        "INFO": "\033[32m",       # Green
        "WARNING": "\033[33m",    # Yellow
        "ERROR": "\033[31m",      # Red
        "CRITICAL": "\033[41;37m" # Red Background, White Text
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, self.RESET)
        timestamp = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        
        # Build colored log message
        log_msg = (
            f"{color}[{record.levelname}]{self.RESET} "
            f"{timestamp} | "
            f"\033[1m{record.name}\033[0m: {record.getMessage()}"
        )

        # Append exceptions cleanly
        if record.exc_info:
            log_msg += f"\n{self.formatException(record.exc_info)}"
        return log_msg


class SensitiveDataFilter(logging.Filter):
    """
    Security validation filter to ensure sensitive credentials (e.g. passwords, 
    Authorization tokens, API keys) are stripped from stdout logging.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        
        # Perform simple regex or text matches to sanitize logging outputs
        sensitive_keywords = ["password", "token", "client_secret", "api_key", "authorization"]
        for keyword in sensitive_keywords:
            if keyword in message.lower():
                # Perform basic token masking
                cleaned_msg = record.msg
                if isinstance(cleaned_msg, str):
                    record.msg = self.sanitize_string(cleaned_msg)
                break
        return True

    @staticmethod
    def sanitize_string(text: str) -> str:
        """Masks Authorization header contents or API keys."""
        # Mask authorization header values
        text = re_sub_case_insensitive(r'(Bearer\s+)[A-Za-z0-9\-\._~\+\/]+=*', r'\1[REDACTED_BEARER_TOKEN]', text)
        text = re_sub_case_insensitive(r'(api_key["\']?\s*:\s*["\']?)[A-Za-z0-9\-_\.]+(["\']?)', r'\1[REDACTED_API_KEY]\2', text)
        text = re_sub_case_insensitive(r'(password["\']?\s*:\s*["\']?)[^"\']+(["\']?)', r'\1[REDACTED_PASSWORD]\2', text)
        return text


def re_sub_case_insensitive(pattern: str, repl: str, text: str) -> str:
    """Helper to perform case-insensitive substitution without crashing."""
    import re
    try:
        return re.sub(pattern, repl, text, flags=re.IGNORECASE)
    except Exception:
        return text


def configure_system_logging():
    """
    Centralized initialization function to configure Python root loggers.
    Detects running environments (dev/prod) and registers appropriate formats.
    """
    env = os.getenv("ENV", "production").lower()
    log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    
    # Map level string to standard logging constants
    log_levels = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL
    }
    level = log_levels.get(log_level_str, logging.INFO)

    # Instantiate console stream handler
    stream_handler = logging.StreamHandler(sys.stdout)

    # Apply formatter based on active environment settings
    if env == "development":
        stream_handler.setFormatter(ConsoleFormatter())
    else:
        stream_handler.setFormatter(JSONFormatter(
            service_name="pcs-business-os",
            environment=env
        ))

    # Add security validation filters to handler
    stream_handler.addFilter(SensitiveDataFilter())

    # Get root logger and reset existing handlers
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # Remove all current handlers to prevent double-logging issues
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Attach our high-fidelity, unified stream handler
    root_logger.addHandler(stream_handler)

    # Suppress third-party noisemakers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("motor").setLevel(logging.WARNING)
    logging.getLogger("pymongo").setLevel(logging.WARNING)

    logging.info(f"Unified structured logging initialized successfully at level: {log_level_str}")

"""
SmartTrade AI Bot — Loguru Logging Configuration
Structured JSON logs, rotation, level filtering.
"""

import sys

from loguru import logger

from app.config import settings


def setup_logger() -> None:
    """Configure Loguru with structured logging."""
    # Remove default stderr handler
    logger.remove()

    # Console handler — human-readable for development
    logger.add(
        sys.stderr,
        level=settings.LOG_LEVEL,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    # JSON file handler — structured logs for production analysis
    logger.add(
        "logs/smarttrade_{time:YYYY-MM-DD}.json",
        level="DEBUG",
        format="{message}",
        serialize=True,  # JSON output
        rotation="10 MB",
        retention="30 days",
        compression="gz",
        enqueue=True,  # Thread-safe async writes
    )

    # Error-only handler — separate file for quick debugging
    logger.add(
        "logs/errors_{time:YYYY-MM-DD}.log",
        level="ERROR",
        rotation="5 MB",
        retention="60 days",
        compression="gz",
        enqueue=True,
    )

    logger.info(
        "Logger initialised | env={} level={}",
        settings.ENVIRONMENT,
        settings.LOG_LEVEL,
    )

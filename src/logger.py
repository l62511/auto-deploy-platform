from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import PROJECT_ROOT


def setup_logging(verbose: bool = False, log_dir: Path | None = None) -> logging.Logger:
    directory = log_dir or PROJECT_ROOT / "logs"
    directory.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("auto_deploy")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    if logger.handlers:
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(
                handler, RotatingFileHandler
            ):
                handler.setLevel(logging.DEBUG if verbose else logging.INFO)
        return logger

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        directory / "platform.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    logger.addHandler(console)
    logger.addHandler(file_handler)
    return logger

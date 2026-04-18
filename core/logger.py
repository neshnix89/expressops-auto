"""
Standardized logging for ExpressOPS automation tasks.
Each task gets its own log file + shared console output.
"""

import logging
import sys
from pathlib import Path


def get_logger(task_name: str, log_dir: Path | str = "logs", level: str = "INFO") -> logging.Logger:
    """
    Create a logger for a specific task.

    Outputs to both console and logs/<task_name>.log.
    Log file is overwritten each run (keeps only latest).

    Args:
        task_name: Name of the task (used for log filename and logger name).
        log_dir: Directory to store log files.
        level: Logging level (DEBUG, INFO, WARNING, ERROR).

    Returns:
        Configured logger instance.
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(f"expressops.{task_name}")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Avoid duplicate handlers if called multiple times
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-7s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler (overwrite each run)
    log_file = log_dir / f"{task_name}.log"
    file_handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger

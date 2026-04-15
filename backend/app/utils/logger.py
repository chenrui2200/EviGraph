"""
Logger Configuration Module
Provides unified logging management with output to both console and file
"""

import os
import sys
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
import time


class SafeRotatingFileHandler(RotatingFileHandler):
    """
    Windows-safe RotatingFileHandler.

    On Windows, os.rename() fails with PermissionError when the current log file
    is still open (which is always the case in a long-running Flask process).
    This subclass catches the error and retries once after a brief delay,
    then falls back to skipping rotation rather than crashing.
    """

    def doRollover(self):
        if self.stream:
            self.stream.flush()
        try:
            super().doRollover()
        except PermissionError:
            # Windows: file still open, retry after brief delay
            time.sleep(0.5)
            try:
                super().doRollover()
            except PermissionError:
                # Still locked, skip rotation silently
                # Re-open stream in case it was closed
                if not self.stream or self.stream.closed:
                    self.mode = 'a'
                    self.stream = self._open()  # type: ignore[assignment]


def _ensure_utf8_stdout():
    """
    Ensure stdout/stderr use UTF-8 encoding
    Solves Windows console Chinese character encoding issue
    """
    if sys.platform == 'win32':
        # Reconfigure standard output to UTF-8 on Windows
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')


# Log directory
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'logs')


def setup_logger(name: str = 'mirofish', level: int = logging.DEBUG) -> logging.Logger:
    """
    Setup logger

    Args:
        name: Logger name
        level: Log level

    Returns:
        Configured logger
    """
    # Ensure log directory exists
    os.makedirs(LOG_DIR, exist_ok=True)

    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Prevent logs from propagating to root logger to avoid duplicate output
    logger.propagate = False

    # If handlers already exist, don't add duplicates
    if logger.handlers:
        return logger

    # Log formats
    detailed_formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    simple_formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)s: %(message)s',
        datefmt='%H:%M:%S'
    )

    # 1. File handler - detailed logs (named by date, with rotation)
    log_filename = datetime.now().strftime('%Y-%m-%d') + '.log'
    try:
        file_handler = SafeRotatingFileHandler(
            os.path.join(LOG_DIR, log_filename),
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(detailed_formatter)
        logger.addHandler(file_handler)
    except (PermissionError, OSError) as e:
        # Windows 上日志文件被占用时，使用控制台 handler 继续运行
        import warnings
        warnings.warn(f"无法创建日志文件处理器 ({e})，仅使用控制台输出")

    # 2. Console handler - concise logs (INFO and above)
    # NOTE: Don't call _ensure_utf8_stdout() here - it calls sys.stdout.reconfigure()
    # which hangs on Windows when stdout is redirected (e.g. by IDE/debuggers)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(simple_formatter)

    # Add handlers
    logger.addHandler(console_handler)

    return logger


def get_logger(name: str = 'mirofish') -> logging.Logger:
    """
    Get logger (create if not exists)

    Args:
        name: Logger name

    Returns:
        Logger instance
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        return setup_logger(name)
    return logger


# Create default logger
logger = setup_logger()


# Convenience functions
def debug(msg, *args, **kwargs):
    logger.debug(msg, *args, **kwargs)

def info(msg, *args, **kwargs):
    logger.info(msg, *args, **kwargs)

def warning(msg, *args, **kwargs):
    logger.warning(msg, *args, **kwargs)

def error(msg, *args, **kwargs):
    logger.error(msg, *args, **kwargs)

def critical(msg, *args, **kwargs):
    logger.critical(msg, *args, **kwargs)

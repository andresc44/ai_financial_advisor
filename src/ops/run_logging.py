# File to write each digest / watchdog run to a rotating log file, with a timestamp and the user (thread) on every line.
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-15, at Andres's request.

Why: the code reports progress with print(), and cron appended all of it to digest.log / watchdog.log in the project
root forever, without timestamps, with several users' lines interleaved once users ran in parallel.

start_run_log(name) sends every printed line, error, and logging record to logs/<name>.log:
- each line gets a timestamp and the name of the thread that wrote it (set to the user's name while a user runs);
- the file rotates at OPS.LOG_MAX_MB, keeping OPS.LOG_BACKUPS old files;
- when the output isn't a terminal (cron), lines are no longer also written to cron's redirect file, so that file
  only catches crashes that happen before logging starts. In a terminal, or with FINNBOT_LOG_ECHO=1 (e.g. when
  piping output), lines are shown as well.
2026-10-16: write_run_status() leaves a one-line result for the healthchecks.io heartbeat (docs/operations.md).
"""
import io
import logging
import logging.handlers
import os
import sys
import threading
from pathlib import Path

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.config.parameters import params_dict

LOG_DIR = project_root / "logs"
OPS_PARAMS = params_dict["OPS"]
RUN_LOGGER_PREFIX = "finnbot.run"


class _RunFormatter(logging.Formatter):
    """Timestamp and thread for every line; level and logger name too for records from the logging module."""

    def format(self, record: logging.LogRecord) -> str:
        source = "" if record.name.startswith(RUN_LOGGER_PREFIX) else f"{record.levelname} {record.name}: "
        return f"{self.formatTime(record)} [{record.threadName}] {source}{record.getMessage()}"


class _TeeStream(io.TextIOBase):
    """Stands in for sys.stdout / sys.stderr: logs each complete line, and optionally echoes to the real stream."""

    def __init__(self, original, logger: logging.Logger, level: int, echo: bool):
        self._original = original
        self._logger = logger
        self._level = level
        self._echo = echo
        self._pending = threading.local()  # Partial line per thread, so parallel users' lines don't mix

    def write(self, text: str) -> int:
        if self._echo:
            self._original.write(text)
        buffered = getattr(self._pending, "text", "") + text
        *lines, rest = buffered.split("\n")
        self._pending.text = rest
        for line in lines:
            if line.strip():
                self._logger.log(self._level, line)
        return len(text)

    def flush(self) -> None:
        if self._echo:
            self._original.flush()

    def isatty(self) -> bool:
        return self._echo and self._original.isatty()

    @property
    def encoding(self) -> str:
        return getattr(self._original, "encoding", "utf-8")


def start_run_log(name: str) -> Path:
    """Routes this process's output to logs/<name>.log (see module docstring). Returns the log path."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"{name}.log"
    handler = logging.handlers.RotatingFileHandler(
        path,
        maxBytes=int(OPS_PARAMS["LOG_MAX_MB"] * 1024 * 1024),
        backupCount=OPS_PARAMS["LOG_BACKUPS"],
        encoding="utf-8",
    )
    handler.setFormatter(_RunFormatter())

    run_logger = logging.getLogger(f"{RUN_LOGGER_PREFIX}.{name}")
    run_logger.setLevel(logging.INFO)
    run_logger.propagate = False
    run_logger.addHandler(handler)

    echo = sys.stdout.isatty() or os.environ.get("FINNBOT_LOG_ECHO") == "1"
    root = logging.getLogger()
    if not echo:  # Under cron, stop logging-module records from also filling cron's redirect file
        for existing in list(root.handlers):
            if isinstance(existing, logging.StreamHandler) and not isinstance(existing, logging.FileHandler):
                root.removeHandler(existing)
    root.addHandler(handler)
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)

    sys.stdout = _TeeStream(sys.stdout, run_logger, logging.INFO, echo)
    sys.stderr = _TeeStream(sys.stderr, run_logger, logging.WARNING, echo)
    return path


def name_thread(user_name: str) -> None:
    """Labels the current thread with the user it is working for, so their log lines are easy to pick out."""
    threading.current_thread().name = user_name


def write_run_status(name: str, text: str) -> None:
    """
    Claude-authored (2026-10-16): writes logs/<name>.status, the one-line result that scripts/cron_job.sh sends to
    healthchecks.io with the run's exit code. It goes to a third-party service, so it holds counts and periods only:
    never user names, holdings, or amounts (those stay in the log and the admin's Telegram alerts).
    """
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        (LOG_DIR / f"{name}.status").write_text(text.strip() + "\n", encoding="utf-8")
    except OSError as e:
        print(f"[WARNING] Could not write the run status for {name}: {e}")

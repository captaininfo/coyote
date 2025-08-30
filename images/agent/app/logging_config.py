# app/agent/logging_config.py
import logging, os, sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

def configure_logging(log_dir="/app/logs",
                      file_name="coyote_agent.log",
                      level=None) -> logging.Logger:
    level = (level or os.getenv("AGENT_LOG_LEVEL", "INFO")).upper()
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    log_file = log_path / file_name

    root = logging.getLogger("coyote.agent")
    root.setLevel(getattr(logging, level, logging.INFO))

    if not root.handlers:
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s %(message)s")

        # Rotating file (10MB x 5)
        fh = RotatingFileHandler(log_file, maxBytes=10_000_000, backupCount=5)
        fh.setFormatter(fmt)
        root.addHandler(fh)

        # Console
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(sh)

        # Quiet noisy deps unless you need them
        logging.getLogger("neo4j").setLevel(logging.WARNING)
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("watchdog").setLevel(logging.WARNING)

        root.info("Logger initialized: level=%s file=%s", level, log_file)

    return root

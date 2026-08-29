import os
import logging
from logging.handlers import RotatingFileHandler

def setup_logger():
    logger = logging.getLogger("bot")
    logger.setLevel(logging.INFO)

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%d.%m.%Y %H:%M:%S"
    )

    # Console
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logger.addHandler(console_handler)

    # In production /app/data is a writable bind mount. Local scripts and unit
    # tests should remain console-only unless BOT_LOG_FILE is explicitly set.
    log_file = os.getenv("BOT_LOG_FILE")
    if not log_file and os.path.isdir("/app/data"):
        log_file = "/app/data/bot.log"
    if log_file:
        file_handler = RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger

logger = setup_logger()

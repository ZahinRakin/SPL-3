import logging

from backend.core.config import settings

LOGGER_NAME = "graphrag"


def setup_logger() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    # LOG_LEVEL in .env: DEBUG shows the sizes and counts logged throughout the pipeline.
    logger.setLevel(settings.LOG_LEVEL.upper())

    # Guard against duplicate lines if this module is ever imported twice.
    if not logger.handlers:
        formatter = logging.Formatter(
            '%(asctime)s | %(levelname)s | %(name)s | %(message)s'
        )
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    logger.propagate = False

    return logger


logger = setup_logger()

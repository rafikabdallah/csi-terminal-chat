"""Central logging configuration.

Two handlers: a rotating file for the audit trail, and stdout so the
operator can watch live. Security-relevant events use WARNING so they
can be filtered from routine traffic.
"""

import logging
import os
from logging.handlers import RotatingFileHandler

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat.log")

_FORMAT = "%(asctime)s | %(levelname)-8s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logger():
    logger = logging.getLogger("csi")
    logger.setLevel(logging.INFO)

    if logger.handlers:
        return logger

    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    file_handler = RotatingFileHandler(
        LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    return logger


log = setup_logger()

import logging
import os
import sys

LOG_TEMPLATE = (
    "%(levelname)s: [%(asctime)s] %(process)d %(filename)s:%(lineno)d %(message)s"
)


def make_logger(name: str, level=logging.INFO, formatter=LOG_TEMPLATE):
    logger = logging.getLogger(name)
    logger.setLevel(os.environ.get("LOG_LEVEL", level))

    stream_handler = logging.StreamHandler(stream=sys.stdout)
    stream_handler.setFormatter(logging.Formatter(fmt=formatter))

    logger.addHandler(stream_handler)

    return logger

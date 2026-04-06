import logging
import os
import sys

LOG_DIR = "logs"


def setup_logging() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)

    fmt = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(os.path.join(LOG_DIR, "run.log")),
        ],
    )

    # Discarded records get their own handler
    discard_logger = logging.getLogger("discarded")
    discard_logger.setLevel(logging.INFO)
    discard_logger.propagate = False
    fh = logging.FileHandler(os.path.join(LOG_DIR, "discarded.log"))
    fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    discard_logger.addHandler(fh)

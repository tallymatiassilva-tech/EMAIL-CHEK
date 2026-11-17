"""Logging rotativo por dia + log por remessa."""

import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler

from config import LOGS_DIR


def get_logger(nome: str = "reembolsos") -> logging.Logger:
    logger = logging.getLogger(nome)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    arquivo = LOGS_DIR / f"{datetime.now():%Y_%m_%d}.log"
    fh = RotatingFileHandler(
        arquivo, maxBytes=10_000_000, backupCount=10, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    return logger


def log_remessa(numero_remessa: str, mensagem: str, nivel: str = "info"):
    """Grava linha no Log.txt da pasta da remessa."""
    from config import REEMBOLSOS_DIR

    pasta = next(REEMBOLSOS_DIR.rglob(numero_remessa), None)
    if not pasta:
        return
    with open(pasta / "Log.txt", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} | {nivel.upper()} | {mensagem}\n")

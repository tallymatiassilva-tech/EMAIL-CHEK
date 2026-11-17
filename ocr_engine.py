"""Extração de texto: nativo -> OCR fallback, com cache de layout."""

from collections import OrderedDict
from pathlib import Path

import cv2
import numpy as np
import pdfplumber
import pytesseract
from pdf2image import convert_from_path

from config import (
    OCR_CACHE_MAX,
    OCR_DPI,
    OCR_MIN_CHARS_PDF,
    TESSDATA_DIR,
    TESSERACT_CMD,
    TESSERACT_LANG,
)
from log_manager import get_logger
from utils import hash_layout

log = get_logger("ocr")

# ---------------------------------------------------------------------------
# CONFIGURAÇÃO DO TESSERACT
# ---------------------------------------------------------------------------

try:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
except Exception as e:
    log.warning("Não foi possível definir tesseract_cmd: %s", e)


def _montar_config_tessdata() -> str:
    """Monta a config do Tesseract com o tessdata-dir correto.

    O caminho vai SEM aspas: o pytesseract já trata o escape, e aspas
    manuais fazem o Tesseract montar '...tessdata"/por.traineddata'.
    """
    caminho = str(TESSDATA_DIR).strip().strip('"').strip("'").rstrip("\\/")
    return f"--tessdata-dir {caminho}"


_TESSERACT_CONFIG = _montar_config_tessdata()


def _verificar_tesseract() -> bool:
    """Testa o Tesseract fazendo um OCR real numa imagem sintética.

    Não usa get_languages(): nessa versão do pytesseract ele não repassa
    o --tessdata-dir e devolve lista vazia mesmo com o OCR funcionando.
    """
    try:
        versao = pytesseract.get_tesseract_version()
        log.info("Tesseract %s em %s", versao, TESSERACT_CMD)
    except Exception as e:
        log.warning("Tesseract indisponível (%s): %s", TESSERACT_CMD, e)
        return False

    # Imagem sintética com texto conhecido
    try:
        teste = np.full((80, 400, 3), 255, dtype=np.uint8)
        cv2.putText(
            teste,
            "TESTE 123",
            (10, 55),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.5,
            (0, 0, 0),
            3,
        )
        texto = pytesseract.image_to_string(
            teste,
            lang=TESSERACT_LANG,
            config=_TESSERACT_CONFIG,
        )
    except Exception as e:
        log.warning(
            "OCR de teste falhou (idioma '%s' em %s): %s",
            TESSERACT_LANG,
            TESSDATA_DIR,
            e,
        )
        return False

    if not texto.strip():
        log.warning(
            "OCR de teste não retornou texto. Idioma '%s' pode estar "
            "indisponível em %s",
            TESSERACT_LANG,
            TESSDATA_DIR,
        )
        return False

    log.info("Idioma '%s' operacional para OCR", TESSERACT_LANG)
    return True


TESSERACT_OK = _verificar_tesseract()


# ---------------------------------------------------------------------------
# CACHE
# ---------------------------------------------------------------------------


class _CacheLRU:
    def __init__(self, maxsize):
        self.maxsize = maxsize
        self._d = OrderedDict()

    def get(self, k):
        if k in self._d:
            self._d.move_to_end(k)
            return self._d[k]
        return None

    def put(self, k, v):
        self._d[k] = v
        self._d.move_to_end(k)
        if len(self._d) > self.maxsize:
            self._d.popitem(last=False)


_cache_texto = _CacheLRU(OCR_CACHE_MAX)


# ---------------------------------------------------------------------------
# PRÉ-PROCESSAMENTO
# ---------------------------------------------------------------------------


def _preprocessar(img):
    """Binarização + denoise para melhorar OCR."""
    arr = np.array(img)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY) if arr.ndim == 3 else arr
    gray = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return th


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------


def _ocr_imagem(caminho: Path) -> str:
    if not TESSERACT_OK:
        log.warning("OCR ignorado (Tesseract indisponível): %s", caminho.name)
        return ""

    img = cv2.imread(str(caminho))
    if img is None:
        log.warning("Não foi possível ler a imagem: %s", caminho.name)
        return ""

    th = _preprocessar(img)
    try:
        return pytesseract.image_to_string(
            th,
            lang=TESSERACT_LANG,
            config=_TESSERACT_CONFIG,
        )
    except Exception as e:
        log.warning("OCR falhou em %s: %s", caminho.name, e)
        return ""


def _ocr_pdf(caminho: Path) -> str:
    if not TESSERACT_OK:
        log.warning("OCR ignorado (Tesseract indisponível): %s", caminho.name)
        return ""

    try:
        paginas = convert_from_path(str(caminho), dpi=OCR_DPI)
    except Exception as e:
        log.warning("Falha ao converter PDF em imagens (%s): %s", caminho.name, e)
        return ""

    textos = []
    for i, pg in enumerate(paginas, 1):
        th = _preprocessar(pg)
        try:
            textos.append(
                pytesseract.image_to_string(
                    th,
                    lang=TESSERACT_LANG,
                    config=_TESSERACT_CONFIG,
                )
            )
        except Exception as e:
            log.warning("OCR falhou na página %d de %s: %s", i, caminho.name, e)
            textos.append("")

    return "\n".join(textos)


# ---------------------------------------------------------------------------
# EXTRAÇÃO PRINCIPAL
# ---------------------------------------------------------------------------


def extrair_texto(caminho: Path) -> str:
    """Retorna texto do documento. Usa cache por caminho+mtime."""
    caminho = Path(caminho)

    try:
        chave = f"{caminho}:{caminho.stat().st_mtime}"
    except OSError:
        log.warning("Arquivo inacessível: %s", caminho)
        return ""

    cache = _cache_texto.get(chave)
    if cache is not None:
        return cache

    ext = caminho.suffix.lower()
    texto = ""

    if ext == ".pdf":
        try:
            with pdfplumber.open(caminho) as pdf:
                texto = "\n".join((p.extract_text() or "") for p in pdf.pages)
        except Exception as e:
            log.warning("pdfplumber falhou em %s: %s", caminho.name, e)

        if len(texto.strip()) < OCR_MIN_CHARS_PDF:
            log.info("PDF escaneado detectado, aplicando OCR: %s", caminho.name)
            texto = _ocr_pdf(caminho)

    elif ext in (".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp"):
        texto = _ocr_imagem(caminho)

    else:
        texto = ""

    _cache_texto.put(chave, texto)
    return texto


def hash_do_documento(texto: str) -> str:
    return hash_layout(texto)

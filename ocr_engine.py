"""Motor de OCR: extrai texto de PDFs e imagens.

Estrategia:
1. PDF com camada de texto: usa o texto direto (mais rapido e exato).
2. PDF sem camada de texto (escaneado): rasteriza e passa pelo Tesseract.
3. Imagem: passa direto pelo Tesseract.

Pre-processamento antes do OCR:
- Escala de cinza
- Binarizacao adaptativa (melhor que threshold fixo em documento amarelado)
- Remocao de ruido leve
- Upscale quando a imagem e pequena

Pos-processamento do texto:
- Corrige caracteres que o Tesseract confunde em contexto de CNPJ/valor
- Normaliza espacos e quebras de linha
- Remove caracteres de controle
"""

import hashlib
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from config import (
    OCR_CACHE_MAX,
    OCR_DPI,
    OCR_MIN_CHARS_PDF,
    TESSDATA_DIR,
    TESSERACT_CMD,
    TESSERACT_LANG,
)
from log_manager import get_logger

log = get_logger("ocr")

# Cache em memoria: hash do arquivo -> texto extraido.
# Evita repetir OCR do mesmo arquivo quando o pipeline roda varias vezes.
_CACHE: dict[str, str] = {}

EXT_IMAGEM = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif"}


# ---------------------------------------------------------------------------
# CACHE
# ---------------------------------------------------------------------------


def _hash_arquivo(caminho: Path) -> str:
    """Hash do conteudo do arquivo (nao do nome)."""
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(65536), b""):
            h.update(bloco)
    return h.hexdigest()


def _cache_get(chave: str) -> str | None:
    return _CACHE.get(chave)


def _cache_set(chave: str, texto: str) -> None:
    if len(_CACHE) >= OCR_CACHE_MAX:
        # remove o mais antigo (dict preserva ordem de insercao)
        _CACHE.pop(next(iter(_CACHE)), None)
    _CACHE[chave] = texto


# ---------------------------------------------------------------------------
# PRE-PROCESSAMENTO DE IMAGEM
# ---------------------------------------------------------------------------


def _preprocessar_imagem(caminho: Path) -> Path:
    """Prepara a imagem para o OCR.

    Converte para escala de cinza, aplica binarizacao adaptativa e faz
    upscale quando a imagem e pequena. Documento fiscal escaneado em
    papel amarelado melhora muito com binarizacao adaptativa.
    """
    try:
        from PIL import Image, ImageFilter, ImageOps
    except ImportError:
        log.warning("Pillow nao instalado — OCR sem pre-processamento")
        return caminho

    try:
        img = Image.open(caminho)
        img = ImageOps.exif_transpose(img)

        # escala de cinza
        if img.mode != "L":
            img = img.convert("L")

        # upscale quando a imagem e pequena (melhora muito o OCR)
        largura_min = 1600
        if img.width < largura_min:
            fator = largura_min / img.width
            nova = (int(img.width * fator), int(img.height * fator))
            img = img.resize(nova, Image.LANCZOS)

        # reducao de ruido leve
        img = img.filter(ImageFilter.MedianFilter(size=3))

        # binarizacao adaptativa: melhor que threshold fixo em documento
        # com fundo irregular (papel amarelado, sombra de digitalizacao)
        img = _binarizar_adaptativo(img)

        destino = Path(tempfile.gettempdir()) / f"ocr_{caminho.stem}.png"
        img.save(destino, "PNG")
        return destino

    except Exception as e:
        log.warning("Falha no pre-processamento de %s: %s", caminho.name, e)
        return caminho


def _binarizar_adaptativo(img):
    """Binarizacao adaptativa por media local.

    Para cada pixel, compara com a media da vizinhanca. Se o pixel e
    mais escuro que a media menos uma margem, vira preto. Senao, branco.
    Funciona bem em documento com fundo irregular.
    """
    try:
        from PIL import Image, ImageFilter
    except ImportError:
        return img

    # media local com blur grande
    media = img.filter(ImageFilter.BoxBlur(15))

    # aplica: pixel < media - margem -> preto
    pixels = img.load()
    pixels_media = media.load()
    largura, altura = img.size
    margem = 12

    for y in range(altura):
        for x in range(largura):
            if pixels[x, y] < pixels_media[x, y] - margem:
                pixels[x, y] = 0
            else:
                pixels[x, y] = 255

    return img


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------


def _rodar_tesseract(imagem: Path) -> str:
    """Executa o Tesseract e devolve o texto."""
    if not Path(TESSERACT_CMD).is_file():
        log.error("Tesseract nao encontrado em %s", TESSERACT_CMD)
        return ""

    cmd = [
        TESSERACT_CMD,
        str(imagem),
        "stdout",
        "-l",
        TESSERACT_LANG,
        "--psm",
        "6",  # bloco uniforme de texto: melhor para documento fiscal
        "--oem",
        "3",  # LSTM + legacy, mais robusto
        "-c",
        "preserve_interword_spaces=1",
    ]

    # tessdata ao lado do executavel (Tesseract portatil)
    if Path(TESSDATA_DIR).is_dir():
        cmd += ["--tessdata-dir", TESSDATA_DIR]

    try:
        resultado = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",
            errors="replace",
        )
        if resultado.returncode != 0:
            log.warning(
                "Tesseract retornou %d: %s",
                resultado.returncode,
                resultado.stderr[:200],
            )
        return resultado.stdout or ""
    except subprocess.TimeoutExpired:
        log.error("Tesseract excedeu o tempo em %s", imagem.name)
        return ""
    except Exception as e:
        log.error("Falha ao rodar Tesseract: %s", e)
        return ""


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def _texto_de_pdf(caminho: Path) -> str:
    """Tenta extrair a camada de texto do PDF.

    Se o PDF tem camada de texto (gerado por sistema), o texto e exato
    e nao precisa de OCR. Se for escaneado, devolve vazio.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        log.warning("PyMuPDF nao instalado — PDF sempre passa por OCR")
        return ""

    try:
        doc = fitz.open(caminho)
        partes = []
        for pagina in doc:
            partes.append(pagina.get_text("text"))
        doc.close()
        texto = "\n".join(partes)
        return texto if len(texto.strip()) >= OCR_MIN_CHARS_PDF else ""
    except Exception as e:
        log.warning("Falha ao ler PDF %s: %s", caminho.name, e)
        return ""


def _ocr_de_pdf(caminho: Path) -> str:
    """Rasteriza o PDF e passa cada pagina pelo Tesseract."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        log.error("PyMuPDF necessario para OCR de PDF escaneado")
        return ""

    try:
        doc = fitz.open(caminho)
        partes = []
        for i, pagina in enumerate(doc):
            # 300 DPI: padrao para documento fiscal
            pix = pagina.get_pixmap(dpi=OCR_DPI)
            tmp = Path(tempfile.gettempdir()) / f"pdf_{caminho.stem}_p{i}.png"
            pix.save(tmp)

            preparada = _preprocessar_imagem(tmp)
            texto = _rodar_tesseract(preparada)
            partes.append(texto)

            for p in (tmp, preparada):
                try:
                    if p != caminho and p.exists():
                        p.unlink()
                except OSError:
                    pass

        doc.close()
        return "\n".join(partes)
    except Exception as e:
        log.error("Falha no OCR de PDF %s: %s", caminho.name, e)
        return ""


# ---------------------------------------------------------------------------
# POS-PROCESSAMENTO DO TEXTO
# ---------------------------------------------------------------------------

# Correcoes que o Tesseract erra com frequencia em documento fiscal.
# Aplicadas apenas em linhas que tem cara de CNPJ ou valor.
RE_LINHA_CNPJ = re.compile(r"(?:cnpj|cpf|contribuinte|pagador)", re.IGNORECASE)
RE_LINHA_VALOR = re.compile(r"(?:valor|total|r\$)", re.IGNORECASE)

MAPA_OCR_DIGITOS = str.maketrans(
    {
        "O": "0",
        "o": "0",
        "I": "1",
        "l": "1",
        "|": "1",
        "S": "5",
        "B": "8",
        "Z": "2",
        "G": "6",
    }
)


def _corrigir_linha_cnpj(linha: str) -> str:
    """Corrige caracteres confundidos em linha que contem CNPJ."""

    # so corrige o trecho que tem cara de CNPJ (14 digitos com pontuacao)
    def _sub(m):
        return m.group(0).translate(MAPA_OCR_DIGITOS)

    return re.sub(r"[\dOoIl|SBZG][\dOoIl|SBZG.\-/]{12,20}", _sub, linha)


def _pos_processar(texto: str) -> str:
    """Limpa e corrige o texto do OCR."""
    if not texto:
        return ""

    # remove caracteres de controle
    texto = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", texto)

    linhas = []
    for linha in texto.splitlines():
        # corrige CNPJ so em linhas com contexto de CNPJ
        if RE_LINHA_CNPJ.search(linha):
            linha = _corrigir_linha_cnpj(linha)

        # normaliza espacos multiplos
        linha = re.sub(r"[ \t]+", " ", linha).strip()
        linhas.append(linha)

    texto = "\n".join(linhas)

    # remove linhas vazias consecutivas
    texto = re.sub(r"\n{3,}", "\n\n", texto)

    return texto.strip()


# ---------------------------------------------------------------------------
# API PUBLICA
# ---------------------------------------------------------------------------


def extrair_texto(caminho: Path | str) -> str:
    """Extrai o texto de um PDF ou imagem.

    Ordem:
    1. Cache por hash do arquivo
    2. PDF: tenta camada de texto; se falhar, OCR
    3. Imagem: pre-processa e roda OCR
    """
    caminho = Path(caminho)
    if not caminho.is_file():
        log.error("Arquivo nao encontrado: %s", caminho)
        return ""

    chave = _hash_arquivo(caminho)
    em_cache = _cache_get(chave)
    if em_cache is not None:
        log.debug("Cache hit: %s", caminho.name)
        return em_cache

    ext = caminho.suffix.lower()

    if ext == ".pdf":
        texto = _texto_de_pdf(caminho)
        if not texto:
            log.info("PDF sem camada de texto, usando OCR: %s", caminho.name)
            texto = _ocr_de_pdf(caminho)
    elif ext in EXT_IMAGEM:
        preparada = _preprocessar_imagem(caminho)
        texto = _rodar_tesseract(preparada)
        try:
            if preparada != caminho and preparada.exists():
                preparada.unlink()
        except OSError:
            pass
    else:
        log.warning("Extensao nao suportada para OCR: %s", ext)
        return ""

    texto = _pos_processar(texto)
    _cache_set(chave, texto)

    log.info("OCR concluido: %s (%d chars)", caminho.name, len(texto))
    return texto

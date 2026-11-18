"""Motor de OCR: extrai texto de PDFs e imagens.

Estrategia:
1. PDF com camada de texto: usa o texto direto (mais rapido e exato).
2. PDF sem camada de texto (escaneado): rasteriza e passa pelo Tesseract.
3. Imagem: passa direto pelo Tesseract.

Pre-processamento adaptativo por tipo de imagem:
- Documento escaneado (papel): escala de cinza + mediana + binarizacao.
- Print de tela (celular): escala de cinza, SEM binarizacao e SEM mediana
  (filtros borram caracteres finos de fonte de sistema).

Upscale: so em imagens abaixo de 900px de largura. Print de celular tem
1080px+ e NAO e redimensionado — o LANCZOS suaviza o traco fino e o
Tesseract perde caracteres.

Modo do Tesseract adaptativo:
- Print de tela: --psm 4 (colunas de tamanho variavel)
- Documento escaneado: --psm 6 (bloco uniforme)

Fallback de OCR: se o texto sair curto, tenta outros --psm e escolhe o
resultado com mais caracteres. Evita perder linhas quando o modo padrao
nao captura o layout.

Pos-processamento:
- Corrige caracteres que o Tesseract confunde em contexto de CNPJ/valor
- Corrige meses por extenso quebrados pelo OCR (oututro -> outubro)
- Normaliza espacos multiplos SEM remover quebras de linha
- Remove caracteres de controle
"""

import hashlib
import re
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

_CACHE: dict[str, str] = {}
EXT_IMAGEM = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif"}

# Upscale so em imagens MUITO pequenas. Print de celular (1080px+) passa
# intacto — o LANCZOS suaviza o traco fino da fonte e apaga caracteres.
LARGURA_MIN_UPSCALE = 900

# Limite de cores distintas para considerar a imagem um print de tela.
MAX_CORES_PRINT = 64

# Abaixo desse numero de caracteres, o OCR e considerado fraco e o motor
# tenta um segundo modo do Tesseract.
MIN_CHARS_OCR_BOM = 80


# ---------------------------------------------------------------------------
# CACHE
# ---------------------------------------------------------------------------


def _hash_arquivo(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(65536), b""):
            h.update(bloco)
    return h.hexdigest()


def _cache_get(chave: str) -> str | None:
    return _CACHE.get(chave)


def _cache_set(chave: str, texto: str) -> None:
    if len(_CACHE) >= OCR_CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)), None)
    _CACHE[chave] = texto


# ---------------------------------------------------------------------------
# DETECCAO DO TIPO DE IMAGEM
# ---------------------------------------------------------------------------


def _e_print_de_tela(img) -> bool:
    """Detecta se a imagem e um print de tela (celular/desktop).

    Print de tela tem fundo predominantemente claro e poucas cores
    distintas. Documento escaneado tem gradiente de cinza e muitas cores.
    """
    try:
        pequena = img.copy()
        pequena.thumbnail((300, 300))

        cores = pequena.convert("RGB").getcolors(maxcolors=10000)
        if cores is None:
            return False

        if len(cores) > MAX_CORES_PRINT:
            return False

        cores_ord = sorted(cores, reverse=True)[:5]
        total_pixels = sum(c[0] for c in cores)
        if total_pixels == 0:
            return False

        pixels_claros = sum(
            c[0] for c in cores_ord if c[1][0] > 200 and c[1][1] > 200 and c[1][2] > 200
        )
        proporcao_claro = pixels_claros / total_pixels

        return proporcao_claro > 0.4

    except Exception as e:
        log.debug("Falha ao detectar tipo de imagem: %s", e)
        return False


# ---------------------------------------------------------------------------
# PRE-PROCESSAMENTO
# ---------------------------------------------------------------------------


def _preprocessar_imagem(caminho: Path) -> tuple[Path, bool]:
    """Prepara a imagem para o OCR.

    Retorna (caminho_preparado, e_print_de_tela).

    Print de tela: escala de cinza, SEM binarizacao e SEM mediana.
    Documento escaneado: escala de cinza + mediana + binarizacao adaptativa.
    """
    try:
        from PIL import Image, ImageFilter, ImageOps
    except ImportError:
        log.warning("Pillow nao instalado — OCR sem pre-processamento")
        return caminho, False

    try:
        img = Image.open(caminho)
        img = ImageOps.exif_transpose(img)

        e_print = _e_print_de_tela(img)

        if img.mode != "L":
            img = img.convert("L")

        # Upscale so em imagem muito pequena
        if img.width < LARGURA_MIN_UPSCALE:
            fator = LARGURA_MIN_UPSCALE / img.width
            nova = (int(img.width * fator), int(img.height * fator))
            img = img.resize(nova, Image.LANCZOS)
            log.debug("Upscale aplicado: %s -> %dx%d", caminho.name, *nova)

        if e_print:
            # Print de tela: NENHUM filtro. A imagem ja e limpa, e a
            # mediana borra os caracteres finos de fonte de sistema.
            log.debug(
                "Pre-processando como print de tela (sem filtro): %s", caminho.name
            )
        else:
            # Documento escaneado: mediana + binarizacao adaptativa
            img = img.filter(ImageFilter.MedianFilter(size=3))
            img = _binarizar_adaptativo(img)
            log.debug("Pre-processando como documento escaneado: %s", caminho.name)

        destino = Path(tempfile.gettempdir()) / f"ocr_{caminho.stem}.png"
        img.save(destino, "PNG")
        return destino, e_print

    except Exception as e:
        log.warning("Falha no pre-processamento de %s: %s", caminho.name, e)
        return caminho, False


def _binarizar_adaptativo(img):
    """Binarizacao adaptativa por media local (documento escaneado)."""
    try:
        from PIL import ImageFilter
    except ImportError:
        return img

    media = img.filter(ImageFilter.BoxBlur(15))
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


def _rodar_tesseract(
    imagem: Path, e_print: bool = False, psm: str | None = None
) -> str:
    """Executa o Tesseract com o modo adequado ao tipo de imagem.

    Print de tela: --psm 4 (colunas de tamanho variavel).
    Documento escaneado: --psm 6 (bloco uniforme).

    O parametro `psm` permite forcar um modo especifico no fallback.
    """
    if not Path(TESSERACT_CMD).is_file():
        log.error("Tesseract nao encontrado em %s", TESSERACT_CMD)
        return ""

    if psm is None:
        psm = "4" if e_print else "6"

    cmd = [
        TESSERACT_CMD,
        str(imagem),
        "stdout",
        "-l",
        TESSERACT_LANG,
        "--psm",
        psm,
        "--oem",
        "3",
        "-c",
        "preserve_interword_spaces=1",
    ]

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


def _ocr_com_fallback(imagem: Path, e_print: bool) -> str:
    """Roda o Tesseract e, se o texto sair fraco, tenta outro --psm.

    Print de tela: tenta psm 4, depois psm 6 e psm 11.
    Documento escaneado: tenta psm 6, depois psm 4.

    Escolhe o resultado com mais caracteres. Evita perder linhas quando o
    modo padrao nao captura o layout do documento.
    """
    modos = ["4", "6", "11"] if e_print else ["6", "4"]

    melhor = ""
    for modo in modos:
        texto = _rodar_tesseract(imagem, e_print, psm=modo)
        if len(texto.strip()) > len(melhor.strip()):
            melhor = texto
        if len(melhor.strip()) >= MIN_CHARS_OCR_BOM * 3:
            break

    if len(melhor.strip()) < MIN_CHARS_OCR_BOM:
        log.warning(
            "OCR fraco em %s: %d chars (minimo %d)",
            imagem.name,
            len(melhor.strip()),
            MIN_CHARS_OCR_BOM,
        )

    return melhor


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def _texto_de_pdf(caminho: Path) -> str:
    try:
        import fitz
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
    try:
        import fitz
    except ImportError:
        log.error("PyMuPDF necessario para OCR de PDF escaneado")
        return ""

    try:
        doc = fitz.open(caminho)
        partes = []
        for i, pagina in enumerate(doc):
            pix = pagina.get_pixmap(dpi=OCR_DPI)
            tmp = Path(tempfile.gettempdir()) / f"pdf_{caminho.stem}_p{i}.png"
            pix.save(tmp)
            preparada, e_print = _preprocessar_imagem(tmp)
            texto = _ocr_com_fallback(preparada, e_print)
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
# POS-PROCESSAMENTO
# ---------------------------------------------------------------------------

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

MESES_OCR = {
    "janeiro": ["janeiro", "janeuo", "janeire", "janeirc"],
    "fevereiro": ["fevereiro", "fevereirc", "fevereuo"],
    "marco": ["marco", "março", "marc0"],
    "abril": ["abril", "abrll", "abri1"],
    "maio": ["maio", "maic", "mai0"],
    "junho": ["junho", "junhc", "junh0"],
    "julho": ["julho", "julhc", "julh0"],
    "agosto": ["agosto", "agostc", "agost0", "agostto"],
    "setembro": ["setembro", "setembrc", "setembr0", "setembto"],
    "outubro": [
        "outubro",
        "oututro",
        "outubrc",
        "outubr0",
        "outubto",
        "owutro",
        "lowutro",
        "7lowutro",
    ],
    "novembro": ["novembro", "novembrc", "novembr0"],
    "dezembro": ["dezembro", "dezembrc", "dezembr0"],
}

CORRECAO_MESES = {}
for _correto, _variantes in MESES_OCR.items():
    for _v in _variantes:
        CORRECAO_MESES[_v] = _correto


def _corrigir_linha_cnpj(linha: str) -> str:
    def _sub(m):
        return m.group(0).translate(MAPA_OCR_DIGITOS)

    return re.sub(r"[\dOoIl|SBZG][\dOoIl|SBZG.\-/]{12,20}", _sub, linha)


def _corrigir_meses(texto: str) -> str:
    """Corrige meses por extenso quebrados pelo OCR."""
    for variante, correto in CORRECAO_MESES.items():
        if variante != correto:
            texto = re.sub(rf"\b{variante}\b", correto, texto, flags=re.IGNORECASE)

    def _corrigir_dia_mes(m):
        dia_bruto = m.group(1)
        mes = m.group(2)
        dia_limpo = re.sub(r"^[lL7]", "1", dia_bruto)
        return f"{dia_limpo}/{mes}"

    texto = re.sub(
        r"(\d[lL7]?\d?|l\d|[lL])(outubro|novembro|dezembro|setembro|agosto|"
        r"julho|junho|maio|abril|marco|março|fevereiro|janeiro)",
        _corrigir_dia_mes,
        texto,
        flags=re.IGNORECASE,
    )

    return texto


def _pos_processar(texto: str) -> str:
    """Limpa e corrige o texto do OCR SEM perder linhas.

    CRITICO: nao colapsa quebras de linha. O layout em blocos do
    comprovante (Mercado Pago, Nubank) depende das linhas vazias para
    separar secoes. Colapsar demais junta blocos que o pdf_reader
    precisa distinguir.
    """
    if not texto:
        return ""

    # Remove caracteres de controle (mantem \n e \t)
    texto = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", texto)

    texto = _corrigir_meses(texto)

    linhas = []
    for linha in texto.splitlines():
        if RE_LINHA_CNPJ.search(linha):
            linha = _corrigir_linha_cnpj(linha)
        # Normaliza espacos multiplos, mas NAO remove a linha
        linha = re.sub(r"[ \t]+", " ", linha).rstrip()
        linhas.append(linha)

    # Preserva TODAS as linhas (inclusive vazias)
    return "\n".join(linhas).strip()


# ---------------------------------------------------------------------------
# API PUBLICA
# ---------------------------------------------------------------------------


def extrair_texto(caminho: Path | str) -> str:
    """Extrai o texto de um PDF ou imagem.

    Ordem:
    1. Cache por hash do arquivo
    2. PDF: tenta camada de texto; se falhar, OCR
    3. Imagem: detecta tipo, pre-processa e roda OCR com fallback
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
        preparada, e_print = _preprocessar_imagem(caminho)
        texto = _ocr_com_fallback(preparada, e_print)
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
    log.info(
        "OCR concluido: %s (%d chars, %d linhas)",
        caminho.name,
        len(texto),
        texto.count("\n") + 1,
    )
    return texto

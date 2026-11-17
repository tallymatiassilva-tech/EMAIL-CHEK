"""Configuração central do sistema. Ajuste aqui, não nos módulos."""

import os
from pathlib import Path

# ---------- Caminhos base ----------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
REEMBOLSOS_DIR = DATA_DIR / "Reembolsos"
LOGS_DIR = BASE_DIR / "logs"
DB_PATH = DATA_DIR / "reembolsos.db"

# ---------- Exportação ----------
PASTA_EXPORTACAO = DATA_DIR / "exportacoes"

for pasta in (DATA_DIR, REEMBOLSOS_DIR, LOGS_DIR, PASTA_EXPORTACAO):
    pasta.mkdir(parents=True, exist_ok=True)

# ---------- Outlook ----------
PASTA_OUTLOOK_PADRAO = "REEMBOLSO"
PASTA_OUTLOOK = PASTA_OUTLOOK_PADRAO

# ---------- Destino dos anexos ----------
# Pasta raiz onde os anexos serão salvos. Cada remessa vira uma subpasta.
# Pode ser sobrescrita em tempo de execução pelo usuário.
PASTA_ANEXOS_PADRAO = REEMBOLSOS_DIR

ASSUNTO_PREFIXO = "REEMBOLSO DE RETENÇÃO FISCAL"
REGEX_REMESSA = r"REEMBOLSO\s+DE\s+RETEN[ÇC][ÃA]O\s+FISCAL\s*[-–:]\s*(\d{10,20})"

# ---------- OCR ----------
# Tesseract portátil: procura na pasta do projeto, na pasta pai e no sistema.
# A variável de ambiente TESSERACT_CMD, se definida, tem prioridade sobre tudo.
_TESSERACT_CANDIDATOS = [
    BASE_DIR / "tesseract" / "tesseract.exe",
    BASE_DIR / "Tesseract-OCR" / "tesseract.exe",
    BASE_DIR.parent / "tesseract" / "tesseract.exe",
    BASE_DIR.parent / "Tesseract-OCR" / "tesseract.exe",
    Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
    Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
]

TESSERACT_CMD = os.getenv("TESSERACT_CMD", "")
if not TESSERACT_CMD:
    for _cand in _TESSERACT_CANDIDATOS:
        if _cand.is_file():
            TESSERACT_CMD = str(_cand)
            break
    else:
        TESSERACT_CMD = str(_TESSERACT_CANDIDATOS[0])

# Pasta tessdata ao lado do executável (necessária para o Tesseract portátil)
TESSDATA_DIR = str(Path(TESSERACT_CMD).parent / "tessdata")

TESSERACT_LANG = "por"
OCR_DPI = 300
OCR_MIN_CHARS_PDF = 80
OCR_CACHE_MAX = 500

# ---------- Performance / volume ----------
WORKER_THREADS = 4
BATCH_SIZE = 50
EMAIL_MAX_AGE_DAYS = 120

# ---------- Conciliação ----------
TOLERANCIA_VALOR = 0.01
SCORE_PESOS = {"valor": 40, "cnpj": 25, "cliente": 15, "nf": 10, "data": 10}
SCORE_CONCILIADO = 100
SCORE_REVISAO = 80
FUZZY_CLIENTE_MIN = 85
FUZZY_COLUNA_MIN = 80
MAX_COMBINACOES = 500

# ---------- Prazos ----------
PRAZO_RAPIDO = 5
PRAZO_NORMAL = 15


class Status:
    AGUARDANDO = "AGUARDANDO_REEMBOLSO"
    CONFERIDO = "CONFERIDO"
    REVISAO = "REVISAO_MANUAL"
    DIV_CNPJ = "DIVERGENCIA_CNPJ"
    DIV_NF = "DIVERGENCIA_NF"
    DIV_VALOR = "DIVERGENCIA_VALOR"
    DIV_DATA = "DIVERGENCIA_DATA"
    AGRUPADO = "PAGAMENTO_AGRUPADO"
    AGRUPADO_CONF = "PAGAMENTO_AGRUPADO_CONFERIDO"
    DUPLICADO = "DUPLICADO"


class TipoDoc:
    GUIA = "GUIA_FISCAL"
    COMP_JADLOG = "COMPROVANTE_JADLOG"
    COMP_CLIENTE = "COMPROVANTE_REEMBOLSO"
    PLANILHA = "PLANILHA_CLIENTE"
    DESCONHECIDO = "DOCUMENTO_DESCONHECIDO"

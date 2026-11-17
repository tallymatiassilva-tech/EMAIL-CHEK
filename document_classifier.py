"""Classificador de documentos por conteúdo (nunca pelo nome do arquivo).

A separação entre comprovante Jadlog e comprovante do cliente é feita pela
DIREÇÃO do pagamento, não por palavras genéricas como "comprovante" ou
"pagamento":

- Comprovante Jadlog: a Jadlog PAGA o tributo. Ela aparece como cliente
  pagador e o governo/beneficiário recebe.
- Comprovante Cliente: o cliente PAGA a Jadlog. A Jadlog aparece como
  recebedora e o cliente como pagador.
"""

import re
from pathlib import Path

from config import TipoDoc
from excel_reader import ler_planilha_bruta
from log_manager import get_logger
from ocr_engine import extrair_texto

log = get_logger("classifier")

# ---------------------------------------------------------------------------
# SINAIS POR TIPO
# ---------------------------------------------------------------------------

# Sinais fortes de GUIA FISCAL
SINAIS_GUIA = {
    "dare": 30,
    "gnre": 30,
    "documento de arrecadação": 30,
    "guia de recolhimento": 30,
    "guia de pagamento": 25,
    "período de apuração": 20,
    "periodo de apuracao": 20,
    "código de barras": 15,
    "codigo de barras": 15,
    "difal": 20,
    "fecp": 15,
    "antecipação tributária": 15,
    "antecipacao tributaria": 15,
    "tributo": 15,
    "vencimento": 10,
    "cnpj do contribuinte": 15,
    "secretaria de estado da fazenda": 20,
}

# Sinais fortes de COMPROVANTE JADLOG (Jadlog PAGA o tributo)
SINAIS_COMP_JADLOG = {
    "cliente: jadlog": 40,
    "cliente jadlog": 40,
    "beneficiario: governo": 35,
    "beneficiário: governo": 35,
    "beneficiario: secretaria": 30,
    "beneficiário: secretaria": 30,
    "pagamento de títulos": 30,
    "pagamento de titulos": 30,
    "comprovante de pagamento de títulos": 30,
    "comprovante de pagamento de titulos": 30,
    "nr. autenticacao": 15,
    "nr. autenticação": 15,
    "nosso numero": 15,
    "nosso número": 15,
    "banco do brasil cliente": 25,
}

# Sinais fortes de COMPROVANTE CLIENTE (cliente PAGA a Jadlog)
SINAIS_COMP_CLIENTE = {
    "comprovante do pix": 40,
    "comprovante de pix": 40,
    "dados do recebedor": 30,
    "dados do pagador": 30,
    "reembolso de retenção fiscal": 35,
    "reembolso de retencao fiscal": 35,
    "chave pix": 20,
    "id/transação": 20,
    "id/transacao": 20,
    "end-to-end": 20,
    "internet banking": 15,
    "pix enviado": 25,
    "pix recebido": 25,
    "transferência realizada": 20,
    "transferencia realizada": 20,
}

EXT_PLANILHA = {".xlsx", ".xls", ".csv"}
EXT_TEXTO = {".pdf", ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp"}


# ---------------------------------------------------------------------------
# DETECÇÃO DE DIREÇÃO DO PAGAMENTO
# ---------------------------------------------------------------------------


def _extrair_bloco(texto: str, marcador: str, tamanho: int = 400) -> str:
    """Retorna o trecho do texto que vem depois de um marcador."""
    idx = texto.find(marcador)
    if idx < 0:
        return ""
    return texto[idx : idx + tamanho]


def _detectar_direcao(texto: str) -> str | None:
    """Decide se a Jadlog é pagadora ou recebedora.

    Retorna "JADLOG", "CLIENTE" ou None se não for possível decidir.
    """
    t = texto.lower()

    # --- Jadlog como RECEBEDORA → comprovante do cliente ---
    bloco_recebedor = _extrair_bloco(t, "dados do recebedor")
    if bloco_recebedor and "jadlog" in bloco_recebedor:
        return "CLIENTE"

    # --- Jadlog como PAGADORA → comprovante Jadlog ---
    bloco_pagador = _extrair_bloco(t, "dados do pagador")
    if bloco_pagador and "jadlog" in bloco_pagador:
        return "JADLOG"

    # --- Sinais textuais diretos ---
    if "cliente: jadlog" in t or "cliente jadlog" in t:
        return "JADLOG"
    if "banco do brasil cliente: jadlog" in t:
        return "JADLOG"

    # --- Reembolso explícito ---
    if "reembolso de retenção fiscal" in t or "reembolso de retencao fiscal" in t:
        return "CLIENTE"

    return None


# ---------------------------------------------------------------------------
# SCORE
# ---------------------------------------------------------------------------


def _pontuar(texto: str, sinais: dict[str, int]) -> tuple[int, list[str]]:
    """Soma os pesos dos sinais encontrados. Retorna (score, sinais_detectados)."""
    up = texto.upper()
    score = 0
    detectados = []
    for termo, peso in sinais.items():
        if termo.upper() in up:
            score += peso
            detectados.append(termo)
    return score, detectados


# ---------------------------------------------------------------------------
# CLASSIFICAÇÃO
# ---------------------------------------------------------------------------


def classificar(caminho: Path, texto: str | None = None) -> tuple[str, float]:
    """Retorna (tipo, confianca 0-100)."""
    caminho = Path(caminho)
    ext = caminho.suffix.lower()

    if ext in EXT_PLANILHA:
        try:
            df = ler_planilha_bruta(caminho)
            if df is not None and not df.empty:
                return TipoDoc.PLANILHA, 95.0
        except Exception as e:
            log.warning("Planilha ilegível %s: %s", caminho.name, e)
        return TipoDoc.DESCONHECIDO, 0.0

    if ext not in EXT_TEXTO:
        return TipoDoc.DESCONHECIDO, 0.0

    if texto is None:
        texto = extrair_texto(caminho)
    if not texto or len(texto.strip()) < 20:
        return TipoDoc.DESCONHECIDO, 0.0

    # 1) Direção do pagamento tem prioridade absoluta
    direcao = _detectar_direcao(texto)
    if direcao == "JADLOG":
        score, sinais = _pontuar(texto, SINAIS_COMP_JADLOG)
        confianca = min(100.0, 70.0 + score * 0.3)
        log.info(
            "%s -> COMPROVANTE_JADLOG (direção: Jadlog pagadora, score %.1f) sinais: %s",
            caminho.name,
            confianca,
            sinais,
        )
        return TipoDoc.COMP_JADLOG, round(confianca, 1)

    if direcao == "CLIENTE":
        score, sinais = _pontuar(texto, SINAIS_COMP_CLIENTE)
        confianca = min(100.0, 70.0 + score * 0.3)
        log.info(
            "%s -> COMPROVANTE_CLIENTE (direção: Jadlog recebedora, score %.1f) sinais: %s",
            caminho.name,
            confianca,
            sinais,
        )
        return TipoDoc.COMP_CLIENTE, round(confianca, 1)

    # 2) Sem direção clara: pontuação por sinais
    score_guia, sinais_guia = _pontuar(texto, SINAIS_GUIA)
    score_jadlog, sinais_jadlog = _pontuar(texto, SINAIS_COMP_JADLOG)
    score_cliente, sinais_cliente = _pontuar(texto, SINAIS_COMP_CLIENTE)

    scores = {
        TipoDoc.GUIA: score_guia,
        TipoDoc.COMP_JADLOG: score_jadlog,
        TipoDoc.COMP_CLIENTE: score_cliente,
    }

    melhor = max(scores, key=scores.get)
    if scores[melhor] == 0:
        return TipoDoc.DESCONHECIDO, 0.0

    total = sum(scores.values()) or 1
    confianca = round((scores[melhor] / total) * 100, 1)

    # Log dos sinais detectados para diagnóstico
    sinais_por_tipo = {
        TipoDoc.GUIA: sinais_guia,
        TipoDoc.COMP_JADLOG: sinais_jadlog,
        TipoDoc.COMP_CLIENTE: sinais_cliente,
    }
    log.info(
        "%s -> %s (score %.1f) sinais: %s",
        caminho.name,
        melhor,
        confianca,
        sinais_por_tipo[melhor],
    )

    return melhor, confianca

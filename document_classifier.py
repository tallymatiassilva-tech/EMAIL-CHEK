"""Classificador de documentos por conteúdo (nunca pelo nome do arquivo).

Ordem de decisão:
0. Regras por conteúdo: DARE. Roda antes do cache porque o cache devolve
   confiança baixa (~55%), abaixo do mínimo do pipeline para gravar layout.
1. Cache de layout: se o formato já foi visto, o tipo vem do cache.
   Isso resolve os comprovantes de cliente, que têm dezenas de formatos
   (PIX do Nubank, TED do Itaú, transferência do Bradesco, etc.).
2. Guia fiscal: decidida antes da direção do pagamento. Uma guia DARE
   tem sinais próprios que não aparecem em comprovante.
3. Direção do pagamento: distingue comprovante Jadlog de comprovante
   cliente pela posição da Jadlog (pagadora ou recebedora).
4. Pontuação por sinais: fallback quando nada acima decidiu.

A separação entre comprovante Jadlog e comprovante do cliente é feita pela
DIREÇÃO do pagamento, não por palavras genéricas como "comprovante":
- Comprovante Jadlog: a Jadlog PAGA o tributo. Ela aparece como cliente
  pagador e o governo/beneficiário recebe.
- Comprovante Cliente: o cliente PAGA a Jadlog. A Jadlog aparece como
  recebedora e o cliente como pagador.
"""

import re
from pathlib import Path

import layout_cache
from config import TipoDoc
from excel_reader import ler_planilha_bruta
from log_manager import get_logger
from ocr_engine import extrair_texto

log = get_logger("classifier")


# ---------------------------------------------------------------------------
# SINAIS POR TIPO
# ---------------------------------------------------------------------------

# Sinais fortes de GUIA FISCAL.
# São sinais que aparecem na guia e NÃO aparecem no comprovante de pagamento.
SINAIS_GUIA = {
    "dare": 30,
    "gnre": 30,
    "documento de arrecadação": 30,
    "documento de arrecadacao": 30,
    "guia de recolhimento": 30,
    "guia de pagamento": 25,
    "guia nacional de recolhimento": 35,
    "período de apuração": 25,
    "periodo de apuracao": 25,
    "código de barras": 20,
    "codigo de barras": 20,
    "linha digitável": 20,
    "linha digitavel": 20,
    "difal": 20,
    "fecp": 15,
    "antecipação tributária": 15,
    "antecipacao tributaria": 15,
    "tributo": 15,
    "vencimento": 10,
    "cnpj do contribuinte": 20,
    "cnpj contribuinte": 20,
    "secretaria de estado da fazenda": 25,
    "secretaria da fazenda": 20,
    "sefaz": 20,
    "valor total a pagar": 15,
    "total a pagar": 15,
    "valor do documento": 10,
    "inscrição estadual": 15,
    "inscricao estadual": 15,
    "chave de acesso": 15,
    "razão social": 10,
    "razao social": 10,
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
    "comprovante de pagamento de títulos": 35,
    "comprovante de pagamento de titulos": 35,
    "nr. autenticacao": 15,
    "nr. autenticação": 15,
    "nosso numero": 15,
    "nosso número": 15,
    "banco do brasil cliente": 25,
    "pag divers guia": 40,
    "pagador: jadlog": 40,
    "convenio governo": 30,
    "convênio governo": 30,
    "autenticação bancária": 20,
    "autenticacao bancaria": 20,
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
    "comprovante de transferência": 25,
    "comprovante de transferencia": 25,
}

# Sinais que NUNCA devem aparecer numa guia. Se aparecerem, o documento
# é comprovante, não guia. Usado para desempatar.
SINAIS_ANTI_GUIA = {
    "comprovante de pagamento": 30,
    "comprovante do pix": 40,
    "comprovante de pix": 40,
    "dados do recebedor": 30,
    "dados do pagador": 30,
    "autenticação bancária": 25,
    "autenticacao bancaria": 25,
    "nr. autenticacao": 20,
    "nr. autenticação": 20,
    "pix enviado": 30,
    "transferência realizada": 25,
    "transferencia realizada": 25,
}

EXT_PLANILHA = {".xlsx", ".xls", ".csv"}
EXT_TEXTO = {".pdf", ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp"}

# Limite mínimo de score para aceitar a classificação.
# Abaixo disso, o documento vai para desconhecido em vez de chutar.
SCORE_MINIMO = 25


# ---------------------------------------------------------------------------
# REGRAS POR CONTEUDO: DARE
# ---------------------------------------------------------------------------
def _regras_dare(texto: str) -> tuple[str, float] | None:
    """Detecta a DARE (Documento de Arrecadacao de Receitas Estaduais).

    Modelo nacional usado por RR e outros estados. Tem marcas textuais
    inequivocas que nao aparecem em nenhuma outra guia.

    Roda antes do cache de layout porque o cache devolve confianca baixa
    (~55%), abaixo do CONFIANCA_MINIMA_LAYOUT do pipeline, e o layout da
    DARE nunca seria gravado.

    Retorna (tipo, confianca) ou None quando nao e DARE.
    """
    up = texto.upper()

    # Marcas fortes: o titulo do documento
    if "DOCUMENTO DE ARRECADA" in up and "RECEITAS ESTADUAIS" in up:
        return TipoDoc.GUIA, 95.0

    # Marca do formulario impresso
    if "PR_IMPDA" in up:
        return TipoDoc.GUIA, 90.0

    # Combinacao de rotulos exclusivos da DARE
    marcadores = [
        "AUTENTICA" in up and "MEC" in up,
        "CONTRIBUINTE EVENTUAL" in up,
        "SEQ" in up and "FRONTEIRA" in up,
        "AÇÃO FISCAL" in up or "ACAO FISCAL" in up,
    ]
    if sum(marcadores) >= 2:
        return TipoDoc.GUIA, 85.0

    return None


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

    # --- Jadlog como RECEBEDORA -> comprovante do cliente ---
    bloco_recebedor = _extrair_bloco(t, "dados do recebedor")
    if bloco_recebedor and "jadlog" in bloco_recebedor:
        return "CLIENTE"

    # --- Jadlog como PAGADORA -> comprovante Jadlog ---
    bloco_pagador = _extrair_bloco(t, "dados do pagador")
    if bloco_pagador and "jadlog" in bloco_pagador:
        return "JADLOG"

    # --- Sinais textuais diretos ---
    if "cliente: jadlog" in t or "cliente jadlog" in t:
        return "JADLOG"
    if "banco do brasil cliente: jadlog" in t:
        return "JADLOG"

    # --- Formato "PAG DIVERS GUIA C/BAR" (BB) ---
    if "pag divers guia" in t and "jadlog" in t:
        return "JADLOG"
    if "pagador: jadlog" in t:
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


def _e_guia(score_guia: int, sinais_anti_guia: int) -> bool:
    """Decide se o documento é guia.

    Guia só é aceita quando tem score próprio alto E não tem sinais
    fortes de comprovante. Um comprovante de pagamento de DARE contém
    "tributo" e "vencimento" (sinais de guia), mas também "comprovante
    de pagamento" e "autenticação bancária" (sinais anti-guia).
    """
    if score_guia < SCORE_MINIMO:
        return False
    if sinais_anti_guia >= 40:
        return False
    return score_guia > sinais_anti_guia


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

    # ------------------------------------------------------------------
    # 0) REGRAS POR CONTEUDO: DARE.
    # Roda antes do cache porque o cache devolve confianca baixa (~55%),
    # abaixo do minimo do pipeline para gravar o layout aprendido.
    # ------------------------------------------------------------------
    regra_dare = _regras_dare(texto)
    if regra_dare:
        tipo_dare, conf_dare = regra_dare
        log.info(
            "%s -> %s (%.1f%%) por regra DARE",
            caminho.name,
            tipo_dare,
            conf_dare,
        )
        return tipo_dare, conf_dare

    # ------------------------------------------------------------------
    # 1) CACHE DE LAYOUT: formato já visto decide o tipo.
    # Resolve os comprovantes de cliente, que têm dezenas de formatos.
    # ------------------------------------------------------------------
    do_cache = layout_cache.buscar_tipo_por_hash(texto)
    if do_cache:
        tipo_cache, conf_cache = do_cache
        log.info(
            "%s -> %s (%.1f%%) pelo cache de layout",
            caminho.name,
            tipo_cache,
            conf_cache,
        )
        return tipo_cache, conf_cache

    # ------------------------------------------------------------------
    # 2) GUIA tem prioridade sobre a direção do pagamento.
    # Uma guia DARE/GNRE tem sinais próprios que não aparecem em
    # comprovante. Sem essa prioridade, a guia perdia para o comprovante
    # de pagamento da própria guia.
    # ------------------------------------------------------------------
    score_guia, sinais_guia = _pontuar(texto, SINAIS_GUIA)
    score_anti_guia, sinais_anti = _pontuar(texto, SINAIS_ANTI_GUIA)

    if _e_guia(score_guia, score_anti_guia):
        total = score_guia + score_anti_guia or 1
        confianca = min(100.0, (score_guia / total) * 100 + 20)
        log.info(
            "%s -> GUIA_FISCAL (score %d, anti-guia %d) sinais: %s",
            caminho.name,
            score_guia,
            score_anti_guia,
            sinais_guia,
        )
        return TipoDoc.GUIA, round(confianca, 1)

    # ------------------------------------------------------------------
    # 3) Direção do pagamento: distingue comprovante Jadlog de cliente.
    # Só roda depois que a guia foi descartada.
    # ------------------------------------------------------------------
    direcao = _detectar_direcao(texto)

    if direcao == "JADLOG":
        score, sinais = _pontuar(texto, SINAIS_COMP_JADLOG)
        confianca = min(100.0, 60.0 + score * 0.3)
        log.info(
            "%s -> COMPROVANTE_JADLOG (direção: Jadlog pagadora, score %.1f) sinais: %s",
            caminho.name,
            confianca,
            sinais,
        )
        return TipoDoc.COMP_JADLOG, round(confianca, 1)

    if direcao == "CLIENTE":
        score, sinais = _pontuar(texto, SINAIS_COMP_CLIENTE)
        confianca = min(100.0, 60.0 + score * 0.3)
        log.info(
            "%s -> COMPROVANTE_CLIENTE (direção: Jadlog recebedora, score %.1f) sinais: %s",
            caminho.name,
            confianca,
            sinais,
        )
        return TipoDoc.COMP_CLIENTE, round(confianca, 1)

    # ------------------------------------------------------------------
    # 4) Sem direção clara: pontuação por sinais entre os comprovantes.
    # A guia já foi descartada no passo 2.
    # ------------------------------------------------------------------
    score_jadlog, sinais_jadlog = _pontuar(texto, SINAIS_COMP_JADLOG)
    score_cliente, sinais_cliente = _pontuar(texto, SINAIS_COMP_CLIENTE)

    scores = {
        TipoDoc.COMP_JADLOG: score_jadlog,
        TipoDoc.COMP_CLIENTE: score_cliente,
    }
    melhor = max(scores, key=scores.get)

    if scores[melhor] < SCORE_MINIMO:
        log.info(
            "%s -> DESCONHECIDO (score abaixo do mínimo: %d)",
            caminho.name,
            scores[melhor],
        )
        return TipoDoc.DESCONHECIDO, 0.0

    total = sum(scores.values()) or 1
    confianca = round((scores[melhor] / total) * 100, 1)
    sinais_por_tipo = {
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

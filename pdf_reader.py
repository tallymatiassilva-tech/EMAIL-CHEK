"""Extração de campos de guias e comprovantes.

ESTRATÉGIA: extração por âncoras, tolerante a layout embaralhado.

O extrator antigo exigia uma sequência rígida de linhas (CNPJ, depois
vencimento, depois razão social, cada um em sua própria linha). Quando o
OCR reorganizava o texto — o que acontece com frequência em DARE de
Roraima — a regex falhava e o valor caía no fallback, que pegava o maior
número do documento (CGF, número da guia, documento de origem).

Agora cada campo é buscado de forma independente, por uma lista de
estratégias em ordem de confiança. A primeira que acerta vence, e o nome
da regra vencedora fica registrado em _rotulos para diagnóstico.

FORMATOS SUPORTADOS:

GUIAS
1. DARE Roraima, layout A (VINICIUS_SOUSA1):
   CNPJ na mesma linha do CGF e da PLACA.
   Valor em "TOTAL R$ 21,28" e em "TOTAL A PAGAR ... R$ 21,28".
2. DARE Roraima, layout B (Caroline_Peres_Sanches1):
   texto em colunas intercaladas, com números soltos à direita.
   Valor em "Total" seguido de "25,03" em linha separada.
3. DARE Roraima, layout C (JULIANA MINOTTO VENZEL1):
   rótulos empilhados no topo (TOTAL, JUROS, MULTA, ATUALIZAÇÃO,
   PRINCIPAL) e valores empilhados embaixo, na mesma ordem.
   Resolvido pela estratégia 4 (bloco por posição).
4. Guia padrão: "Total a pagar", "Valor total", "Valor da guia".

COMPROVANTES JADLOG (pagamento da guia)
1. Banco do Brasil, layout A (VINICIUS_SOUSA2):
   "VALOR DO DOCUMENTO: R$ 21,28", "DATA DO PAGAMENTO: 13/10/2026".
2. Banco do Brasil, layout B (AMANDA2_1):
   "Valor Total 21,71", "Data do pagamento 24/11/2026".
3. Outros bancos: "Valor pago", "Valor do pagamento", "Valor".

COMPROVANTES DO CLIENTE (reembolso)
Vários bancos e fintechs. Rótulos comuns: "Valor", "Valor pago",
"Valor do PIX", "Valor transferido", "Valor do depósito".

ESTRATÉGIAS DE BUSCA DE VALOR (em ordem):
1. Valor na MESMA linha, depois do rótulo
2. Valor na linha SEGUINTE (layout em colunas)
3. Valor até 2 linhas depois (colunas intercaladas)
4. Bloco de rótulos + bloco de valores alinhados por posição
   (padrão do DARE de Roraima com rótulos empilhados no topo)

REGRA DE SEGURANÇA DO VALOR:
Nenhum valor é aceito se estiver na mesma linha de CGF, número da guia,
documento de origem, nota fiscal, autenticação, código de barras ou
nosso número. Esses campos têm números grandes que o fallback antigo
confundia com valor.

FALLBACK DA GUIA:
Quando a guia não tem valor legível, o pipeline pode usar o valor pago
pela Jadlog como referência. Isso é feito no pipeline, não aqui: este
módulo devolve valor_guia = None e o pipeline decide.
"""

import re
from pathlib import Path

from config import TipoDoc
from log_manager import get_logger

log = get_logger("pdf_reader")

# ---------------------------------------------------------------------------
# PADRÕES BÁSICOS
# ---------------------------------------------------------------------------

RE_CNPJ = re.compile(r"\b(\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2})\b")
RE_DATA = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")
RE_VALOR = re.compile(r"\b(\d{1,3}(?:\.\d{3})*,\d{2})\b")
RE_VALOR_SEM_MILHAR = re.compile(r"\b(\d{1,6},\d{2})\b")

# Linhas que contêm números que NUNCA são valor monetário.
# O fallback antigo pegava o maior número do texto e confundia estes.
LINHAS_PROIBIDAS_VALOR = (
    "cgf",
    "inscricao",
    "inscrição",
    "placa",
    "nosso numero",
    "nosso número",
    "nr. documento",
    "nr documento",
    "documento de origem",
    "doc. de origem",
    "nota fiscal",
    "autenticacao",
    "autenticação",
    "codigo de barras",
    "código de barras",
    "acao fiscal",
    "ação fiscal",
    "parcela",
    "sequencia",
    "sequência",
    "referencia",
    "referência",
    "cnpj",
    "cpf",
    "agencia",
    "agência",
    "conta",
    "banco",
    "convenio",
    "convênio",
    "telefone",
    "sac",
    "ouvidoria",
)

# Rótulos de valor por tipo de documento, em ordem de confiança.
ROTULOS_VALOR_GUIA = (
    r"total\s*a\s*pagar",
    r"valor\s*(?:total|da\s*guia|a\s*pagar|do\s*documento)",
    r"total\s*do\s*documento",
    r"^\s*total\s*$",
    r"^\s*total\b",
    r"principal",
)

ROTULOS_VALOR_JADLOG = (
    r"valor\s*do\s*documento",
    r"valor\s*(?:total|pago|do\s*pagamento|liquido|líquido)",
    r"^\s*valor\b",
)

ROTULOS_VALOR_CLIENTE = (
    r"valor\s*(?:do\s*)?(?:pix|transferencia|transferência|deposito|depósito)",
    r"valor\s*(?:total|pago|do\s*pagamento|liquido|líquido|creditado)",
    r"^\s*valor\b",
)

ROTULOS_DATA_GUIA = (
    r"data\s*(?:limite\s*)?(?:de\s*)?(?:pagamento|vencimento)",
    r"vencimento",
    r"data\s*de\s*emissao",
    r"data\s*de\s*emissão",
)

ROTULOS_DATA_JADLOG = (
    r"data\s*do\s*pagamento",
    r"data\s*da\s*transacao",
    r"data\s*da\s*transação",
    r"data\s*do\s*debito",
    r"data\s*do\s*débito",
    r"^\s*data\b",
)

ROTULOS_DATA_CLIENTE = (
    r"data\s*do\s*pagamento",
    r"data\s*da\s*transferencia",
    r"data\s*da\s*transferência",
    r"data\s*do\s*deposito",
    r"data\s*do\s*depósito",
    r"data\s*do\s*pix",
    r"^\s*data\b",
)

ROTULOS_CNPJ_PAGADOR = (
    r"cnpj\s*/?\s*cpf\s*do\s*pagador",
    r"cnpj\s*do\s*pagador",
    r"cpf\s*/?\s*cnpj\s*do\s*pagador",
    r"pagador",
    r"cnpj\s*/\s*cpf",
    r"cnpj",
)

ROTULOS_NOME_PAGADOR = (
    r"nome\s*do\s*pagador",
    r"razao\s*social\s*do\s*pagador",
    r"razão\s*social\s*do\s*pagador",
    r"pagador",
    r"cliente",
    r"nome",
)

ROTULOS_NUMERO_GUIA = (
    r"n[ºo°]?\s*da\s*guia",
    r"numero\s*da\s*guia",
    r"número\s*da\s*guia",
)

ROTULOS_NUMERO_NF = (
    r"nota\s*fiscal\s*n?[ºo°]?\s*",
    r"n[ºo°]?\s*da\s*nota\s*fiscal",
    r"numero\s*da\s*nota",
    r"número\s*da\s*nota",
    r"nf[-\s]?e?\s*n?[ºo°]?\s*",
)

ROTULOS_TRIBUTO = (
    r"tributo",
    r"codigo\s*do\s*tributo",
    r"código\s*do\s*tributo",
)

# Rótulos que formam o bloco de valores do DARE, na ordem em que aparecem.
# Usado pela estratégia 4 (bloco por posição).
ROTULOS_BLOCO_DARE = (
    r"^\s*total\s*$",
    r"^\s*juros\s*$",
    r"^\s*multa\s*$",
    r"^\s*atualizacao\s*$",
    r"^\s*atualização\s*$",
    r"^\s*principal\s*$",
)


# ---------------------------------------------------------------------------
# NORMALIZAÇÃO
# ---------------------------------------------------------------------------


def _normalizar_texto(texto: str) -> str:
    """Padroniza o texto para as buscas.

    - Converte CRLF em LF
    - Colapsa espaços múltiplos, preservando quebras de linha
    """
    if not texto:
        return ""
    t = texto.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[ \t]+", " ", t)
    return t


def _sem_acento(s: str) -> str:
    """Remove acentos para comparação de rótulos."""
    tabela = str.maketrans(
        "áàâãäéèêëíìîïóòôõöúùûüçÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ",
        "aaaaaeeeeiiiiooooouuuucAAAAAEEEEIIIIOOOOOUUUUC",
    )
    return s.translate(tabela)


def _linha_proibida(linha: str) -> bool:
    """Indica se a linha contém número que não pode ser valor monetário."""
    baixa = _sem_acento(linha.lower())
    return any(p in baixa for p in LINHAS_PROIBIDAS_VALOR)


def _valor_valido(bruto: str) -> float | None:
    """Converte '21,28' ou '1.234,56' em float. None se inválido."""
    if not bruto:
        return None
    s = bruto.strip().replace("R$", "").replace(" ", "")
    s = s.replace(".", "").replace(",", ".")
    try:
        v = float(s)
    except (ValueError, TypeError):
        return None
    # Descarta valores absurdos que indicam número de documento
    if v <= 0 or v > 10_000_000:
        return None
    return round(v, 2)


def _cnpj_valido(bruto: str) -> str | None:
    """Normaliza um CNPJ para 14 dígitos. None se inválido."""
    if not bruto:
        return None
    d = re.sub(r"\D", "", bruto)
    if len(d) != 14:
        return None
    if len(set(d)) == 1:
        return None
    return d


def _data_valida(bruto: str) -> str | None:
    """Valida dd/mm/yyyy e devolve no mesmo formato."""
    if not bruto:
        return None
    m = RE_DATA.search(bruto)
    if not m:
        return None
    s = m.group(1)
    try:
        d, mes, a = s.split("/")
        di, mi, ai = int(d), int(mes), int(a)
    except (ValueError, TypeError):
        return None
    if not (1 <= di <= 31 and 1 <= mi <= 12 and 1990 <= ai <= 2100):
        return None
    return s


# ---------------------------------------------------------------------------
# BUSCA ANCORADA
# ---------------------------------------------------------------------------


def _valor_por_bloco(
    linhas: list[str],
    indice_rotulo: int,
    rotulo: str,
    rotulo_log: dict,
    campo: str,
) -> float | None:
    """Casa rótulo e valor por posição em blocos separados.

    Padrão do DARE de Roraima: os rótulos (TOTAL, JUROS, MULTA,
    ATUALIZAÇÃO, PRINCIPAL) aparecem juntos no topo, e os valores
    aparecem juntos embaixo, na mesma ordem.

    A posição do rótulo é contada entre os rótulos do bloco que
    aparecem ANTES dele no texto, não pelo índice fixo. Assim funciona
    mesmo quando algum rótulo falta ou aparece em ordem diferente.
    """
    # Confirma que o rótulo atual é do bloco
    if not any(
        re.match(p, _sem_acento(linhas[indice_rotulo].lower()))
        for p in ROTULOS_BLOCO_DARE
    ):
        return None

    # Conta quantos rótulos do bloco aparecem ANTES deste
    posicao = 0
    for j in range(indice_rotulo):
        if any(re.match(p, _sem_acento(linhas[j].lower())) for p in ROTULOS_BLOCO_DARE):
            posicao += 1

    # Coleta os valores do bloco, na ordem em que aparecem
    valores = []
    for linha in linhas[indice_rotulo + 1 :]:
        if _linha_proibida(linha):
            continue
        achado = RE_VALOR.search(linha) or RE_VALOR_SEM_MILHAR.search(linha)
        if achado:
            v = _valor_valido(achado.group(1))
            if v is not None:
                valores.append(v)
        # Para quando tiver valores suficientes para cobrir a posição
        if len(valores) > posicao:
            break

    if posicao < len(valores):
        rotulo_log[campo] = f"rotulo:{rotulo}|bloco_posicao_{posicao}"
        return valores[posicao]

    return None


def _buscar_valor_por_rotulo(
    texto: str, rotulos: tuple[str, ...], rotulo_log: dict, campo: str
) -> float | None:
    """Procura o valor associado a um rótulo, tolerando layout embaralhado.

    Estratégias, em ordem:
    1. Valor na MESMA linha, depois do rótulo
    2. Valor na linha SEGUINTE (layout em colunas)
    3. Valor até 2 linhas depois (colunas intercaladas)
    4. Bloco de rótulos + bloco de valores alinhados por posição
       (padrão do DARE de Roraima: rótulos em coluna no topo, valores
       em coluna embaixo, na mesma ordem)

    Descarta linhas proibidas (CGF, número da guia, código de barras).
    Registra em rotulo_log qual regra venceu.
    """
    linhas = texto.split("\n")

    for rotulo in rotulos:
        try:
            re_rotulo = re.compile(rotulo, re.IGNORECASE)
        except re.error:
            continue

        for i, linha in enumerate(linhas):
            if not re_rotulo.search(_sem_acento(linha.lower())):
                continue
            if _linha_proibida(linha) and "total" not in rotulo.lower():
                continue

            # Estratégia 1: mesma linha, depois do rótulo
            m = re_rotulo.search(_sem_acento(linha.lower()))
            if m:
                resto = linha[m.end() :]
                achado = RE_VALOR.search(resto) or RE_VALOR_SEM_MILHAR.search(resto)
                if achado:
                    v = _valor_valido(achado.group(1))
                    if v is not None:
                        rotulo_log[campo] = f"rotulo:{rotulo}|mesma_linha"
                        return v

            # Estratégia 2: linha seguinte
            if i + 1 < len(linhas):
                prox = linhas[i + 1]
                if not _linha_proibida(prox):
                    achado = RE_VALOR.search(prox) or RE_VALOR_SEM_MILHAR.search(prox)
                    if achado:
                        v = _valor_valido(achado.group(1))
                        if v is not None:
                            rotulo_log[campo] = f"rotulo:{rotulo}|linha_seguinte"
                            return v

            # Estratégia 3: até 2 linhas depois (colunas intercaladas)
            for j in range(i + 1, min(i + 3, len(linhas))):
                alvo = linhas[j]
                if _linha_proibida(alvo):
                    continue
                achado = RE_VALOR.search(alvo) or RE_VALOR_SEM_MILHAR.search(alvo)
                if achado:
                    v = _valor_valido(achado.group(1))
                    if v is not None:
                        rotulo_log[campo] = f"rotulo:{rotulo}|linha_{j - i}"
                        return v

            # Estratégia 4: bloco de rótulos + bloco de valores
            v = _valor_por_bloco(linhas, i, rotulo, rotulo_log, campo)
            if v is not None:
                return v

    return None


def _buscar_data_por_rotulo(
    texto: str, rotulos: tuple[str, ...], rotulo_log: dict, campo: str
) -> str | None:
    """Procura a data associada a um rótulo."""
    linhas = texto.split("\n")

    for rotulo in rotulos:
        try:
            re_rotulo = re.compile(rotulo, re.IGNORECASE)
        except re.error:
            continue

        for i, linha in enumerate(linhas):
            if not re_rotulo.search(_sem_acento(linha.lower())):
                continue

            m = re_rotulo.search(_sem_acento(linha.lower()))
            if m:
                resto = linha[m.end() :]
                achado = RE_DATA.search(resto)
                if achado:
                    d = _data_valida(achado.group(1))
                    if d:
                        rotulo_log[campo] = f"rotulo:{rotulo}|mesma_linha"
                        return d

            if i + 1 < len(linhas):
                achado = RE_DATA.search(linhas[i + 1])
                if achado:
                    d = _data_valida(achado.group(1))
                    if d:
                        rotulo_log[campo] = f"rotulo:{rotulo}|linha_seguinte"
                        return d

    return None


def _buscar_cnpj_por_rotulo(
    texto: str, rotulos: tuple[str, ...], rotulo_log: dict, campo: str
) -> str | None:
    """Procura o CNPJ associado a um rótulo."""
    linhas = texto.split("\n")

    for rotulo in rotulos:
        try:
            re_rotulo = re.compile(rotulo, re.IGNORECASE)
        except re.error:
            continue

        for i, linha in enumerate(linhas):
            if not re_rotulo.search(_sem_acento(linha.lower())):
                continue

            m = re_rotulo.search(_sem_acento(linha.lower()))
            if m:
                resto = linha[m.end() :]
                achado = RE_CNPJ.search(resto)
                if achado:
                    c = _cnpj_valido(achado.group(1))
                    if c:
                        rotulo_log[campo] = f"rotulo:{rotulo}|mesma_linha"
                        return c

            if i + 1 < len(linhas):
                achado = RE_CNPJ.search(linhas[i + 1])
                if achado:
                    c = _cnpj_valido(achado.group(1))
                    if c:
                        rotulo_log[campo] = f"rotulo:{rotulo}|linha_seguinte"
                        return c

    return None


def _primeiro_cnpj(texto: str, rotulo_log: dict, campo: str) -> str | None:
    """Último recurso: primeiro CNPJ válido do texto."""
    for m in RE_CNPJ.finditer(texto):
        c = _cnpj_valido(m.group(1))
        if c:
            rotulo_log[campo] = "fallback:primeiro_cnpj"
            return c
    return None


def _maior_valor_seguro(texto: str, rotulo_log: dict, campo: str) -> float | None:
    """Último recurso: maior valor em linha não proibida.

    Diferente do fallback antigo, ignora linhas com CGF, número da guia,
    documento de origem, código de barras e afins.
    """
    candidatos = []
    for linha in texto.split("\n"):
        if _linha_proibida(linha):
            continue
        for m in RE_VALOR.finditer(linha):
            v = _valor_valido(m.group(1))
            if v is not None:
                candidatos.append(v)
    if not candidatos:
        return None
    rotulo_log[campo] = "fallback:maior_valor_seguro"
    return max(candidatos)


def _buscar_numero_por_rotulo(
    texto: str, rotulos: tuple[str, ...], rotulo_log: dict, campo: str
) -> str | None:
    """Procura um número de documento (guia, NF) após o rótulo."""
    linhas = texto.split("\n")
    for rotulo in rotulos:
        try:
            re_rotulo = re.compile(rotulo, re.IGNORECASE)
        except re.error:
            continue
        for i, linha in enumerate(linhas):
            m = re_rotulo.search(_sem_acento(linha.lower()))
            if not m:
                continue
            resto = linha[m.end() :]
            achado = re.search(r"\b(\d{3,20})\b", resto)
            if achado:
                rotulo_log[campo] = f"rotulo:{rotulo}|mesma_linha"
                return achado.group(1)
            if i + 1 < len(linhas):
                achado = re.search(r"\b(\d{3,20})\b", linhas[i + 1])
                if achado:
                    rotulo_log[campo] = f"rotulo:{rotulo}|linha_seguinte"
                    return achado.group(1)
    return None


def _buscar_texto_por_rotulo(
    texto: str, rotulos: tuple[str, ...], rotulo_log: dict, campo: str
) -> str | None:
    """Procura um texto curto após o rótulo."""
    linhas = texto.split("\n")
    for rotulo in rotulos:
        try:
            re_rotulo = re.compile(rotulo, re.IGNORECASE)
        except re.error:
            continue
        for i, linha in enumerate(linhas):
            m = re_rotulo.search(_sem_acento(linha.lower()))
            if not m:
                continue
            resto = linha[m.end() :].strip(" :-\t")
            if resto and len(resto) <= 60:
                rotulo_log[campo] = f"rotulo:{rotulo}|mesma_linha"
                return resto
            if i + 1 < len(linhas):
                prox = linhas[i + 1].strip()
                if prox and len(prox) <= 60:
                    rotulo_log[campo] = f"rotulo:{rotulo}|linha_seguinte"
                    return prox
    return None


def _buscar_nome_razao_social(texto: str, rotulo_log: dict) -> str | None:
    """Procura a razão social do contribuinte na guia.

    No DARE, o nome vem logo abaixo do rótulo "NOME / RAZÃO SOCIAL".
    """
    linhas = texto.split("\n")
    for i, linha in enumerate(linhas):
        baixa = _sem_acento(linha.lower())
        if "nome" in baixa and "razao" in baixa:
            for j in range(i + 1, min(i + 4, len(linhas))):
                cand = linhas[j].strip()
                if not cand or len(cand) < 4:
                    continue
                if _sem_acento(cand.lower()).startswith("cnpj"):
                    continue
                if re.fullmatch(r"[\d\s./-]+", cand):
                    continue
                rotulo_log["cliente"] = f"rotulo:nome_razao_social|linha_{j - i}"
                return cand[:120]
    return None


def _buscar_nome_pagador(texto: str, rotulo_log: dict) -> str | None:
    """Procura o nome do pagador no comprovante."""
    linhas = texto.split("\n")
    for i, linha in enumerate(linhas):
        baixa = _sem_acento(linha.lower())
        if "pagador" in baixa or "cliente:" in baixa:
            m = re.search(r"(?:pagador|cliente)\s*:?\s*(.+)", linha, re.IGNORECASE)
            if m:
                nome = m.group(1).strip(" :-\t")
                if nome and len(nome) >= 3 and not re.fullmatch(r"[\d\s./-]+", nome):
                    rotulo_log["nome_pagador"] = "rotulo:pagador|mesma_linha"
                    return nome[:120]
            if i + 1 < len(linhas):
                prox = linhas[i + 1].strip()
                if prox and len(prox) >= 3 and not re.fullmatch(r"[\d\s./-]+", prox):
                    rotulo_log["nome_pagador"] = "rotulo:pagador|linha_seguinte"
                    return prox[:120]
    return None


def _buscar_autenticacao(texto: str, rotulo_log: dict) -> str | None:
    """Procura o número de autenticação do comprovante."""
    m = re.search(
        r"autentica[cç][aã]o\s*:?\s*([A-Z0-9.\-]{6,})",
        texto,
        re.IGNORECASE,
    )
    if m:
        rotulo_log["autenticacao"] = "rotulo:autenticacao"
        return m.group(1).strip()
    return None


def _buscar_beneficiario(texto: str, rotulo_log: dict) -> str | None:
    """Procura o beneficiário do pagamento."""
    m = re.search(r"benefici[aá]rio\s*:?\s*(.+)", texto, re.IGNORECASE)
    if m:
        nome = m.group(1).strip(" :-\t")
        if nome:
            rotulo_log["beneficiario"] = "rotulo:beneficiario"
            return nome[:120]
    return None


# ---------------------------------------------------------------------------
# EXTRAÇÃO POR TIPO
# ---------------------------------------------------------------------------


def _extrair_guia(texto: str, rotulo_log: dict) -> dict:
    """Extrai os campos de uma guia (DARE ou padrão)."""
    campos = {}

    campos["valor_guia"] = _buscar_valor_por_rotulo(
        texto, ROTULOS_VALOR_GUIA, rotulo_log, "valor_guia"
    )
    if campos["valor_guia"] is None:
        campos["valor_guia"] = _maior_valor_seguro(texto, rotulo_log, "valor_guia")

    campos["data_vencimento"] = _buscar_data_por_rotulo(
        texto, ROTULOS_DATA_GUIA, rotulo_log, "data_vencimento"
    )
    campos["data_emissao"] = _buscar_data_por_rotulo(
        texto,
        (r"data\s*de\s*emissao", r"data\s*de\s*emissão", r"emissao", r"emissão"),
        rotulo_log,
        "data_emissao",
    )

    campos["cnpj"] = _buscar_cnpj_por_rotulo(
        texto, ROTULOS_CNPJ_PAGADOR, rotulo_log, "cnpj"
    )
    if not campos["cnpj"]:
        campos["cnpj"] = _primeiro_cnpj(texto, rotulo_log, "cnpj")

    campos["numero_guia"] = _buscar_numero_por_rotulo(
        texto, ROTULOS_NUMERO_GUIA, rotulo_log, "numero_guia"
    )
    campos["numero_nf"] = _buscar_numero_por_rotulo(
        texto, ROTULOS_NUMERO_NF, rotulo_log, "numero_nf"
    )
    campos["tributo"] = _buscar_texto_por_rotulo(
        texto, ROTULOS_TRIBUTO, rotulo_log, "tributo"
    )
    campos["cliente"] = _buscar_nome_razao_social(texto, rotulo_log)

    return campos


def _extrair_comprovante_jadlog(texto: str, rotulo_log: dict) -> dict:
    """Extrai os campos do comprovante de pagamento da guia."""
    campos = {}

    campos["valor_pago_jadlog"] = _buscar_valor_por_rotulo(
        texto, ROTULOS_VALOR_JADLOG, rotulo_log, "valor_pago_jadlog"
    )
    if campos["valor_pago_jadlog"] is None:
        campos["valor_pago_jadlog"] = _maior_valor_seguro(
            texto, rotulo_log, "valor_pago_jadlog"
        )

    campos["data_pagamento_jadlog"] = _buscar_data_por_rotulo(
        texto, ROTULOS_DATA_JADLOG, rotulo_log, "data_pagamento_jadlog"
    )

    campos["cnpj_pagador"] = _buscar_cnpj_por_rotulo(
        texto, ROTULOS_CNPJ_PAGADOR, rotulo_log, "cnpj_pagador"
    )
    campos["nome_pagador"] = _buscar_nome_pagador(texto, rotulo_log)
    campos["autenticacao"] = _buscar_autenticacao(texto, rotulo_log)
    campos["beneficiario"] = _buscar_beneficiario(texto, rotulo_log)

    return campos


def _extrair_comprovante_cliente(texto: str, rotulo_log: dict) -> dict:
    """Extrai os campos do comprovante de reembolso do cliente."""
    campos = {}

    campos["valor_reembolso"] = _buscar_valor_por_rotulo(
        texto, ROTULOS_VALOR_CLIENTE, rotulo_log, "valor_reembolso"
    )
    if campos["valor_reembolso"] is None:
        campos["valor_reembolso"] = _maior_valor_seguro(
            texto, rotulo_log, "valor_reembolso"
        )

    campos["data_reembolso"] = _buscar_data_por_rotulo(
        texto, ROTULOS_DATA_CLIENTE, rotulo_log, "data_reembolso"
    )

    campos["cnpj_pagador"] = _buscar_cnpj_por_rotulo(
        texto, ROTULOS_CNPJ_PAGADOR, rotulo_log, "cnpj_pagador"
    )
    campos["nome_pagador"] = _buscar_nome_pagador(texto, rotulo_log)

    return campos


# ---------------------------------------------------------------------------
# API PÚBLICA
# ---------------------------------------------------------------------------


def extrair_por_tipo(caminho: Path, tipo: str) -> dict:
    """Extrai os campos de um documento conforme o tipo.

    Devolve um dict com os campos extraídos, mais:
    - texto: o texto bruto (limitado a 8000 chars)
    - _rotulos: qual regra extraiu cada campo (para diagnóstico)
    - confianca: 0-100 conforme quantos campos obrigatórios vieram
    """
    from ocr_engine import extrair_texto

    caminho = Path(caminho)
    texto = _normalizar_texto(extrair_texto(caminho))
    rotulo_log: dict = {}

    if tipo == TipoDoc.GUIA:
        campos = _extrair_guia(texto, rotulo_log)
        obrigatorios = ("valor_guia", "data_vencimento")
    elif tipo == TipoDoc.COMP_JADLOG:
        campos = _extrair_comprovante_jadlog(texto, rotulo_log)
        obrigatorios = ("valor_pago_jadlog", "data_pagamento_jadlog")
    elif tipo == TipoDoc.COMP_CLIENTE:
        campos = _extrair_comprovante_cliente(texto, rotulo_log)
        obrigatorios = ("valor_reembolso", "data_reembolso")
    else:
        campos = {}
        obrigatorios = ()

    # Confiança: proporção de campos obrigatórios preenchidos
    if obrigatorios:
        preenchidos = sum(1 for c in obrigatorios if campos.get(c))
        confianca = int(100 * preenchidos / len(obrigatorios))
    else:
        confianca = 0

    campos["texto"] = texto[:8000]
    campos["_rotulos"] = rotulo_log
    campos["confianca"] = confianca
    campos["tipo"] = tipo

    log.info(
        "Extração %s (%s): %s | confiança %d%%",
        caminho.name,
        tipo,
        ", ".join(f"{k}={v}" for k, v in rotulo_log.items()) or "nenhum campo",
        confianca,
    )

    return campos


def extrair_guia(caminho: Path) -> dict:
    """Atalho para extrair uma guia."""
    return extrair_por_tipo(caminho, TipoDoc.GUIA)


def extrair_comprovante_jadlog(caminho: Path) -> dict:
    """Atalho para extrair um comprovante Jadlog."""
    return extrair_por_tipo(caminho, TipoDoc.COMP_JADLOG)


def extrair_comprovante_cliente(caminho: Path) -> dict:
    """Atalho para extrair um comprovante do cliente."""
    return extrair_por_tipo(caminho, TipoDoc.COMP_CLIENTE)

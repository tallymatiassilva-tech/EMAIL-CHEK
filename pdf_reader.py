"""Extracao de campos estruturados por tipo de documento.

Etapa 5: consulta o cache de layouts por hash antes de tentar todos os
rotulos. Se o formato ja foi visto, usa os rotulos que funcionaram.

Inclui extracao de CNPJ da guia (contribuinte) e do comprovante (pagador),
alem da razao social do tomador na guia.

DATAS: todas as datas sao devolvidas no formato dd/mm/yyyy, que e o que a
planilha de exportacao espera. Aceita quatro formatos de entrada:
- numerico: 21/08/2026, 21-08-2026, 21.08.2026
- por extenso: 21/agosto/2026, 9/setembro/2026 (comprovante Mercado Pago)
- abreviado: 23 OUT 2026, 15 SET 2026 (comprovante Nubank)
- ISO: 2026-08-21

CONTRATO COM O PIPELINE:
Cada extrator devolve, alem dos campos de negocio, duas chaves internas:
- `_rotulos`: mapa campo -> rotulo que funcionou na extracao.
- `texto`: o texto bruto extraido, usado pelo hash do layout.

A gravacao do layout NAO acontece aqui. Quem decide gravar e o pipeline.
"""

import re
from pathlib import Path

import layout_cache
from config import TipoDoc
from log_manager import get_logger
from ocr_engine import extrair_texto
from utils import normalizar_cnpj, parse_data, parse_valor, validar_cnpj
from valor_tolerante import valor_tolerante

log = get_logger("pdf_reader")

RE_CNPJ = re.compile(r"\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}")
RE_NF_SECAO = re.compile(
    r"notas?\s*fiscais?[^\n]*\n(.{0,400})", re.IGNORECASE | re.DOTALL
)
RE_NF = re.compile(r"\b(\d{3,9})\b")
RE_VALOR_BRL = re.compile(r"R?\$?\s*(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})")
RE_DATA_BR = re.compile(r"(\d{1,2}[/.-]\d{1,2}[/.-]\d{4})")

# Data com mes por extenso: "21/agosto/2026", "9/setembro/2026"
RE_DATA_EXTENSO = re.compile(
    r"(\d{1,2})\s*/\s*"
    r"(janeiro|fevereiro|mar[cç]o|abril|maio|junho|julho|agosto|setembro|"
    r"outubro|novembro|dezembro)"
    r"\s*/\s*(\d{4})",
    re.IGNORECASE,
)

# Data com mes abreviado em maiusculas: "23 OUT 2026", "15 SET 2026"
RE_DATA_ABREV = re.compile(
    r"(\d{1,2})\s+"
    r"(JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)"
    r"\s+(\d{4})",
    re.IGNORECASE,
)

MESES_EXTENSO = {
    "janeiro": 1,
    "fevereiro": 2,
    "março": 3,
    "marco": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}

MESES_ABREV = {
    "JAN": 1,
    "FEV": 2,
    "MAR": 3,
    "ABR": 4,
    "MAI": 5,
    "JUN": 6,
    "JUL": 7,
    "AGO": 8,
    "SET": 9,
    "OUT": 10,
    "NOV": 11,
    "DEZ": 12,
}

UFS = "AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RS|RO|RR|SC|SP|SE|TO"

BANCOS = (
    "ITAU",
    "ITAÚ",
    "BRADESCO",
    "SANTANDER",
    "BANCO DO BRASIL",
    "BB",
    "CAIXA",
    "NUBANK",
    "INTER",
    "SICOOB",
    "SICREDI",
    "BTG",
    "MERCANTIL",
)

CNPJS_A_IGNORAR = (
    "BANCO DO BRASIL",
    "CAIXA ECONOMICA",
    "PREFEITURA",
    "SECRETARIA",
    "FAZENDA",
    "RECEITA FEDERAL",
    "SEFAZ",
    "TESOURO",
    "ITAU",
    "BRADESCO",
    "SANTANDER",
)

ROTULOS_VALOR_GENERICOS = [
    r"valor\s*(?:do\s*)?(?:documento|pagamento|pago|total|l[ií]quido|bruto)",
    r"total\s*(?:a\s*pagar|pago|do\s*documento)?",
    r"principal",
    r"valor",
]

ROTULOS_DATA_GENERICOS = [
    r"data\s*(?:do\s*)?(?:pagamento|transa[çc][ãa]o|emiss[ãa]o|vencimento|limite\s*pagamento)",
    r"vencimento",
    r"emiss[ãa]o",
    r"data",
]

ROTULOS_CNPJ_GUIA = [
    r"cnpj\s*(?:do\s*)?contribuinte",
    r"cnpj\s*(?:do\s*)?(?:destinat[aá]rio|tomador|devedor)",
    r"cpf\s*/?\s*cnpj\s*(?:do\s*)?(?:contribuinte|destinat[aá]rio|tomador)",
    r"contribuinte\s*[:.]?\s*(?:cnpj|cpf)",
    r"inscri[çc][ãa]o\s*(?:estadual|no\s*estado)",
    r"cnpj\s*[:.]?\s*",
    r"cpf\s*/?\s*cnpj",
    r"raz[ãa]o\s*social",
    r"nome\s*(?:do\s*)?(?:contribuinte|destinat[aá]rio|tomador)",
]

ROTULOS_CNPJ_PAGADOR = [
    r"cnpj\s*(?:do\s*)?pagador",
    r"pagador\s*[:.]?\s*(?:cnpj)?",
    r"origem",
    r"debitad[oa]",
]

ROTULOS_RAZAO_SOCIAL = [
    r"raz[ãa]o\s*social",
    r"nome\s*(?:do\s*)?(?:contribuinte|destinat[aá]rio|tomador)",
    r"contribuinte",
    r"tomador\s*(?:de\s*servi[çc]os)?",
    r"destinat[aá]rio",
]

MAPA_OCR = str.maketrans(
    {
        "O": "0",
        "o": "0",
        "I": "1",
        "l": "1",
        "|": "1",
        "S": "5",
        "s": "5",
        "B": "8",
        "Z": "2",
        "z": "2",
        "G": "6",
    }
)


# ---------------------------------------------------------------------------
# VALIDACAO DE CNPJ
# ---------------------------------------------------------------------------


def _corrigir_ocr_cnpj(texto: str) -> str:
    return texto.translate(MAPA_OCR)


def _cnpj_valido(texto: str) -> str:
    cnpj = normalizar_cnpj(texto)
    if cnpj and validar_cnpj(cnpj):
        return cnpj
    corrigido = normalizar_cnpj(_corrigir_ocr_cnpj(texto))
    if corrigido and validar_cnpj(corrigido):
        return corrigido
    return ""


# ---------------------------------------------------------------------------
# HELPERS DE EXTRACAO
# ---------------------------------------------------------------------------


def _primeiro_cnpj(texto: str) -> str:
    for m in RE_CNPJ.finditer(texto):
        cnpj = _cnpj_valido(m.group(0))
        if cnpj:
            return cnpj
    return ""


def _cnpj_apos_rotulo(texto: str, rotulos: list[str]) -> str:
    for rotulo in rotulos:
        padrao = rf"{rotulo}[\s\S]{{0,200}}?({RE_CNPJ.pattern})"
        for m in re.finditer(padrao, texto, re.IGNORECASE):
            cnpj = _cnpj_valido(m.group(1))
            if cnpj:
                return cnpj
    return ""


def _razao_social_apos_rotulo(texto: str, rotulos: list[str]) -> str:
    for rotulo in rotulos:
        padrao = rf"{rotulo}[\s:.\-]*\n?\s*([A-ZÀ-Ú0-9][A-ZÀ-Ú0-9&\s.\-/]{{4,90}})"
        m = re.search(padrao, texto, re.IGNORECASE)
        if m:
            nome = re.sub(r"\s+", " ", m.group(1)).strip(" .-")
            if len(nome) >= 4:
                return nome
    return ""


def _cnpj_por_proximidade_nome(texto: str, nome: str) -> str:
    if not nome:
        return ""
    nome_re = re.escape(nome[:40])
    padrao = rf"{nome_re}[\s\S]{{0,300}}?({RE_CNPJ.pattern})"
    for m in re.finditer(padrao, texto, re.IGNORECASE):
        cnpj = _cnpj_valido(m.group(1))
        if cnpj:
            return cnpj
    return ""


def _cnpj_nao_e_orgao(cnpj: str, texto: str) -> bool:
    if not cnpj:
        return True
    pos = texto.find(cnpj)
    if pos < 0:
        fmt = f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"
        pos = texto.find(fmt)
    if pos < 0:
        return True
    inicio = max(0, pos - 120)
    contexto = texto[inicio:pos].upper()
    return not any(orgao in contexto for orgao in CNPJS_A_IGNORAR)


def _cnpj_guia(texto: str, nome_tomador: str = "") -> str:
    cnpj = _cnpj_apos_rotulo(texto, ROTULOS_CNPJ_GUIA)
    if cnpj and _cnpj_nao_e_orgao(cnpj, texto):
        return cnpj
    cnpj = _cnpj_por_proximidade_nome(texto, nome_tomador)
    if cnpj and _cnpj_nao_e_orgao(cnpj, texto):
        return cnpj
    for m in RE_CNPJ.finditer(texto):
        cnpj = _cnpj_valido(m.group(0))
        if cnpj and _cnpj_nao_e_orgao(cnpj, texto):
            return cnpj
    return _primeiro_cnpj(texto)


def _cnpj_pagador_comprovante(texto: str, nome_pagador: str) -> str:
    if nome_pagador:
        linha = re.search(
            rf"{re.escape(nome_pagador)}[^\n]{{0,80}}({RE_CNPJ.pattern})",
            texto,
        )
        if linha:
            cnpj = _cnpj_valido(linha.group(1))
            if cnpj:
                return cnpj
    cnpj = _cnpj_apos_rotulo(texto, ROTULOS_CNPJ_PAGADOR)
    if cnpj:
        return cnpj
    return _primeiro_cnpj(texto)


def _valor_apos_rotulo(texto: str, rotulos: list[str]) -> tuple[float | None, str]:
    for rotulo in rotulos:
        padrao = rf"{rotulo}[\s\S]{{0,150}}?{RE_VALOR_BRL.pattern}"
        m = re.search(padrao, texto, re.IGNORECASE)
        if m:
            v = parse_valor(m.group(1))
            if v:
                return v, rotulo
    return None, ""


def _data_apos_rotulo(texto: str, rotulos: list[str]) -> tuple[str | None, str]:
    """Retorna (data em dd/mm/yyyy, rotulo que casou).

    Aceita tres formatos:
    - numerico: 21/08/2026, 21-08-2026, 21.08.2026
    - por extenso: 21/agosto/2026, 9/setembro/2026 (Mercado Pago)
    - abreviado: 23 OUT 2026, 15 SET 2026 (Nubank)

    Sempre devolve no formato dd/mm/yyyy, que e o que a planilha espera.
    """
    for rotulo in rotulos:
        padrao_janela = rf"{rotulo}[\s\S]{{0,150}}"
        m_janela = re.search(padrao_janela, texto, re.IGNORECASE)
        if not m_janela:
            continue

        trecho = texto[m_janela.start() : m_janela.end() + 60]

        # 1) Mes por extenso: 21/agosto/2026
        m_ext = RE_DATA_EXTENSO.search(trecho)
        if m_ext:
            dia, mes_nome, ano = m_ext.groups()
            mes_num = MESES_EXTENSO.get(mes_nome.lower())
            if mes_num:
                return f"{int(dia):02d}/{mes_num:02d}/{ano}", rotulo

        # 2) Numerico: 21/08/2026
        m_num = RE_DATA_BR.search(trecho)
        if m_num:
            bruto = m_num.group(1)
            partes = re.split(r"[/.-]", bruto)
            if len(partes) == 3:
                dia, mes, ano = partes
                return f"{int(dia):02d}/{int(mes):02d}/{ano}", rotulo

        # 3) Mes abreviado: 23 OUT 2026
        m_abrev = RE_DATA_ABREV.search(trecho)
        if m_abrev:
            dia, mes_abrev, ano = m_abrev.groups()
            mes_num = MESES_ABREV.get(mes_abrev.upper())
            if mes_num:
                return f"{int(dia):02d}/{mes_num:02d}/{ano}", rotulo

    return None, ""


def _valor_fallback(texto: str) -> float | None:
    valores = []
    for m in RE_VALOR_BRL.finditer(texto):
        v = parse_valor(m.group(1))
        if v and 0 < v < 10_000_000:
            valores.append(v)
    return max(valores) if valores else None


def _data_fallback(texto: str) -> str | None:
    """Primeira data valida do texto, em dd/mm/yyyy.

    Ordem de tentativa:
    1. Mes por extenso: 21/agosto/2026 (comprovante Mercado Pago)
    2. Numerico brasileiro: 21/08/2026, 21-08-2026, 21.08.2026
    3. Mes abreviado: 23 OUT 2026 (comprovante Nubank)
    4. ISO: 2026-08-21

    Sempre devolve dd/mm/yyyy, que e o formato da planilha.
    """
    # 1) Mes por extenso (mais especifico)
    m = RE_DATA_EXTENSO.search(texto)
    if m:
        dia, mes_nome, ano = m.groups()
        mes_num = MESES_EXTENSO.get(mes_nome.lower())
        if mes_num:
            return f"{int(dia):02d}/{mes_num:02d}/{ano}"

    # 2) Numerico brasileiro
    for m in RE_DATA_BR.finditer(texto):
        bruto = m.group(1)
        partes = re.split(r"[/.-]", bruto)
        if len(partes) == 3:
            dia, mes, ano = partes
            if 2000 <= int(ano) <= 2100:
                return f"{int(dia):02d}/{int(mes):02d}/{ano}"

    # 3) Mes abreviado: 23 OUT 2026
    m = RE_DATA_ABREV.search(texto)
    if m:
        dia, mes_abrev, ano = m.groups()
        mes_num = MESES_ABREV.get(mes_abrev.upper())
        if mes_num:
            return f"{int(dia):02d}/{mes_num:02d}/{ano}"

    # 4) ISO (YYYY-MM-DD)
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", texto)
    if m:
        ano, mes, dia = m.groups()
        return f"{dia}/{mes}/{ano}"

    return None


def _valor_com_cache(
    texto: str, rotulos: list[str], rotulo_cache: str | None
) -> tuple[float | None, str]:
    if rotulo_cache:
        v, _ = _valor_apos_rotulo(texto, [rotulo_cache])
        if v is not None:
            return v, rotulo_cache
    v, r = _valor_apos_rotulo(texto, rotulos)
    if v is not None:
        return v, r
    v, r = _valor_apos_rotulo(texto, ROTULOS_VALOR_GENERICOS)
    if v is not None:
        return v, r
    return _valor_fallback(texto), ""


def _data_com_cache(
    texto: str, rotulos: list[str], rotulo_cache: str | None
) -> tuple[str | None, str]:
    if rotulo_cache:
        d, _ = _data_apos_rotulo(texto, [rotulo_cache])
        if d is not None:
            return d, rotulo_cache
    d, r = _data_apos_rotulo(texto, rotulos)
    if d is not None:
        return d, r
    d, r = _data_apos_rotulo(texto, ROTULOS_DATA_GENERICOS)
    if d is not None:
        return d, r
    return _data_fallback(texto), ""


def _extrair_nf(texto: str) -> str:
    secao = RE_NF_SECAO.search(texto)
    alvo = secao.group(1) if secao else ""
    if alvo:
        m = re.search(r"(?:NF|NOTA\s*FISCAL)\s*[:\-]?\s*(\d{3,9})", alvo, re.IGNORECASE)
        if m:
            return m.group(1)
        m = RE_NF.search(alvo)
        if m:
            return m.group(1)
    m = re.search(r"(?:NF|NOTA\s*FISCAL)\s*[:\-]?\s*(\d{3,9})", texto, re.IGNORECASE)
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# GUIA
# ---------------------------------------------------------------------------


def extrair_guia(caminho: Path) -> dict:
    texto = extrair_texto(caminho)
    cache = layout_cache.buscar_layout(TipoDoc.GUIA, texto) or {}
    rotulos_usados = {}

    razao = _razao_social_apos_rotulo(texto, ROTULOS_RAZAO_SOCIAL)

    campos = {
        "numero_guia": "",
        "numero_nf": _extrair_nf(texto),
        "cliente": razao,
        "tomador": razao,
        "cnpj": _cnpj_guia(texto, razao),
        "uf": "",
        "tributo": "",
        "valor_guia": None,
        "data_emissao": None,
        "data_vencimento": None,
        "chave_nfe": "",
    }

    confianca = 0
    if razao:
        confianca += 10

    m = re.search(
        r"chave\s*(?:de\s*)?acesso[:\s]*([\d\s]{44,60})", texto, re.IGNORECASE
    )
    if m:
        campos["chave_nfe"] = re.sub(r"\s", "", m.group(1))[:44]
        confianca += 10

    m = re.search(
        r"(?:n[uú]mero|n[ºo])\s*(?:da\s*)?guia[:\s]*([\w./-]{4,30})",
        texto,
        re.IGNORECASE,
    )
    if m:
        campos["numero_guia"] = m.group(1).strip()
        confianca += 10

    for trib in ("DARE", "GNRE", "DIFAL", "ICMS", "FECP", "ANTECIPA"):
        if trib in texto.upper():
            campos["tributo"] = trib
            confianca += 10
            break

    m = re.search(rf"\b({UFS})\b", texto)
    if m:
        campos["uf"] = m.group(1)
        confianca += 5

    valor, rotulo = _valor_com_cache(
        texto,
        [
            r"total\s*a\s*pagar",
            r"total",
            r"principal",
            r"valor\s*(?:total|da\s*guia|a\s*pagar)",
        ],
        cache.get("valor_guia"),
    )
    campos["valor_guia"] = valor
    if valor:
        confianca += 20
        if rotulo:
            rotulos_usados["valor_guia"] = rotulo

    data_em, rotulo = _data_com_cache(
        texto,
        [r"data\s*emiss[ãa]o", r"emiss[ãa]o"],
        cache.get("data_emissao"),
    )
    campos["data_emissao"] = data_em
    if data_em:
        confianca += 10
        if rotulo:
            rotulos_usados["data_emissao"] = rotulo

    data_venc, rotulo = _data_com_cache(
        texto,
        [r"data\s*limite\s*pagamento", r"vencimento"],
        cache.get("data_vencimento"),
    )
    campos["data_vencimento"] = data_venc
    if data_venc:
        confianca += 10
        if rotulo:
            rotulos_usados["data_vencimento"] = rotulo

    if campos["numero_nf"]:
        confianca += 15
    if campos["cnpj"]:
        confianca += 10

    campos["confianca"] = min(confianca, 100)
    campos["tipo"] = TipoDoc.GUIA
    campos["texto"] = texto
    campos["_rotulos"] = rotulos_usados
    return campos


# ---------------------------------------------------------------------------
# COMPROVANTE JADLOG
# ---------------------------------------------------------------------------


def extrair_comprovante_jadlog(caminho: Path) -> dict:
    texto = extrair_texto(caminho)
    cache = layout_cache.buscar_layout(TipoDoc.COMP_JADLOG, texto) or {}
    rotulos_usados = {}

    campos = {
        "nome_pagador": "",
        "cnpj_pagador": "",
        "banco": "",
        "beneficiario": "",
        "numero_documento": "",
        "autenticacao": "",
        "data_pagamento_jadlog": None,
        "valor_pago_jadlog": None,
    }

    confianca = 0

    m = re.search(r"pagador[:\s]*([A-ZÀ-Ú][^\n]{3,60})", texto, re.IGNORECASE)
    if m:
        campos["nome_pagador"] = m.group(1).strip()
        confianca += 10

    campos["cnpj_pagador"] = _cnpj_apos_rotulo(
        texto, [r"cliente", r"cnpj"]
    ) or _primeiro_cnpj(texto)
    if campos["cnpj_pagador"]:
        confianca += 20

    m = re.search(r"benefici[áa]rio[:\s]*([A-ZÀ-Ú][^\n]{3,60})", texto, re.IGNORECASE)
    if m:
        campos["beneficiario"] = m.group(1).strip()
        confianca += 10

    for banco in BANCOS:
        if banco in texto.upper():
            campos["banco"] = banco
            confianca += 10
            break

    m = re.search(r"autentica[çc][ãa]o[:\s]*([\w.\-/]{6,40})", texto, re.IGNORECASE)
    if m:
        campos["autenticacao"] = m.group(1)
        confianca += 10

    m = re.search(
        r"(?:documento|n[ºo]\s*doc)[:\s]*([\w.\-/]{4,30})",
        texto,
        re.IGNORECASE,
    )
    if m:
        campos["numero_documento"] = m.group(1)
        confianca += 5

    data, rotulo = _data_com_cache(
        texto,
        [r"data\s*do\s*pagamento", r"data\s*pagamento", r"pago\s*em"],
        cache.get("data_pagamento_jadlog"),
    )
    campos["data_pagamento_jadlog"] = data
    if data:
        confianca += 15
        if rotulo:
            rotulos_usados["data_pagamento_jadlog"] = rotulo

    valor, rotulo = _valor_com_cache(
        texto,
        [
            r"valor\s*do\s*documento",
            r"valor\s*pago",
            r"valor\s*total",
            r"valor",
        ],
        cache.get("valor_pago_jadlog"),
    )
    campos["valor_pago_jadlog"] = valor
    if valor:
        confianca += 20
        if rotulo:
            rotulos_usados["valor_pago_jadlog"] = rotulo

    campos["confianca"] = min(confianca, 100)
    campos["tipo"] = TipoDoc.COMP_JADLOG
    campos["texto"] = texto
    campos["_rotulos"] = rotulos_usados
    return campos


# ---------------------------------------------------------------------------
# COMPROVANTE CLIENTE
# ---------------------------------------------------------------------------


def extrair_comprovante_cliente(caminho: Path) -> dict:
    texto = extrair_texto(caminho)
    cache = layout_cache.buscar_layout(TipoDoc.COMP_CLIENTE, texto) or {}
    rotulos_usados = {}

    campos = {
        "nome_pagador": "",
        "cnpj_pagador": "",
        "valor_reembolso": None,
        "data_reembolso": None,
        "banco_origem": "",
        "tipo_pagamento": "",
        "id_transacao": "",
        "agencia": "",
        "conta": "",
    }

    confianca = 0

    m = re.search(
        r"dados\s*do\s*pagador[\s\S]{0,120}?\n([A-ZÀ-Ú][A-ZÀ-Ú&\s.]{14,80})",
        texto,
        re.IGNORECASE,
    )
    if m:
        campos["nome_pagador"] = m.group(1).strip()
        confianca += 10

    campos["cnpj_pagador"] = _cnpj_pagador_comprovante(texto, campos["nome_pagador"])
    if campos["cnpj_pagador"]:
        confianca += 25

    valor, rotulo = _valor_com_cache(
        texto, [r"valor\s*pago", r"valor"], cache.get("valor_reembolso")
    )
    if not valor:
        valor = valor_tolerante(texto, caminho)
        rotulo = "tolerante"
    if not valor:
        valor = _valor_fallback(texto)
        rotulo = "fallback"

    campos["valor_reembolso"] = valor
    if valor:
        confianca += 25
    if rotulo and rotulo not in ("fallback", "tolerante"):
        rotulos_usados["valor_reembolso"] = rotulo

    data, rotulo = _data_com_cache(
        texto,
        [
            r"data\s*e\s*hora\s*da\s*transa[çc][ãa]o",
            r"data\s*da\s*transa[çc][ãa]o",
            r"data",
        ],
        cache.get("data_reembolso"),
    )
    campos["data_reembolso"] = data
    if data:
        confianca += 15
        if rotulo:
            rotulos_usados["data_reembolso"] = rotulo

    up = texto.upper()
    if "PIX" in up:
        campos["tipo_pagamento"] = "PIX"
        confianca += 10
    elif "TED" in up:
        campos["tipo_pagamento"] = "TED"
        confianca += 10
    elif "DOC" in up:
        campos["tipo_pagamento"] = "DOC"
        confianca += 10

    m = re.search(r"(?:id|e2e|end-to-end)[:\s]*([\w.\-]{10,40})", texto, re.IGNORECASE)
    if m:
        campos["id_transacao"] = m.group(1)
        confianca += 5

    m = re.search(r"ag[êe]ncia[:\s]*([\d.\-]{2,10})", texto, re.IGNORECASE)
    if m:
        campos["agencia"] = m.group(1)

    m = re.search(r"conta[:\s]*([\d.\-/]{3,20})", texto, re.IGNORECASE)
    if m:
        campos["conta"] = m.group(1)

    campos["confianca"] = min(confianca, 100)
    campos["tipo"] = TipoDoc.COMP_CLIENTE
    campos["texto"] = texto
    campos["_rotulos"] = rotulos_usados
    return campos


# ---------------------------------------------------------------------------
# DISPATCH
# ---------------------------------------------------------------------------


def extrair_por_tipo(caminho: Path, tipo: str) -> dict:
    """Extrai os campos de um documento conforme o tipo.

    Sempre devolve `_rotulos` (mapa campo -> rotulo usado) e `texto`
    (texto bruto). O pipeline usa os dois para gravar o layout aprendido.
    """
    if tipo == TipoDoc.GUIA:
        return extrair_guia(caminho)
    if tipo == TipoDoc.COMP_JADLOG:
        return extrair_comprovante_jadlog(caminho)
    if tipo == TipoDoc.COMP_CLIENTE:
        return extrair_comprovante_cliente(caminho)
    return {
        "tipo": tipo,
        "confianca": 0,
        "texto": extrair_texto(caminho),
        "_rotulos": {},
    }

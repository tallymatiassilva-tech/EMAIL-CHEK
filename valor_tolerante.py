"""Extrator de valor tolerante a falhas de OCR.

Usado quando a extracao por rotulo e o fallback simples nao encontram o valor.
Tenta varias estrategias, da mais segura para a mais arriscada, e REJEITA
numeros que claramente nao sao valor:

- CNPJ (14 digitos, com ou sem pontuacao)
- CPF (11 digitos)
- Agencia, conta, autenticacao, ID de transacao
- Datas (dd/mm/yyyy)
- Telefones (0800, sequencias longas)
- Codigos de barras e chaves de acesso

Cada candidato passa por uma validacao de contexto: o numero precisa estar
perto de uma palavra que indique valor (R$, pago, total, valor) ou estar
formatado como moeda (com virgula e dois decimais).
"""

import re
from pathlib import Path

from log_manager import get_logger

log = get_logger("valor_tolerante")

# ---------------------------------------------------------------------------
# PADROES QUE NAO SAO VALOR
# ---------------------------------------------------------------------------

# CNPJ formatado: 20.749.543/0001-25
RE_CNPJ_FMT = re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}")
# CNPJ sem pontuacao: 20749543000125
RE_CNPJ_NUM = re.compile(r"\b\d{14}\b")
# CPF formatado: 123.456.789-01
RE_CPF_FMT = re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}")
# CPF sem pontuacao
RE_CPF_NUM = re.compile(r"\b\d{11}\b")
# CPF mascarado: *** 151.722-**
RE_CPF_MASCARADO = re.compile(r"\*{2,}\s*\d{3}\.\d{3}")
# Datas
RE_DATA = re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b")
# Telefones e 0800
RE_TELEFONE = re.compile(r"\b0?800[\s.-]?\d{3}[\s.-]?\d{4}\b")
# Chave de acesso NFe (44 digitos)
RE_CHAVE_NFE = re.compile(r"\b\d{44}\b")
# IDs longos (E2E do Pix tem 32 caracteres alfanumericos)
RE_ID_LONGO = re.compile(r"\b[A-Za-z0-9]{20,}\b")

# Rotulos que indicam que o numero seguinte NAO e valor
ROTULOS_NAO_VALOR = re.compile(
    r"(?:cnpj|cpf|ag[êe]ncia|conta|autentica[çc][ãa]o|"
    r"id|e2e|end-to-end|transa[çc][ãa]o|documento|"
    r"telefone|celular|cep|inscri[çc][ãa]o)",
    re.IGNORECASE,
)

# Rotulos que indicam que o numero seguinte E valor
ROTULOS_VALOR = re.compile(
    r"(?:r\$|valor|total|pago|pagamento|reembolso|"
    r"l[ií]quido|bruto|principal|montante)",
    re.IGNORECASE,
)

# Valor formatado como moeda: 1.234,56 ou 123,45
RE_MOEDA = re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})*,\d{2})(?![\d.,])")


# ---------------------------------------------------------------------------
# VALIDACAO
# ---------------------------------------------------------------------------


def _e_cnpj_ou_cpf(texto: str) -> bool:
    """Verifica se o texto e um CNPJ ou CPF, com ou sem pontuacao."""
    limpo = re.sub(r"\D", "", texto)
    if len(limpo) in (11, 14):
        return True
    return bool(
        RE_CNPJ_FMT.fullmatch(texto)
        or RE_CNPJ_NUM.fullmatch(texto)
        or RE_CPF_FMT.fullmatch(texto)
        or RE_CPF_NUM.fullmatch(texto)
    )


def _e_data(texto: str) -> bool:
    return bool(RE_DATA.fullmatch(texto))


def _e_telefone(texto: str) -> bool:
    return bool(RE_TELEFONE.fullmatch(texto))


def _e_chave_ou_id(texto: str) -> bool:
    limpo = re.sub(r"\D", "", texto)
    if len(limpo) >= 20:
        return True
    return bool(RE_CHAVE_NFE.fullmatch(texto) or RE_ID_LONGO.fullmatch(texto))


def _contexto_indica_valor(texto: str, pos: int, janela: int = 80) -> bool:
    """Verifica se o numero esta perto de uma palavra de valor."""
    inicio = max(0, pos - janela)
    fim = min(len(texto), pos + janela)
    contexto = texto[inicio:fim]

    if ROTULOS_NAO_VALOR.search(contexto):
        pos_nao_valor = ROTULOS_NAO_VALOR.search(contexto)
        pos_valor = ROTULOS_VALOR.search(contexto)
        if pos_nao_valor and (
            not pos_valor or pos_nao_valor.start() < pos_valor.start()
        ):
            return False

    return bool(ROTULOS_VALOR.search(contexto))


def _contexto_e_documento(texto: str, pos: int, janela: int = 40) -> bool:
    """Verifica se o numero esta dentro de um CNPJ/CPF no contexto.

    Diferente de _e_cnpj_ou_cpf, que valida o numero isolado, esta funcao
    olha o texto em volta. Pega o caso em que o OCR quebra o CNPJ em
    pedacos com virgula, como '37.242,74'.
    """
    inicio = max(0, pos - janela)
    fim = min(len(texto), pos + janela)
    contexto = texto[inicio:fim]

    if RE_CNPJ_FMT.search(contexto):
        return True
    if RE_CPF_FMT.search(contexto):
        return True
    if RE_CPF_MASCARADO.search(contexto):
        return True

    # Rotulo explicito imediatamente antes
    antes = texto[max(0, pos - 40) : pos]
    if re.search(r"(?:CPF|CNPJ)\s*:?\s*\*{0,3}\s*$", antes, re.IGNORECASE):
        return True

    return False


def _candidato_valido(texto: str, bruto: str, pos: int, valor: float) -> bool:
    """Aplica todas as regras de rejeicao a um candidato."""
    if not (0.01 <= valor <= 100_000):
        return False

    if _e_cnpj_ou_cpf(bruto):
        return False
    if _e_data(bruto):
        return False
    if _e_telefone(bruto):
        return False
    if _e_chave_ou_id(bruto):
        return False

    # NOVO: rejeita se o numero esta dentro de CNPJ/CPF no contexto
    if _contexto_e_documento(texto, pos):
        return False

    if RE_MOEDA.fullmatch(bruto):
        return True

    return _contexto_indica_valor(texto, pos)


# ---------------------------------------------------------------------------
# CONVERSAO
# ---------------------------------------------------------------------------


def _para_float(bruto: str) -> float | None:
    """Converte '1.234,56' ou '123,45' para float."""
    try:
        limpo = bruto.replace(".", "").replace(",", ".")
        return float(limpo)
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# API PUBLICA
# ---------------------------------------------------------------------------


def valor_tolerante(texto: str, caminho: Path | None = None) -> float | None:
    """Extrai o valor de reembolso de um texto com OCR ruim.

    Estrategias, em ordem de confianca:

    1. Valor formatado como moeda perto de rotulo de valor
    2. Valor formatado como moeda perto de R$ ou de rotulo de valor
    3. Numero perto de rotulo de valor, sem ser CNPJ/CPF/data/ID
    4. Nome do arquivo (quando o valor esta no nome, ex: "240,61.pdf")

    Retorna None se nenhum candidato seguro for encontrado.

    O parametro `caminho` e opcional e serve para a estrategia 4.
    """
    if not texto:
        return None

    # 1) Moeda perto de rotulo de valor
    for m in RE_MOEDA.finditer(texto):
        bruto = m.group(1)
        valor = _para_float(bruto)
        if valor is None:
            continue
        if not (0.01 <= valor <= 100_000):
            continue
        if _contexto_e_documento(texto, m.start()):
            continue
        if _contexto_indica_valor(texto, m.start()):
            log.debug("Valor por moeda + rotulo: %s", bruto)
            return valor

    # 2) Moeda com contexto de R$ ou rotulo de valor
    # Antes aceitava qualquer moeda isolada, o que pegava numeros de
    # transacao. Agora exige contexto.
    candidatos = []
    for m in RE_MOEDA.finditer(texto):
        bruto = m.group(1)
        valor = _para_float(bruto)
        if valor is None:
            continue
        if not (0.01 <= valor <= 100_000):
            continue
        if _e_cnpj_ou_cpf(bruto) or _e_data(bruto):
            continue
        if _contexto_e_documento(texto, m.start()):
            continue

        antes = texto[max(0, m.start() - 20) : m.start()]
        if re.search(r"R\$\s*$", antes):
            candidatos.append(valor)
            continue
        if _contexto_indica_valor(texto, m.start()):
            candidatos.append(valor)

    if candidatos:
        valor = max(candidatos)
        log.debug("Valor por moeda com contexto: %s", valor)
        return valor

    # 3) Numero perto de rotulo de valor, com validacao de contexto
    for m in re.finditer(r"\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2}", texto):
        bruto = m.group(0)
        valor = _para_float(bruto)
        if valor is None:
            continue
        if _candidato_valido(texto, bruto, m.start(), valor):
            log.debug("Valor por contexto: %s", bruto)
            return valor

    # 4) Nome do arquivo (ex: "240,61.pdf")
    if caminho:
        nome = Path(caminho).stem
        m = re.search(r"(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})", nome)
        if m:
            bruto = m.group(1)
            # NOVO: rejeita se o nome do arquivo parece CNPJ/CPF
            if _e_cnpj_ou_cpf(bruto):
                log.debug("Nome do arquivo parece CNPJ/CPF, ignorado")
            else:
                valor = _para_float(bruto)
                if valor and 0.01 <= valor <= 100_000:
                    log.debug("Valor pelo nome do arquivo: %s", bruto)
                    return valor

    log.debug("Nenhum valor seguro encontrado")
    return None

"""Camada tolerante de extração de valor para comprovantes de fintech.

O OCR do Tesseract corrompe valores em dois padrões conhecidos:
  1. Caractere no lugar da vírgula:  "R$ 38*º"   -> 38,00
  2. Vírgula perdida:                "R$ 24061"  -> 240,61

Esta camada tenta corrigir antes de entregar ao parse_valor.
"""

import re
from pathlib import Path

from utils import parse_valor

# Caracteres que o OCR costuma colocar no lugar de vírgula/zero
LIXO = r"[*º°~^`'\"!|]"

# R$ seguido de dígitos, aceitando lixo no meio
RE_VALOR_TOLERANTE = re.compile(
    r"R\$\s*(\d[\d.,*º°~^`'\"!|]{0,15})",
    re.IGNORECASE,
)

# Valor no nome do arquivo: "240,61.pdf" ou "240.61.pdf"
RE_VALOR_NOME = re.compile(r"(\d{1,3}(?:[.,]\d{3})*[.,]\d{2})")


def _limpar_trecho(trecho: str) -> str:
    """Remove lixo e normaliza separadores de um trecho numérico."""
    original = trecho.strip()

    # Detecta se havia lixo no fim (indica centavos corrompidos)
    tinha_lixo_no_fim = bool(re.search(LIXO + r"\s*$", original))

    # Remove tudo que não é dígito, ponto ou vírgula
    s = re.sub(LIXO, "", original)
    s = re.sub(r"\s+", "", s)

    if not s:
        return ""

    # Já tem separador decimal: deixa o parse_valor resolver
    if "," in s or "." in s:
        return s

    # Sem separador: reconstrói com base no tamanho
    n = len(s)

    if n <= 2:
        # 1-2 dígitos. Se tinha lixo no fim, é valor inteiro com centavos zerados.
        # Ex: "38*º" -> "38" + lixo -> "38,00"
        if tinha_lixo_no_fim:
            return f"{s},00"
        return s

    if n == 3:
        # "384" -> "3,84" ou "38,4"? Assume centavos nos 2 últimos.
        return f"{s[:1]},{s[1:]}"

    if n == 4:
        # "3845" -> "38,45"
        return f"{s[:2]},{s[2:]}"

    if n == 5:
        # "24061" -> "240,61"
        return f"{s[:3]},{s[3:]}"

    if n == 6:
        # "240061" -> "2400,61"
        return f"{s[:4]},{s[4:]}"

    return s


def _valor_do_nome(caminho: Path | None) -> float | None:
    """Extrai o valor do nome do arquivo quando ele é predominantemente numérico.

    Só aceita quando o nome é essencialmente um valor:
      "240,61.pdf"        -> 240.61  (aceita)
      "comprovante (6).pdf" -> None  (rejeita: "6" não é o valor)
      "WhatsApp Image 2026-09-09 at 10.50.42.jpeg" -> None (rejeita)
    """
    if not caminho:
        return None

    stem = caminho.stem
    # Remove sufixos de duplicata: "_1", "_2"
    stem = re.sub(r"_\d+$", "", stem)

    # Rejeita nomes com muitas letras: "WhatsApp Image", "comprovante"
    letras = re.sub(r"[^A-Za-zÀ-ÿ]", "", stem)
    if len(letras) > 3:
        return None

    m = RE_VALOR_NOME.search(stem)
    if not m:
        return None

    v = parse_valor(m.group(1))
    if v and 0 < v < 10_000_000:
        return v
    return None

def valor_tolerante(texto: str, caminho: Path | None = None) -> float | None:
    """Extrai valor de um texto com OCR corrompido.

    Ordem de tentativa:
      1. Valor do nome do arquivo (mais confiável quando existe)
      2. Primeiro valor após "R$" (o valor do Pix)
      3. Nada
    """
    # Tentativa 1: nome do arquivo
    v = _valor_do_nome(caminho)
    if v:
        return v

    # Tentativa 2: primeiro valor após "R$"
    for m in RE_VALOR_TOLERANTE.finditer(texto):
        limpo = _limpar_trecho(m.group(1))
        if not limpo:
            continue
        v = parse_valor(limpo)
        if v and 0 < v < 10_000_000:
            return v

    return None
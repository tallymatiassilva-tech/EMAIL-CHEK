"""Leitura de planilhas de clientes com mapeamento fuzzy de colunas."""

import unicodedata
from pathlib import Path

import pandas as pd
from config import FUZZY_COLUNA_MIN
from log_manager import get_logger
from rapidfuzz import fuzz
from utils import normalizar_cnpj, parse_valor

log = get_logger("excel")

ALVOS = {
    "remessa": [
        "remessa",
        "numero remessa",
        "n remessa",
        "nr remessa",
        "remessa jadlog",
    ],
    "nf": ["nf", "nota fiscal", "numero nf", "n nota fiscal", "num nf"],
    "valor": ["valor", "valor reembolso", "valor pago", "total", "vlr"],
    "cliente": ["cliente", "nome cliente", "razao social", "empresa"],
    "cnpj": ["cnpj", "cnpj cliente", "documento"],
}

# Palavras que não ajudam a identificar a coluna
RUIDO = {"n", "no", "nr", "num", "numero", "da", "de", "do", "dos", "das", "a", "o"}


def _normalizar_nome_coluna(nome: str) -> str:
    """Remove acentos, pontuação, ruído e espaços extras."""
    s = unicodedata.normalize("NFKD", str(nome))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    for ch in "ºª.-_/()[]{}:;,":
        s = s.replace(ch, " ")
    tokens = [t for t in s.split() if t and t not in RUIDO]
    return " ".join(tokens)


def ler_planilha_bruta(caminho: Path) -> pd.DataFrame | None:
    """Lê XLSX/XLS/CSV tentando múltiplos encodings e separadores."""
    ext = Path(caminho).suffix.lower()
    try:
        if ext == ".csv":
            for enc in ("utf-8", "latin-1", "cp1252"):
                for sep in (",", ";", "\t"):
                    try:
                        df = pd.read_csv(caminho, encoding=enc, sep=sep, dtype=str)
                        if df.shape[1] > 1:
                            return df
                    except Exception:
                        continue
            return pd.read_csv(caminho, dtype=str)
        return pd.read_excel(caminho, dtype=str)
    except Exception as e:
        log.warning("Erro lendo planilha %s: %s", caminho.name, e)
        return None


def mapear_colunas(df: pd.DataFrame) -> dict:
    """Mapeia colunas em 3 etapas: exato, palavra contida, fuzzy.

    Evita que 'CNPJ' caia em 'Cliente' e que 'Nº NF' fique sem mapeamento.
    """
    mapa = {}
    usadas = set()
    colunas = list(df.columns)
    colunas_norm = {col: _normalizar_nome_coluna(col) for col in colunas}

    # Etapa 1: match exato (após normalização)
    for campo, sinonimos in ALVOS.items():
        for col in colunas:
            if col in usadas:
                continue
            if colunas_norm[col] in sinonimos:
                mapa[campo] = col
                usadas.add(col)
                break

    # Etapa 2: palavra-chave contida (ex: "nf" dentro de "n nf" -> "nf")
    for campo, sinonimos in ALVOS.items():
        if campo in mapa:
            continue
        for col in colunas:
            if col in usadas:
                continue
            tokens = colunas_norm[col].split()
            for sin in sinonimos:
                sin_tokens = sin.split()
                # todas as palavras do sinônimo presentes na coluna
                if sin_tokens and all(t in tokens for t in sin_tokens):
                    mapa[campo] = col
                    usadas.add(col)
                    break
            if campo in mapa:
                break

    # Etapa 3: fuzzy apenas para campos ainda sem match
    for campo, sinonimos in ALVOS.items():
        if campo in mapa:
            continue
        melhor, melhor_score = None, 0
        for col in colunas:
            if col in usadas:
                continue
            nome_norm = colunas_norm[col]
            for sin in sinonimos:
                s = fuzz.ratio(nome_norm, sin)
                if s > melhor_score:
                    melhor, melhor_score = col, s
        if melhor and melhor_score >= FUZZY_COLUNA_MIN:
            mapa[campo] = melhor
            usadas.add(melhor)

    return mapa


def extrair_linhas_planilha(caminho: Path) -> list[dict]:
    """Retorna lista de linhas normalizadas da planilha do cliente."""
    df = ler_planilha_bruta(caminho)
    if df is None or df.empty:
        return []

    mapa = mapear_colunas(df)
    if "remessa" not in mapa:
        log.warning("Planilha %s sem coluna de remessa reconhecível", caminho.name)
        return []

    linhas = []
    for _, row in df.iterrows():
        remessa = str(row.get(mapa["remessa"], "")).strip()
        if not remessa or remessa.lower() == "nan":
            continue
        linhas.append(
            {
                "numero_remessa": "".join(filter(str.isdigit, remessa)) or remessa,
                "numero_nf": str(row.get(mapa.get("nf", ""), "")).strip() or None,
                "valor": parse_valor(row.get(mapa.get("valor", ""), "")),
                "cliente": str(row.get(mapa.get("cliente", ""), "")).strip() or None,
                "cnpj": normalizar_cnpj(str(row.get(mapa.get("cnpj", ""), ""))) or None,
            }
        )
    log.info("Planilha %s: %d linhas extraídas", caminho.name, len(linhas))
    return linhas

"""Utilidades: normalização, datas, valores, CNPJ, hash de layout."""

import hashlib
import re
from datetime import date, datetime
from pathlib import Path

from rapidfuzz import fuzz


def apenas_digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto or "")


def normalizar_cnpj(cnpj: str) -> str:
    d = apenas_digitos(cnpj)
    return d.zfill(14) if d else ""


def formatar_cnpj(cnpj: str) -> str:
    d = normalizar_cnpj(cnpj)
    if len(d) != 14:
        return cnpj or ""
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"


def validar_cnpj(cnpj: str) -> bool:
    d = apenas_digitos(cnpj)
    if len(d) != 14 or len(set(d)) == 1:
        return False
    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos2 = [6] + pesos1
    for pesos in (pesos1, pesos2):
        soma = sum(int(d[i]) * pesos[i] for i in range(len(pesos)))
        resto = soma % 11
        digito = 0 if resto < 2 else 11 - resto
        if int(d[len(pesos)]) != digito:
            return False
    return True


def parse_valor(texto) -> float | None:
    """Converte 'R$ 1.250,45' / '1,250.45' / '1250.45' em float."""
    if texto is None:
        return None
    if isinstance(texto, (int, float)):
        return float(texto)
    s = str(texto).replace("R$", "").replace(" ", "").strip()
    if not s:
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def parse_data(texto) -> str | None:
    """Retorna ISO 'YYYY-MM-DD'. Aceita dd/mm/aaaa, aaaa-mm-dd, dd.mm.aaaa."""
    if not texto:
        return None
    if isinstance(texto, (datetime, date)):
        return texto.strftime("%Y-%m-%d")
    s = str(texto).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    m = re.search(r"(\d{2})[/.-](\d{2})[/.-](\d{4})", s)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return None


def dias_entre(iso_ini: str, iso_fim: str) -> int | None:
    if not iso_ini or not iso_fim:
        return None
    try:
        a = datetime.strptime(iso_ini, "%Y-%m-%d").date()
        b = datetime.strptime(iso_fim, "%Y-%m-%d").date()
        return (b - a).days
    except ValueError:
        return None


def similaridade(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return fuzz.token_set_ratio(str(a).lower(), str(b).lower())


def hash_layout(texto: str) -> str:
    """Hash estrutural: remove números e normaliza espaços."""
    base = re.sub(r"\d+", "#", texto or "")
    base = re.sub(r"\s+", " ", base).strip().lower()
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:32]


def garantir_pasta_remessa(ano: str, mes: str, numero_remessa: str) -> Path:
    from config import REEMBOLSOS_DIR

    pasta = REEMBOLSOS_DIR / ano / mes / numero_remessa
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def mover_para(pasta_destino: Path, arquivo: Path) -> Path:
    """Move arquivo tratando colisão de nome."""
    pasta_destino.mkdir(parents=True, exist_ok=True)
    destino = pasta_destino / arquivo.name
    if destino.exists():
        destino = (
            pasta_destino / f"{arquivo.stem}_{datetime.now():%H%M%S}{arquivo.suffix}"
        )
    arquivo.replace(destino)
    return destino

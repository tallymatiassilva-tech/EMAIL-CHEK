"""Revisao manual interativa dos comprovantes pendentes.

Percorre os comprovantes que nao tem valor_reembolso ou data_reembolso,
ABRE o comprovante no visualizador padrao do sistema e pede o valor e a
data um por um no terminal.

Cada item confirmado e gravado NA HORA, com a trava editado_manualmente.
Se a pessoa parar no meio, o que ja foi revisado esta salvo. Na proxima
execucao, os revisados nao aparecem mais.

TECLAS:
- Enter em branco          -> pula o campo (mantem o valor atual)
- Enter em branco nos dois -> pula o item inteiro (nao grava)
- s                        -> pula o item inteiro
- o                        -> reabre o comprovante
- q                        -> sai do programa (o que ja foi gravado fica)

GRAVACAO:
- fila_treinamento: valor_reembolso, data_reembolso, editado_manualmente=True
- reembolsos: valor_reembolso_cliente, data_reembolso_cliente (pela remessa)

Uso:
    python revisar.py              # percorre os pendentes
    python revisar.py --remessa X  # so uma remessa
    python revisar.py --listar     # so lista, nao pergunta nada
    python revisar.py --sem-abrir  # nao abre o comprovante automaticamente
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from config import DB_PATH

RE_VALOR_BRUTO = re.compile(r"R\$\s*[\d\.,º°?\"”'`]+", re.IGNORECASE)


# ---------------------------------------------------------------------------
# VALIDACAO
# ---------------------------------------------------------------------------


def parse_valor(texto: str) -> float | None:
    """Converte '38,00', '38.00' ou '1.234,56' em float."""
    if not texto or not str(texto).strip():
        return None
    limpo = re.sub(r"[^\d,.]", "", str(texto)).strip()
    if not limpo:
        return None
    if "," in limpo and "." in limpo:
        if limpo.rfind(",") > limpo.rfind("."):
            limpo = limpo.replace(".", "").replace(",", ".")
        else:
            limpo = limpo.replace(",", "")
    elif "," in limpo:
        limpo = limpo.replace(",", ".")
    try:
        v = float(limpo)
    except ValueError:
        return None
    if not (0 < v < 10_000_000):
        return None
    return round(v, 2)


def parse_data(texto: str) -> str | None:
    """Valida dd/mm/yyyy e devolve no mesmo formato."""
    if not texto or not str(texto).strip():
        return None
    bruto = str(texto).strip()
    m = re.match(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$", bruto)
    if not m:
        return None
    dia, mes, ano = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        datetime(ano, mes, dia)
    except ValueError:
        return None
    return f"{dia:02d}/{mes:02d}/{ano}"


# ---------------------------------------------------------------------------
# ABERTURA DO COMPROVANTE
# ---------------------------------------------------------------------------


def abrir_comprovante(caminho: str) -> bool:
    """Abre o comprovante no visualizador padrao do sistema.

    Windows: os.startfile
    macOS:   open
    Linux:   xdg-open

    Devolve True se conseguiu disparar a abertura. Nao espera o programa
    fechar — o usuario le o comprovante na janela que abriu e volta ao
    terminal para digitar.
    """
    arquivo = Path(caminho)
    if not arquivo.is_file():
        print(f"  AVISO: arquivo nao encontrado: {caminho}")
        return False

    try:
        if sys.platform.startswith("win"):
            os.startfile(str(arquivo))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(arquivo)])
        else:
            subprocess.Popen(
                ["xdg-open", str(arquivo)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        return True
    except Exception as e:
        print(f"  AVISO: nao foi possivel abrir o comprovante: {e}")
        return False


# ---------------------------------------------------------------------------
# LEITURA
# ---------------------------------------------------------------------------


def extrair_contexto(texto: str, limite: int = 220) -> str:
    """Trecho do texto em volta do valor."""
    m = RE_VALOR_BRUTO.search(texto)
    if m:
        inicio = max(0, m.start() - 70)
        fim = min(len(texto), m.end() + 120)
        return texto[inicio:fim].replace("\n", " | ").strip()
    return texto[:limite].replace("\n", " | ").strip()


def classificar_motivo(campos: dict, texto: str) -> str:
    falta_valor = not campos.get("valor_reembolso")
    falta_data = not campos.get("data_reembolso")

    if falta_valor and falta_data:
        base = "valor e data ausentes"
    elif falta_valor:
        base = "valor ausente"
    else:
        base = "data ausente"

    trechos = RE_VALOR_BRUTO.findall(texto)
    corrompido = any(any(c in t for c in "º°?\"”'`") for t in trechos)

    if corrompido:
        return base + " / OCR corrompido"
    if trechos:
        return base + " / valor legivel no texto"
    return base + " / sem valor no documento"


def carregar_pendentes(remessa_filtro: str | None = None) -> list[dict]:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    sql = (
        "SELECT id, numero_remessa, caminho_arquivo, campos_json "
        "FROM fila_treinamento "
        "WHERE tipo_documento = 'COMPROVANTE_REEMBOLSO' "
    )
    params: list = []
    if remessa_filtro:
        sql += "AND numero_remessa = ? "
        params.append(remessa_filtro)
    sql += "ORDER BY numero_remessa, id"

    rows = con.execute(sql, params).fetchall()
    con.close()

    pendentes = []
    for r in rows:
        d = json.loads(r["campos_json"])
        c = d.get("campos", d)
        texto = d.get("texto") or c.get("texto") or ""

        if c.get("editado_manualmente"):
            continue

        falta_valor = not c.get("valor_reembolso")
        falta_data = not c.get("data_reembolso")
        if not (falta_valor or falta_data):
            continue

        pendentes.append(
            {
                "id": r["id"],
                "remessa": r["numero_remessa"],
                "arquivo": r["caminho_arquivo"].split("\\")[-1],
                "caminho": r["caminho_arquivo"],
                "valor_atual": c.get("valor_reembolso"),
                "data_atual": c.get("data_reembolso"),
                "motivo": classificar_motivo(c, texto),
                "contexto": extrair_contexto(texto),
            }
        )
    return pendentes


# ---------------------------------------------------------------------------
# GRAVACAO
# ---------------------------------------------------------------------------


def gravar(item: dict, valor: float | None, data: str | None) -> bool:
    """Grava a revisao na fila e em reembolsos. Devolve True se gravou."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    row = con.execute(
        "SELECT campos_json FROM fila_treinamento WHERE id = ?", (item["id"],)
    ).fetchone()
    if not row:
        con.close()
        return False

    dados = json.loads(row["campos_json"])
    campos = dados.get("campos")
    if campos is None:
        campos = dict(dados)
        dados = {"campos": campos}

    if valor is not None:
        campos["valor_reembolso"] = valor
    if data is not None:
        campos["data_reembolso"] = data

    campos["editado_manualmente"] = True
    campos["confianca"] = 100

    faltando = [c for c in ("valor_reembolso", "data_reembolso") if not campos.get(c)]
    dados["campos"] = campos
    dados["faltando"] = faltando
    dados["confianca"] = 100

    con.execute(
        "UPDATE fila_treinamento SET campos_json = ?, confianca = 100 WHERE id = ?",
        (json.dumps(dados, ensure_ascii=False), item["id"]),
    )

    remessa = campos.get("numero_remessa") or item["remessa"]
    if remessa and (valor is not None or data is not None):
        con.execute(
            "UPDATE reembolsos SET valor_reembolso_cliente = ?, "
            "data_reembolso_cliente = ? WHERE numero_remessa = ?",
            (campos.get("valor_reembolso"), campos.get("data_reembolso"), remessa),
        )

    con.commit()
    con.close()
    return True


# ---------------------------------------------------------------------------
# INTERACAO
# ---------------------------------------------------------------------------


def perguntar_valor(atual) -> tuple[str, float | None]:
    """Pergunta o valor. Devolve (acao, valor).

    acao: 'ok' (valor valido), 'pular' (enter em branco), 'sair', 'reabrir'
    """
    dica = f" [{atual}]" if atual else ""
    while True:
        entrada = input(f"  Valor{dica}: ").strip()
        baixo = entrada.lower()
        if baixo == "q":
            return "sair", None
        if baixo == "s":
            return "pular", None
        if baixo == "o":
            return "reabrir", None
        if not entrada:
            return "pular", None
        v = parse_valor(entrada)
        if v is None:
            print("    valor invalido, tente de novo (ex: 38,00)")
            continue
        return "ok", v


def perguntar_data(atual) -> tuple[str, str | None]:
    """Pergunta a data. Devolve (acao, data)."""
    dica = f" [{atual}]" if atual else ""
    while True:
        entrada = input(f"  Data (dd/mm/aaaa){dica}: ").strip()
        baixo = entrada.lower()
        if baixo == "q":
            return "sair", None
        if baixo == "s":
            return "pular", None
        if baixo == "o":
            return "reabrir", None
        if not entrada:
            return "pular", None
        d = parse_data(entrada)
        if d is None:
            print("    data invalida, use dd/mm/aaaa (ex: 07/10/2026)")
            continue
        return "ok", d


def revisar(pendentes: list[dict], abrir_auto: bool = True) -> None:
    total = len(pendentes)
    gravados = 0
    pulados = 0

    print("=" * 74)
    print("  REVISAO MANUAL INTERATIVA")
    print("=" * 74)
    print(f"  {total} comprovante(s) pendente(s)")
    if abrir_auto:
        print("  O comprovante abre automaticamente no visualizador padrao")
    print("  Enter em branco pula o campo | s pula o item | o reabre | q sai")
    print("=" * 74)
    print()

    for i, item in enumerate(pendentes, start=1):
        print("-" * 74)
        print(f"[{i}/{total}] ID {item['id']} | remessa {item['remessa']}")
        print(f"  {item['arquivo']}")
        print(f"  Motivo: {item['motivo']}")
        print(f"  Trecho: {item['contexto']}")

        if abrir_auto:
            if abrir_comprovante(item["caminho"]):
                print("  (comprovante aberto — confira e volte ao terminal)")
        print()

        acao, valor = perguntar_valor(item["valor_atual"])
        while acao == "reabrir":
            abrir_comprovante(item["caminho"])
            acao, valor = perguntar_valor(item["valor_atual"])

        if acao == "sair":
            print("\nSaindo. O que ja foi gravado esta salvo.")
            break
        if acao == "pular":
            print("  -> item pulado")
            pulados += 1
            print()
            continue

        acao, data = perguntar_data(item["data_atual"])
        while acao == "reabrir":
            abrir_comprovante(item["caminho"])
            acao, data = perguntar_data(item["data_atual"])

        if acao == "sair":
            print("\nSaindo. O que ja foi gravado esta salvo.")
            break
        if acao == "pular":
            if gravar(item, valor, None):
                gravados += 1
                print(f"  OK gravado (valor={valor:.2f}, data mantida)")
            print()
            continue

        if gravar(item, valor, data):
            gravados += 1
            print(f"  OK gravado (valor={valor:.2f}, data={data})")
        else:
            print("  ERRO: item nao encontrado no banco")
        print()

    print("=" * 74)
    print("  RESULTADO")
    print("=" * 74)
    print(f"  Gravados: {gravados}")
    print(f"  Pulados:  {pulados}")
    print(f"  Restantes: {total - gravados - pulados}")
    if total - gravados - pulados > 0:
        print("\n  Rode novamente para continuar de onde parou.")


def listar(pendentes: list[dict]) -> None:
    print("=" * 74)
    print("  COMPROVANTES PENDENTES")
    print("=" * 74)
    print(f"  Total: {len(pendentes)}")
    print()

    for item in pendentes:
        print(f"  ID {item['id']:>3} | {item['remessa']} | {item['arquivo']}")
        print(f"         {item['motivo']}")
    print()


def main():
    remessa = None
    if "--remessa" in sys.argv:
        idx = sys.argv.index("--remessa")
        if idx + 1 < len(sys.argv):
            remessa = sys.argv[idx + 1]

    pendentes = carregar_pendentes(remessa)

    if not pendentes:
        print("Nenhum comprovante pendente de revisao.")
        return

    if "--listar" in sys.argv:
        listar(pendentes)
        return

    abrir_auto = "--sem-abrir" not in sys.argv
    revisar(pendentes, abrir_auto=abrir_auto)


if __name__ == "__main__":
    main()

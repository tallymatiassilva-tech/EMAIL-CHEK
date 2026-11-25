"""Gera a lista de comprovantes que precisam de revisao manual.

Le a fila de treinamento e separa os itens que nao tem valor_reembolso
ou data_reembolso. Para cada um, mostra:
- ID e remessa
- qual campo falta
- o trecho do texto onde o valor deveria estar
- o caminho do arquivo original

O CSV ja sai com as colunas de revisao prontas para preencher:
- "Valor Revisado"  (ex: 38,00)
- "Data Revisada"   (ex: 07/10/2026)

Depois de preencher, rode importar_revisao.py para gravar no banco.

Gera dois arquivos na raiz do projeto:
- revisao_manual.csv   (planilha para abrir no Excel)
- revisao_manual.txt   (relatorio legivel com o texto bruto)

Uso:
    python gerar_revisao_manual.py
"""

import csv
import json
import re
import sqlite3
from pathlib import Path

from config import DB_PATH

CSV_SAIDA = Path("revisao_manual.csv")
TXT_SAIDA = Path("revisao_manual.txt")

# Regex para achar o valor no texto, mesmo corrompido
RE_VALOR_BRUTO = re.compile(r"R\$\s*[\d\.,º°?\"”'`]+", re.IGNORECASE)

# Regex para achar a data no texto
RE_DATA_BRUTA = re.compile(
    r"\d{1,2}[/.-]\d{1,2}[/.-]\d{4}"
    r"|\d{1,2}\s*/\s*[a-zçãé]+\s*/\s*\d{4}"
    r"|\d{1,2}\s+(?:jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)\s*,?\s*\d{4}",
    re.IGNORECASE,
)

# Colunas do CSV, na ordem. As duas ultimas sao de preenchimento manual.
COLUNAS = [
    "ID",
    "Remessa",
    "Arquivo",
    "Falta Valor",
    "Falta Data",
    "Valor Atual",
    "Data Atual",
    "Motivo",
    "Trecho do Texto",
    "Caminho Completo",
    "Valor Revisado",
    "Data Revisada",
]


def classificar_motivo(campos: dict, texto: str) -> str:
    """Classifica o motivo pelo qual o item precisa de revisao."""
    falta_valor = not campos.get("valor_reembolso")
    falta_data = not campos.get("data_reembolso")

    if falta_valor and falta_data:
        base = "valor e data ausentes"
    elif falta_valor:
        base = "valor ausente"
    else:
        base = "data ausente"

    trechos_valor = RE_VALOR_BRUTO.findall(texto)
    corrompido = any(any(c in t for c in "º°?\"”'`") for t in trechos_valor)

    if corrompido:
        return base + " / OCR corrompido"
    if trechos_valor:
        return base + " / valor legivel no texto"
    return base + " / sem valor no documento"


def extrair_contexto(texto: str, limite: int = 200) -> str:
    """Extrai o trecho do texto onde o valor deveria estar."""
    m = RE_VALOR_BRUTO.search(texto)
    if m:
        inicio = max(0, m.start() - 60)
        fim = min(len(texto), m.end() + 100)
        return texto[inicio:fim].replace("\n", " | ").strip()
    return texto[:limite].replace("\n", " | ").strip()


def gerar_lista():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    rows = con.execute(
        "SELECT id, numero_remessa, caminho_arquivo, campos_json "
        "FROM fila_treinamento "
        "WHERE tipo_documento = 'COMPROVANTE_REEMBOLSO' "
        "ORDER BY numero_remessa, id"
    ).fetchall()

    pendentes = []
    for r in rows:
        d = json.loads(r["campos_json"])
        c = d.get("campos", d)
        texto = d.get("texto") or c.get("texto") or ""

        # Item ja revisado manualmente nao entra na lista
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
                "falta_valor": "SIM" if falta_valor else "",
                "falta_data": "SIM" if falta_data else "",
                "valor_atual": c.get("valor_reembolso") or "",
                "data_atual": c.get("data_reembolso") or "",
                "motivo": classificar_motivo(c, texto),
                "contexto": extrair_contexto(texto),
                "texto": texto,
            }
        )

    con.close()
    return pendentes


def escrever_csv(pendentes: list):
    with open(CSV_SAIDA, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(COLUNAS)
        for p in pendentes:
            w.writerow(
                [
                    p["id"],
                    p["remessa"],
                    p["arquivo"],
                    p["falta_valor"],
                    p["falta_data"],
                    p["valor_atual"],
                    p["data_atual"],
                    p["motivo"],
                    p["contexto"],
                    p["caminho"],
                    "",  # Valor Revisado — preenchimento manual
                    "",  # Data Revisada — preenchimento manual
                ]
            )


def escrever_txt(pendentes: list):
    with open(TXT_SAIDA, "w", encoding="utf-8") as f:
        f.write("=" * 78 + "\n")
        f.write("  COMPROVANTES PARA REVISAO MANUAL\n")
        f.write("=" * 78 + "\n\n")
        f.write(f"Total: {len(pendentes)} item(ns)\n")
        f.write("Preencha 'Valor Revisado' e 'Data Revisada' no CSV.\n")
        f.write("Depois rode: python importar_revisao.py --aplicar\n\n")

        por_remessa = {}
        for p in pendentes:
            por_remessa.setdefault(p["remessa"], []).append(p)

        for remessa in sorted(por_remessa):
            itens = por_remessa[remessa]
            f.write("-" * 78 + "\n")
            f.write(f"REMESSA {remessa} ({len(itens)} item(ns))\n")
            f.write("-" * 78 + "\n\n")

            for p in itens:
                f.write(f"  ID {p['id']}  |  {p['arquivo']}\n")
                f.write(f"  Motivo: {p['motivo']}\n")
                if p["valor_atual"]:
                    f.write(f"  Valor atual: {p['valor_atual']}\n")
                if p["data_atual"]:
                    f.write(f"  Data atual: {p['data_atual']}\n")
                f.write(f"  Trecho: {p['contexto']}\n")
                f.write(f"  Arquivo: {p['caminho']}\n")
                f.write("\n")

        f.write("=" * 78 + "\n")
        f.write("  RESUMO POR MOTIVO\n")
        f.write("=" * 78 + "\n\n")

        contagem = {}
        for p in pendentes:
            contagem[p["motivo"]] = contagem.get(p["motivo"], 0) + 1

        for motivo, qtd in sorted(contagem.items(), key=lambda x: -x[1]):
            f.write(f"  {qtd:3d}  {motivo}\n")


def main():
    pendentes = gerar_lista()

    if not pendentes:
        print("Nenhum comprovante pendente de revisao.")
        return

    escrever_csv(pendentes)
    escrever_txt(pendentes)

    print(f"Total de comprovantes para revisao: {len(pendentes)}")
    print(f"  CSV: {CSV_SAIDA.resolve()}")
    print(f"  TXT: {TXT_SAIDA.resolve()}")
    print()
    print("O CSV ja tem as colunas 'Valor Revisado' e 'Data Revisada'.")
    print("Preencha no Excel e rode: python importar_revisao.py --aplicar")
    print()

    contagem = {}
    for p in pendentes:
        contagem[p["motivo"]] = contagem.get(p["motivo"], 0) + 1

    print("Resumo por motivo:")
    for motivo, qtd in sorted(contagem.items(), key=lambda x: -x[1]):
        print(f"  {qtd:3d}  {motivo}")


if __name__ == "__main__":
    main()

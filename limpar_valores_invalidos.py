"""Limpa valores de reembolso que na verdade sao CNPJ ou CPF.

O motor antigo pegava o CNPJ/CPF do texto quando o valor real estava
corrompido pelo OCR. Este script varre a fila de treinamento, detecta
esses casos e zera o campo para que o item volte a revisao manual.

Uso:
    python limpar_valores_invalidos.py            # dry-run (nao grava)
    python limpar_valores_invalidos.py --aplicar  # grava as correcoes
"""

import argparse
import json
import re
import sqlite3
from pathlib import Path

from config import DB_PATH

# Padroes que indicam que o valor e na verdade parte de um documento
RE_CNPJ = re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}")
RE_CPF_MASCARADO = re.compile(r"\*{2,}\s*\d{3}\.\d{3}")
RE_CPF_FORMATADO = re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}")


def _campos_do_json(campos_json: str | None) -> dict:
    try:
        dados = json.loads(campos_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return dados.get("campos", dados) or {}


def _valor_e_documento(valor: float, texto: str) -> bool:
    """Retorna True se o valor aparece dentro de um CNPJ ou CPF no texto."""
    if valor is None or not texto:
        return False

    candidatos = [f"{valor:.2f}", f"{valor:.2f}".replace(".", ",")]

    for cand in candidatos:
        pos = texto.find(cand)
        while pos >= 0:
            inicio = max(0, pos - 30)
            fim = min(len(texto), pos + len(cand) + 30)
            contexto = texto[inicio:fim]

            if RE_CNPJ.search(contexto):
                return True
            if RE_CPF_MASCARADO.search(contexto):
                return True
            if RE_CPF_FORMATADO.search(contexto):
                return True
            if re.search(r"(?:CPF|CNPJ)\s*:?\s*\*{0,3}\s*$", contexto[:60]):
                return True

            pos = texto.find(cand, pos + 1)

    return False


def _texto_do_item(dados: dict) -> str:
    """Recupera o texto bruto do item, em qualquer formato de JSON."""
    texto = dados.get("texto")
    if texto:
        return texto
    campos = dados.get("campos", {})
    return campos.get("texto", "") or ""


def limpar(aplicar: bool = False) -> dict:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    try:
        rows = con.execute(
            "SELECT id, numero_remessa, caminho_arquivo, campos_json "
            "FROM fila_treinamento "
            "WHERE tipo_documento = 'COMPROVANTE_REEMBOLSO' "
            "ORDER BY id"
        ).fetchall()

        suspeitos = []
        preservados = 0

        for r in rows:
            dados = json.loads(r["campos_json"] or "{}")
            campos = dados.get("campos", dados) or {}

            # Respeita a trava de revisao manual
            if campos.get("editado_manualmente") or dados.get("editado_manualmente"):
                preservados += 1
                continue

            valor = campos.get("valor_reembolso")
            if not valor:
                continue

            texto = _texto_do_item(dados)
            if not texto:
                continue

            if _valor_e_documento(float(valor), texto):
                suspeitos.append(
                    {
                        "id": r["id"],
                        "remessa": r["numero_remessa"],
                        "arquivo": Path(r["caminho_arquivo"]).name
                        if r["caminho_arquivo"]
                        else "?",
                        "valor_atual": valor,
                        "dados": dados,
                        "campos": campos,
                    }
                )

        print("=" * 70)
        print("  LIMPEZA DE VALORES INVALIDOS (CNPJ/CPF)")
        print("=" * 70)
        print(f"Itens analisados:        {len(rows)}")
        print(f"Preservados (revisados): {preservados}")
        print(f"Valores suspeitos:       {len(suspeitos)}")
        print()

        if not suspeitos:
            print("Nada a limpar.")
            return {"limpos": 0, "preservados": preservados}

        for s in suspeitos:
            print(
                f"  #{s['id']:>3}  remessa={s['remessa']}  "
                f"valor={s['valor_atual']}  {s['arquivo']}"
            )

        print()

        if not aplicar:
            print("Modo DRY-RUN: nada foi gravado.")
            print("Para aplicar: python limpar_valores_invalidos.py --aplicar")
            return {
                "limpos": 0,
                "suspeitos": len(suspeitos),
                "preservados": preservados,
            }

        for s in suspeitos:
            campos = s["campos"]
            campos["valor_reembolso"] = None

            dados = s["dados"]
            dados["campos"] = campos
            dados["faltando"] = [
                c for c in ("valor_reembolso", "data_reembolso") if not campos.get(c)
            ]

            con.execute(
                "UPDATE fila_treinamento SET campos_json = ? WHERE id = ?",
                (json.dumps(dados, ensure_ascii=False), s["id"]),
            )

        con.commit()
        print(f"Limpos: {len(suspeitos)} itens zerados para revisao manual.")
        return {
            "limpos": len(suspeitos),
            "preservados": preservados,
        }

    finally:
        con.close()


def main():
    parser = argparse.ArgumentParser(
        description="Limpa valores que na verdade sao CNPJ ou CPF."
    )
    parser.add_argument(
        "--aplicar",
        action="store_true",
        help="Grava as correcoes (sem isso, roda em dry-run).",
    )
    args = parser.parse_args()
    limpar(aplicar=args.aplicar)


if __name__ == "__main__":
    main()

"""Lista os layouts treinados formatados, com os rótulos descobertos.

Uso:
    python ver_layouts.py                    # todos os layouts
    python ver_layouts.py --tipo GUIA_FISCAL
    python ver_layouts.py --min-confirmacoes 3
    python ver_layouts.py --json             # saída em JSON
"""

import argparse
import json
import sys

import database as db


def _carregar(tipo=None, min_confirmacoes=None):
    """Busca os layouts aplicando os filtros pedidos."""
    sql = "SELECT * FROM layout_treinado WHERE 1=1"
    params = []

    if tipo:
        sql += " AND tipo_documento = ?"
        params.append(tipo)
    if min_confirmacoes:
        sql += " AND confirmacoes >= ?"
        params.append(min_confirmacoes)

    sql += " ORDER BY confirmacoes DESC, taxa_acerto DESC, tipo_documento"

    with db.cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def _formatar(entrada: dict) -> str:
    """Monta o bloco de exibição de um layout."""
    try:
        mapa = json.loads(entrada.get("campos_json") or "{}")
    except json.JSONDecodeError:
        mapa = {}

    linhas = []
    linhas.append("=" * 78)
    linhas.append(f"{entrada['tipo_documento']} | {entrada.get('nome_layout') or '-'}")
    linhas.append(
        f"Hash: {entrada.get('hash_layout')} | "
        f"Confirmações: {entrada.get('confirmacoes') or 0} | "
        f"Acerto: {entrada.get('taxa_acerto') or 0:.1f}%"
    )
    linhas.append(
        f"Criado: {entrada.get('data_criacao')} | "
        f"Último uso: {entrada.get('ultima_utilizacao') or '-'}"
    )
    linhas.append("")

    if mapa:
        linhas.append(f"RÓTULOS DESCOBERTOS ({len(mapa)}):")
        for campo, rotulo in mapa.items():
            linhas.append(f"  {campo} <- {rotulo}")
    else:
        linhas.append("RÓTULOS DESCOBERTOS: nenhum (layout vazio)")

    linhas.append("")
    return "\n".join(linhas)


def _resumo(entradas: list[dict]) -> str:
    """Conta por tipo de documento."""
    if not entradas:
        return "Nenhum layout treinado."

    por_tipo = {}
    for e in entradas:
        por_tipo[e["tipo_documento"]] = por_tipo.get(e["tipo_documento"], 0) + 1

    partes = [f"{n} {t}" for t, n in sorted(por_tipo.items(), key=lambda x: -x[1])]
    return f"{len(entradas)} layout(s): " + ", ".join(partes)


def main():
    parser = argparse.ArgumentParser(
        description="Lista os layouts treinados formatados."
    )
    parser.add_argument("--tipo", help="Filtra por tipo de documento")
    parser.add_argument(
        "--min-confirmacoes",
        type=int,
        help="Só layouts com pelo menos N confirmações",
    )
    parser.add_argument(
        "--json", action="store_true", help="Saída em JSON em vez de formatada"
    )
    args = parser.parse_args()

    db.init_db()
    entradas = _carregar(tipo=args.tipo, min_confirmacoes=args.min_confirmacoes)

    if args.json:
        print(json.dumps(entradas, ensure_ascii=False, indent=2, default=str))
        return 0

    if not entradas:
        print("Nenhum layout treinado.")
        print()
        print("Os layouts são gravados quando o pdf_reader.py extrai campos")
        print("com rótulo identificado. Se estiver vazio, confirme que o")
        print("pdf_reader.py tem 'import layout_cache' no topo.")
        return 0

    print()
    print(_resumo(entradas))
    print()

    for entrada in entradas:
        print(_formatar(entrada))

    print("=" * 78)
    print(f"Total exibido: {len(entradas)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

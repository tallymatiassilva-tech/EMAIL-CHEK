"""Marca entradas da fila de treinamento como resolvidas.

Sempre mostra o que será alterado e pede confirmação antes de aplicar.
Sem --sim, nada é gravado.

Uso:
    python resolver_fila.py --id 1
    python resolver_fila.py --id 1 2 3
    python resolver_fila.py --remessa 10084206883004
    python resolver_fila.py --tipo GUIA_FISCAL
    python resolver_fila.py --todos-pendentes
    python resolver_fila.py --id 1 --sim          # sem confirmação
    python resolver_fila.py --remessa X --reabrir # volta para pendente
"""

import argparse
import json
import sys

import database as db


def _buscar(ids=None, remessa=None, tipo=None, todos_pendentes=False):
    """Busca as entradas que serão afetadas, aplicando os filtros."""
    sql = "SELECT * FROM fila_treinamento WHERE 1=1"
    params = []

    if ids:
        placeholders = ",".join("?" for _ in ids)
        sql += f" AND id IN ({placeholders})"
        params.extend(ids)
    elif remessa:
        sql += " AND numero_remessa LIKE ?"
        params.append(f"%{remessa}%")
    elif tipo:
        sql += " AND tipo_documento = ?"
        params.append(tipo)
    elif todos_pendentes:
        sql += " AND resolvido=0"
    else:
        return []

    sql += " ORDER BY id"
    with db.cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def _resumo_entrada(entrada: dict) -> str:
    """Uma linha compacta por entrada, para o preview."""
    try:
        payload = json.loads(entrada.get("campos_json") or "{}")
    except json.JSONDecodeError:
        payload = {}
    faltando = payload.get("faltando") or []
    estado = "resolvido" if entrada.get("resolvido") else "pendente"
    return (
        f"  [{entrada['id']}] {entrada['numero_remessa']} | "
        f"{entrada['tipo_documento']} | {estado} | "
        f"faltando: {', '.join(faltando) if faltando else '-'}"
    )


def _aplicar(ids: list[int], valor: int) -> int:
    """Atualiza o campo resolvido. Retorna quantas linhas mudaram."""
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    with db.cursor() as cur:
        cur.execute(
            f"UPDATE fila_treinamento SET resolvido=? WHERE id IN ({placeholders})",
            [valor, *ids],
        )
        return cur.rowcount


def main():
    parser = argparse.ArgumentParser(
        description="Marca entradas da fila de treinamento como resolvidas."
    )
    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--id", type=int, nargs="+", help="Um ou mais IDs")
    grupo.add_argument("--remessa", help="Filtra por número de remessa (parcial)")
    grupo.add_argument("--tipo", help="Filtra por tipo de documento")
    grupo.add_argument(
        "--todos-pendentes",
        action="store_true",
        help="Marca TODAS as entradas pendentes",
    )
    parser.add_argument(
        "--reabrir",
        action="store_true",
        help="Reverte: volta as entradas para pendente (resolvido=0)",
    )
    parser.add_argument(
        "--sim", action="store_true", help="Aplica sem pedir confirmação"
    )
    args = parser.parse_args()

    db.init_db()

    entradas = _buscar(
        ids=args.id,
        remessa=args.remessa,
        tipo=args.tipo,
        todos_pendentes=args.todos_pendentes,
    )

    if not entradas:
        print("Nenhuma entrada encontrada com esses filtros.")
        return 1

    acao = "reabrir (voltar para pendente)" if args.reabrir else "marcar como resolvida"
    alvo = 0 if args.reabrir else 1

    print()
    print(f"Ação: {acao}")
    print(f"Entradas afetadas: {len(entradas)}")
    print()
    for e in entradas:
        print(_resumo_entrada(e))
    print()

    # Confirmação obrigatória, exceto com --sim
    if not args.sim:
        resposta = input(f"Confirmar {acao} em {len(entradas)} entrada(s)? [s/N] ")
        if resposta.strip().lower() not in ("s", "sim", "y", "yes"):
            print("Cancelado. Nada foi alterado.")
            return 0

    ids = [e["id"] for e in entradas]
    alteradas = _aplicar(ids, alvo)

    print()
    print(f"OK: {alteradas} entrada(s) atualizada(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

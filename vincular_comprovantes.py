"""Vincula comprovantes da fila aos reembolsos pela remessa, em lote.

Le os itens da fila de treinamento do tipo COMPROVANTE_REEMBOLSO que ja
tem valor_reembolso e data_reembolso, grava esses valores na tabela
reembolsos pela numero_remessa e opcionalmente reprocessa a remessa.

Uso:
    python vincular_comprovantes.py              # dry-run
    python vincular_comprovantes.py --aplicar    # grava e reprocessa
    python vincular_comprovantes.py --aplicar --sem-reprocessar
"""

import argparse
import json
import sqlite3
from pathlib import Path

import database as db
from config import DB_PATH
from log_manager import get_logger

log = get_logger("vincular_comprovantes")

CAMPOS_NECESSARIOS = ["valor_reembolso", "data_reembolso"]


def _conectar() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _campos_do_json(campos_json: str | None) -> dict:
    """Le o campos_json e devolve os campos (aceita os dois formatos)."""
    try:
        dados = json.loads(campos_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(dados, dict):
        return {}
    return dados.get("campos", dados) or {}


def _listar_comprovantes(apenas_resolvidos: bool = False) -> list[dict]:
    """Comprovantes de cliente na fila que tem remessa vinculada."""
    sql = (
        "SELECT id, numero_remessa, caminho_arquivo, campos_json, "
        "confianca, resolvido FROM fila_treinamento "
        "WHERE tipo_documento = 'COMPROVANTE_REEMBOLSO' "
        "AND numero_remessa IS NOT NULL "
        "AND numero_remessa != ''"
    )
    if apenas_resolvidos:
        sql += " AND resolvido = 1"
    sql += " ORDER BY numero_remessa, id DESC"

    con = _conectar()
    try:
        return [dict(r) for r in con.execute(sql).fetchall()]
    finally:
        con.close()


def _reembolso_existe(numero_remessa: str) -> bool:
    con = _conectar()
    try:
        r = con.execute(
            "SELECT 1 FROM reembolsos WHERE numero_remessa = ?",
            (numero_remessa,),
        ).fetchone()
        return r is not None
    finally:
        con.close()


def _gravar_no_reembolso(
    numero_remessa: str,
    valor: float,
    data: str,
    caminho: str,
) -> None:
    """Grava os dados do comprovante na tabela reembolsos."""
    con = _conectar()
    try:
        con.execute(
            "UPDATE reembolsos SET "
            "valor_reembolso_cliente = ?, "
            "data_reembolso_cliente = ?, "
            "caminho_comp_cliente = COALESCE(caminho_comp_cliente, ?), "
            "data_atualizacao = datetime('now') "
            "WHERE numero_remessa = ?",
            (valor, data, caminho, numero_remessa),
        )
        con.commit()
    finally:
        con.close()


def vincular(
    aplicar: bool = False,
    reprocessar: bool = True,
    apenas_resolvidos: bool = False,
) -> dict:
    """Vincula comprovantes da fila aos reembolsos pela remessa."""
    itens = _listar_comprovantes(apenas_resolvidos)

    total = len(itens)
    vinculados = 0
    sem_reembolso = 0
    sem_campos = 0
    detalhes: list[dict] = []
    remessas_afetadas: set[str] = set()

    for item in itens:
        remessa = item["numero_remessa"]
        campos = _campos_do_json(item.get("campos_json"))

        valor = campos.get("valor_reembolso")
        data = campos.get("data_reembolso")
        faltando = [c for c in CAMPOS_NECESSARIOS if not campos.get(c)]

        if faltando:
            sem_campos += 1
            detalhes.append(
                {
                    "id": item["id"],
                    "remessa": remessa,
                    "arquivo": Path(item["caminho_arquivo"]).name,
                    "status": "SEM_CAMPOS",
                    "faltando": faltando,
                    "valor": valor,
                    "data": data,
                }
            )
            continue

        if not _reembolso_existe(remessa):
            sem_reembolso += 1
            detalhes.append(
                {
                    "id": item["id"],
                    "remessa": remessa,
                    "arquivo": Path(item["caminho_arquivo"]).name,
                    "status": "SEM_REEMBOLSO",
                    "faltando": [],
                    "valor": valor,
                    "data": data,
                }
            )
            continue

        vinculados += 1
        remessas_afetadas.add(remessa)
        detalhes.append(
            {
                "id": item["id"],
                "remessa": remessa,
                "arquivo": Path(item["caminho_arquivo"]).name,
                "status": "VINCULADO",
                "faltando": [],
                "valor": valor,
                "data": data,
            }
        )

        if aplicar:
            try:
                _gravar_no_reembolso(
                    remessa,
                    float(valor),
                    str(data),
                    item["caminho_arquivo"],
                )
            except Exception as e:
                log.error("Falha ao gravar remessa %s: %s", remessa, e)
                detalhes[-1]["status"] = "ERRO_GRAVACAO"

    reprocessadas = 0
    if aplicar and reprocessar and remessas_afetadas:
        try:
            from reprocessar import reprocessar_lote

            log.info("Reprocessando %d remessas...", len(remessas_afetadas))
            ret = reprocessar_lote(sorted(remessas_afetadas))
            reprocessadas = ret.get("processadas", 0)
        except Exception as e:
            log.error("Falha no reprocessamento: %s", e)

    return {
        "total": total,
        "vinculados": vinculados,
        "sem_reembolso": sem_reembolso,
        "sem_campos": sem_campos,
        "reprocessadas": reprocessadas,
        "aplicado": aplicar,
        "detalhes": detalhes,
    }


def _imprimir(ret: dict) -> None:
    for d in ret["detalhes"]:
        marca = {
            "VINCULADO": "[OK]",
            "SEM_CAMPOS": "[--]",
            "SEM_REEMBOLSO": "[!!]",
            "ERRO_GRAVACAO": "[XX]",
        }.get(d["status"], "[??]")

        print(f"{marca} #{d['id']} remessa={d['remessa']} {d['arquivo']}")
        if d["status"] == "SEM_CAMPOS":
            print(f"     faltando: {d['faltando']}")
        elif d["status"] == "SEM_REEMBOLSO":
            print("     remessa nao existe na tabela reembolsos")
        elif d["status"] == "VINCULADO":
            print(f"     valor={d['valor']}  data={d['data']}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Vincula comprovantes da fila aos reembolsos pela remessa."
    )
    parser.add_argument(
        "--aplicar",
        action="store_true",
        help="Grava no banco (padrao: dry-run).",
    )
    parser.add_argument(
        "--sem-reprocessar",
        action="store_true",
        help="Nao roda o motor de conciliacao depois de gravar.",
    )
    parser.add_argument(
        "--so-resolvidos",
        action="store_true",
        help="Considera apenas itens ja resolvidos na fila.",
    )
    args = parser.parse_args()

    modo = "APLICAR" if args.aplicar else "DRY-RUN (nada sera gravado)"
    print("=" * 70)
    print(f"  VINCULACAO DE COMPROVANTES — {modo}")
    print("=" * 70)

    ret = vincular(
        aplicar=args.aplicar,
        reprocessar=not args.sem_reprocessar,
        apenas_resolvidos=args.so_resolvidos,
    )

    _imprimir(ret)

    print("=" * 70)
    print("  RESULTADO")
    print("=" * 70)
    print(f"  Comprovantes encontrados: {ret['total']}")
    print(f"  Vinculados:               {ret['vinculados']}")
    print(f"  Sem campos:               {ret['sem_campos']}")
    print(f"  Sem reembolso:            {ret['sem_reembolso']}")
    if args.aplicar:
        print(f"  Remessas reprocessadas:   {ret['reprocessadas']}")
    else:
        print("  Modo DRY-RUN: nada foi gravado.")
        print("  Para aplicar: python vincular_comprovantes.py --aplicar")


if __name__ == "__main__":
    main()
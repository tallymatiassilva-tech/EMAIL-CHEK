"""Reprocessa a fila de treinamento.

Roda o OCR e a extracao em todos os itens pendentes da fila e, opcionalmente,
grava os campos extraidos no banco.

Dois modos:
- Dry-run (padrao): mostra o resultado sem gravar nada.
- Aplicar: grava os campos e marca os itens como resolvidos.

Uso pela linha de comando:
    python reprocessar_fila.py              # dry-run
    python reprocessar_fila.py --aplicar    # grava
    python reprocessar_fila.py --limite 8   # so os 8 primeiros

Uso pelo app Streamlit:
    from reprocessar_fila import reprocessar
    ret = reprocessar(limite=None, aplicar=False)
"""

import argparse
import json
import sqlite3
from pathlib import Path

import ocr_engine
from config import DB_PATH, TipoDoc
from log_manager import get_logger
from pdf_reader import extrair_por_tipo

log = get_logger("reprocessar_fila")

# Campos que precisam existir para o item ser considerado resolvido,
# por tipo de documento.
CAMPOS_OBRIGATORIOS = {
    TipoDoc.COMP_CLIENTE: ["valor_reembolso", "data_reembolso"],
    TipoDoc.COMP_JADLOG: ["valor_pago_jadlog", "data_pagamento_jadlog"],
    TipoDoc.GUIA: ["valor_guia", "data_emissao", "data_vencimento"],
}


# ---------------------------------------------------------------------------
# BANCO
# ---------------------------------------------------------------------------


def _conectar() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _listar_pendentes(limite: int | None = None) -> list[dict]:
    """Itens da fila ainda nao resolvidos, ordenados por id decrescente."""
    sql = (
        "SELECT id, tipo_documento, numero_remessa, caminho_arquivo, "
        "campos_json, confianca FROM fila_treinamento "
        "WHERE resolvido = 0 ORDER BY id DESC"
    )
    if limite:
        sql += f" LIMIT {int(limite)}"

    con = _conectar()
    try:
        return [dict(r) for r in con.execute(sql).fetchall()]
    finally:
        con.close()


def _gravar_item(
    item_id: int,
    campos: dict,
    confianca: int,
    campos_json_antigo: str | None = None,
    marcar_resolvido: bool = True,
) -> None:
    """Grava os campos extraidos preservando texto e _rotulos.

    O JSON anterior e lido e reaproveitado: as chaves que nao sao campos
    (texto, _rotulos e qualquer outra) continuam no registro. So os campos
    e a confianca sao substituidos.

    marcar_resolvido=False permite gravar uma extracao parcial sem tirar
    o item da fila.
    """
    try:
        base = json.loads(campos_json_antigo or "{}")
    except (json.JSONDecodeError, TypeError):
        base = {}
    if not isinstance(base, dict):
        base = {}

    base["campos"] = campos
    base["confianca"] = confianca

    resolvido = 1 if marcar_resolvido else 0

    con = _conectar()
    try:
        con.execute(
            "UPDATE fila_treinamento "
            "SET campos_json = ?, confianca = ?, resolvido = ? "
            "WHERE id = ?",
            (
                json.dumps(base, ensure_ascii=False),
                confianca,
                resolvido,
                item_id,
            ),
        )
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# EXTRACAO
# ---------------------------------------------------------------------------


def _campos_do_item(item: dict) -> dict:
    """Le o campos_json do item e devolve o dicionario de campos.

    Aceita os dois formatos:
    - {"campos": {...}, "confianca": ...}  -> devolve o conteudo de "campos"
    - {...campos soltos na raiz...}        -> devolve o dict inteiro
    """
    try:
        dados = json.loads(item.get("campos_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(dados, dict):
        return {}
    return dados.get("campos", dados) or {}


def _faltando(campos: dict, tipo: str) -> list[str]:
    """Campos obrigatorios que estao vazios para o tipo do documento."""
    obrigatorios = CAMPOS_OBRIGATORIOS.get(tipo, [])
    return [c for c in obrigatorios if not campos.get(c)]


def _extrair(item: dict) -> tuple[dict, list[str], int]:
    """Roda o OCR e a extracao em um item da fila.

    Retorna (campos_extraidos, campos_faltando, confianca).
    """
    caminho = Path(item["caminho_arquivo"])
    tipo = item["tipo_documento"]

    if not caminho.is_file():
        log.warning("Arquivo nao encontrado: %s", caminho)
        return {}, _faltando({}, tipo), 0

    try:
        resultado = extrair_por_tipo(caminho, tipo)
    except Exception as e:
        log.error("Falha ao extrair %s: %s", caminho.name, e)
        return {}, _faltando({}, tipo), 0

    # Remove as chaves internas que nao vao para o banco
    campos = {
        k: v
        for k, v in resultado.items()
        if k not in ("texto", "_rotulos", "tipo", "confianca")
    }
    confianca = int(resultado.get("confianca") or 0)
    faltando = _faltando(campos, tipo)

    return campos, faltando, confianca


# ---------------------------------------------------------------------------
# API PUBLICA
# ---------------------------------------------------------------------------


def reprocessar(
    limite: int | None = None,
    aplicar: bool = False,
    gravar_parciais: bool = False,
) -> dict:
    """Reprocessa a fila de treinamento.

    Args:
        limite: numero maximo de itens a processar. None = todos.
        aplicar: se True, grava os campos no banco.
                 Se False (padrao), apenas simula (dry-run).
        gravar_parciais: quando True, tambem grava itens que continuam
                 faltando campos (extracao parcial), sem marca-los como
                 resolvidos. Padrao False: so grava o que ficou completo.

    Retorna:
        {
            "total": int,
            "resolvidos": int,
            "falhando": int,
            "erros": int,
            "aplicado": bool,
            "detalhes": list[dict],
        }

    Cada item de `detalhes` tem:
        id, arquivo, tipo, antes, depois, faltando, confianca, status
    """
    ocr_engine._CACHE.clear()
    log.info("Cache do OCR limpo")

    pendentes = _listar_pendentes(limite)
    total = len(pendentes)

    resolvidos = 0
    falhando = 0
    erros = 0
    detalhes: list[dict] = []

    for i, item in enumerate(pendentes, start=1):
        nome = Path(item["caminho_arquivo"]).name
        tipo = item["tipo_documento"]
        antes = _campos_do_item(item)

        log.info("[%d/%d] ID %s | %s", i, total, item["id"], nome)

        try:
            campos, faltando, confianca = _extrair(item)
        except Exception as e:
            log.error("Erro no item %s: %s", item["id"], e)
            erros += 1
            detalhes.append(
                {
                    "id": item["id"],
                    "arquivo": nome,
                    "tipo": tipo,
                    "antes": antes,
                    "depois": {},
                    "faltando": [],
                    "confianca": 0,
                    "status": "ERRO",
                }
            )
            continue

        if faltando:
            falhando += 1
            status = "FALHANDO"
        else:
            resolvidos += 1
            status = "RESOLVIDO"

        detalhes.append(
            {
                "id": item["id"],
                "arquivo": nome,
                "tipo": tipo,
                "antes": antes,
                "depois": campos,
                "faltando": faltando,
                "confianca": confianca,
                "status": status,
            }
        )

        if aplicar:
            deve_gravar = status == "RESOLVIDO" or (
                gravar_parciais and status == "FALHANDO"
            )
            if deve_gravar:
                try:
                    _gravar_item(
                        item["id"],
                        campos,
                        confianca,
                        item.get("campos_json"),
                        marcar_resolvido=(status == "RESOLVIDO"),
                    )
                except Exception as e:
                    log.error("Falha ao gravar item %s: %s", item["id"], e)
                    erros += 1

    log.info(
        "Reprocessamento: %d processados, %d resolvidos, %d falhando, %d erros",
        total,
        resolvidos,
        falhando,
        erros,
    )

    return {
        "total": total,
        "resolvidos": resolvidos,
        "falhando": falhando,
        "erros": erros,
        "aplicado": aplicar,
        "detalhes": detalhes,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _imprimir_detalhes(ret: dict) -> None:
    """Imprime o resultado no formato do script de linha de comando."""
    for d in ret["detalhes"]:
        print(f"[{d['id']}] {d['arquivo']} ({d['tipo']})")
        if d["status"] == "ERRO":
            print("  >>> ERRO ao processar")
            continue
        faltando = d["faltando"]
        if faltando:
            print(f"  >>> AINDA FALHANDO: {faltando}")
        else:
            print("  >>> RESOLVIDO: todos os campos extraidos")
        print(f"  conf={d['confianca']}%")


def main() -> None:
    parser = argparse.ArgumentParser(description="Reprocessa a fila de treinamento.")
    parser.add_argument(
        "--aplicar",
        action="store_true",
        help="Grava os campos no banco (padrao: dry-run).",
    )
    parser.add_argument(
        "--limite",
        type=int,
        default=None,
        help="Numero maximo de itens a processar.",
    )
    parser.add_argument(
        "--parciais",
        action="store_true",
        help="Tambem grava itens com extracao parcial (sem marcar como resolvidos).",
    )
    args = parser.parse_args()

    modo = "APLICAR" if args.aplicar else "DRY-RUN (nada sera gravado)"
    print("=" * 70)
    print(f"  REPROCESSAMENTO DA FILA — {modo}")
    print("=" * 70)

    ret = reprocessar(
        limite=args.limite,
        aplicar=args.aplicar,
        gravar_parciais=args.parciais,
    )
    _imprimir_detalhes(ret)

    print("=" * 70)
    print("  RESULTADO")
    print("=" * 70)
    print(f"  Total processado:   {ret['total']}")
    print(f"  Resolvidos:         {ret['resolvidos']}")
    print(f"  Ainda falhando:     {ret['falhando']}")
    print(f"  Erros:              {ret['erros']}")
    if not args.aplicar:
        print("  Modo DRY-RUN: nada foi gravado.")
        print("  Para aplicar: python reprocessar_fila.py --aplicar")
    else:
        print("  Campos gravados no banco.")


if __name__ == "__main__":
    main()

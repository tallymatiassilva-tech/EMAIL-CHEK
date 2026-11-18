"""Reprocessa os itens da fila de treinamento com o OCR novo.

Para cada entrada nao resolvida da fila_treinamento:
1. Limpa o cache em memoria do OCR (o texto antigo esta la)
2. Reextrai o texto do arquivo com o ocr_engine novo
3. Reextrai os campos com o pdf_reader novo
4. Compara com o que estava na fila
5. Mostra o antes/depois

NAO altera o banco por padrao. Use --aplicar para gravar os resultados.

Uso:
    python reprocessar_fila.py              # dry-run: so mostra
    python reprocessar_fila.py --aplicar    # grava os resultados
    python reprocessar_fila.py --remessa 18131502322673
"""

import argparse
import json
import sys
from pathlib import Path

import database as db
import ocr_engine
import pdf_reader
from config import TipoDoc
from log_manager import get_logger

log = get_logger("reprocessar_fila")

# Campos que o pdf_reader precisa extrair para o item sair da fila
CAMPOS_OBRIGATORIOS = {
    TipoDoc.COMP_CLIENTE: ["valor_reembolso", "data_reembolso"],
    TipoDoc.COMP_JADLOG: ["valor_pago_jadlog", "data_pagamento_jadlog"],
    TipoDoc.GUIA: ["valor_guia", "data_vencimento"],
}


def _carregar_fila(remessa: str | None = None) -> list[dict]:
    """Carrega as entradas nao resolvidas da fila."""
    sql = "SELECT * FROM fila_treinamento WHERE resolvido = 0"
    params = []
    if remessa:
        sql += " AND numero_remessa = ?"
        params.append(remessa)
    sql += " ORDER BY id DESC"

    with db.cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def _campos_faltantes(campos: dict, tipo: str) -> list[str]:
    """Lista os campos obrigatorios que nao foram extraidos."""
    obrigatorios = CAMPOS_OBRIGATORIOS.get(tipo, [])
    return [c for c in obrigatorios if not campos.get(c)]


def _limpar_cache_ocr():
    """Limpa o cache em memoria do OCR.

    Sem isso, o reprocessamento devolve o texto antigo (ruim) do cache,
    porque a chave e o hash do arquivo, que nao mudou.
    """
    ocr_engine._CACHE.clear()
    log.info("Cache do OCR limpo")


def _reprocessar_entrada(entrada: dict) -> dict:
    """Reprocessa uma entrada da fila.

    Retorna dict com o antes/depois.
    """
    arquivo = Path(entrada["caminho_arquivo"])
    tipo = entrada["tipo_documento"]

    resultado = {
        "id": entrada["id"],
        "remessa": entrada.get("numero_remessa"),
        "arquivo": arquivo.name,
        "tipo": tipo,
        "ok": False,
        "motivo": "",
        "antes": {},
        "depois": {},
    }

    if not arquivo.is_file():
        resultado["motivo"] = "Arquivo nao encontrado"
        return resultado

    # Campos que estavam na fila (o que falhou antes)
    try:
        campos_antes = json.loads(entrada.get("campos_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        campos_antes = {}

    resultado["antes"] = {
        "valor": campos_antes.get("valor_reembolso")
        or campos_antes.get("valor_pago_jadlog")
        or campos_antes.get("valor_guia"),
        "data": campos_antes.get("data_reembolso")
        or campos_antes.get("data_pagamento_jadlog")
        or campos_antes.get("data_vencimento"),
        "faltantes": _campos_faltantes(campos_antes, tipo),
    }

    # Reextrai com o OCR novo
    try:
        campos_novos = pdf_reader.extrair_por_tipo(arquivo, tipo)
    except Exception as e:
        resultado["motivo"] = f"Erro na extracao: {e}"
        return resultado

    resultado["depois"] = {
        "valor": campos_novos.get("valor_reembolso")
        or campos_novos.get("valor_pago_jadlog")
        or campos_novos.get("valor_guia"),
        "data": campos_novos.get("data_reembolso")
        or campos_novos.get("data_pagamento_jadlog")
        or campos_novos.get("data_vencimento"),
        "faltantes": _campos_faltantes(campos_novos, tipo),
        "confianca": campos_novos.get("confianca"),
        "rotulos": campos_novos.get("_rotulos", {}),
    }

    resultado["ok"] = True
    return resultado


def _aplicar_resultado(resultado: dict, campos_novos: dict):
    """Atualiza a fila com o resultado do reprocessamento.

    Se o item agora tem todos os campos, marca como resolvido.
    Senao, atualiza o campos_json com o que foi extraido.
    """
    faltantes = resultado["depois"]["faltantes"]
    resolvido = 0 if faltantes else 1

    with db.cursor() as cur:
        cur.execute(
            """UPDATE fila_treinamento
               SET campos_json = ?, confianca = ?, resolvido = ?
               WHERE id = ?""",
            (
                json.dumps(campos_novos, default=str),
                campos_novos.get("confianca", 0),
                resolvido,
                resultado["id"],
            ),
        )

    return resolvido


def _formatar_valor(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"R$ {v:.2f}"
    return str(v)


def main():
    parser = argparse.ArgumentParser(
        description="Reprocessa os itens da fila de treinamento"
    )
    parser.add_argument(
        "--aplicar",
        action="store_true",
        help="Grava os resultados no banco (padrao: dry-run)",
    )
    parser.add_argument(
        "--remessa",
        type=str,
        default=None,
        help="Filtra por numero de remessa",
    )
    parser.add_argument(
        "--limite",
        type=int,
        default=None,
        help="Processa no maximo N entradas",
    )
    args = parser.parse_args()

    db.init_db()

    entradas = _carregar_fila(args.remessa)
    if not entradas:
        print("\n  Fila vazia. Nada a reprocessar.\n")
        return 0

    if args.limite:
        entradas = entradas[: args.limite]

    modo = "APLICAR" if args.aplicar else "DRY-RUN (nada sera gravado)"
    print(f"\n{'=' * 70}")
    print(f"  REPROCESSAMENTO DA FILA — {modo}")
    print(f"{'=' * 70}")
    print(f"\n  Entradas a processar: {len(entradas)}\n")

    _limpar_cache_ocr()

    resolvidos = 0
    ainda_falhando = 0
    erros = 0

    for i, entrada in enumerate(entradas, 1):
        print(
            f"[{i}/{len(entradas)}] ID {entrada['id']} | {entrada.get('numero_remessa')}"
        )
        print(f"  Arquivo: {Path(entrada['caminho_arquivo']).name}")

        resultado = _reprocessar_entrada(entrada)

        if not resultado["ok"]:
            print(f"  ERRO: {resultado['motivo']}\n")
            erros += 1
            continue

        antes = resultado["antes"]
        depois = resultado["depois"]

        print(
            f"  ANTES:  valor={_formatar_valor(antes['valor'])} "
            f"data={antes['data'] or '-'} "
            f"faltando={antes['faltantes']}"
        )
        print(
            f"  DEPOIS: valor={_formatar_valor(depois['valor'])} "
            f"data={depois['data'] or '-'} "
            f"faltando={depois['faltantes']} "
            f"conf={depois['confianca']}%"
        )

        if depois["rotulos"]:
            print(f"  Rotulos: {depois['rotulos']}")

        if not depois["faltantes"]:
            print("  >>> RESOLVIDO: todos os campos extraidos")
            resolvidos += 1
        else:
            print(f"  >>> AINDA FALHANDO: {depois['faltantes']}")
            ainda_falhando += 1

        if args.aplicar:
            try:
                campos_novos = pdf_reader.extrair_por_tipo(
                    Path(entrada["caminho_arquivo"]), entrada["tipo_documento"]
                )
                _aplicar_resultado(resultado, campos_novos)
                print("  (gravado no banco)")
            except Exception as e:
                print(f"  ERRO ao gravar: {e}")
                erros += 1

        print()

    print("=" * 70)
    print(f"  RESULTADO")
    print("=" * 70)
    print(f"  Total processado:   {len(entradas)}")
    print(f"  Resolvidos:         {resolvidos}")
    print(f"  Ainda falhando:     {ainda_falhando}")
    print(f"  Erros:              {erros}")

    if not args.aplicar:
        print(f"\n  Modo DRY-RUN: nada foi gravado.")
        print(f"  Para aplicar: python reprocessar_fila.py --aplicar")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

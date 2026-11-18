"""Regrava a fila de treinamento com os valores do motor atual.

A fila guarda o resultado da extração no momento em que o arquivo foi
enfileirado. Quando o motor melhora, os itens antigos ficam com valores
desatualizados (None). Este script recalcula e regrava.

Uso:
    python atualizar_fila.py            # só pendentes (resolvido = 0)
    python atualizar_fila.py --todos    # todos os itens
"""

import json
import sys
from pathlib import Path

import database as db
import pdf_reader
from config import TipoDoc

# Campos que, se vazios, entram na lista "faltando"
CAMPOS_OBRIGATORIOS = ["valor_reembolso", "data_reembolso"]

# Tipos de documento que este script sabe recalcular
TIPOS_SUPORTADOS = {TipoDoc.COMP_CLIENTE}


def _campos_do_json(campos_json: str | None) -> dict:
    """Le o campos_json e devolve o dicionario de campos.

    Aceita os dois formatos:
    - {"campos": {...}, "confianca": ...}  -> devolve o conteudo de "campos"
    - {...campos soltos na raiz...}        -> devolve o dict inteiro
    """
    try:
        dados = json.loads(campos_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(dados, dict):
        return {}
    return dados.get("campos", dados) or {}


def recalcular_item(campos_json: str) -> dict | None:
    """Recalcula um item da fila chamando o motor atual.

    Retorna o novo dicionario {campos, faltando, texto, confianca} ou None
    se o arquivo nao existir mais ou o motor falhar.

    Preserva as chaves extras do JSON original (_rotulos, editado_manualmente
    e qualquer outra), sobrescrevendo apenas campos, faltando, texto e
    confianca.
    """
    try:
        dados = json.loads(campos_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(dados, dict):
        return None

    campos_antigos = dados.get("campos", dados) or {}
    caminho = campos_antigos.get("caminho")

    if not caminho:
        return None

    arquivo = Path(caminho)
    if not arquivo.is_file():
        return None

    try:
        novo = pdf_reader.extrair_comprovante_cliente(arquivo)
    except Exception as e:
        print("  erro em " + arquivo.name + ": " + str(e))
        return None

    campos_novos = dict(campos_antigos)
    campos_novos.update(
        {
            "nome_pagador": novo.get("nome_pagador") or "",
            "cnpj_pagador": novo.get("cnpj_pagador") or "",
            "valor_reembolso": novo.get("valor_reembolso"),
            "data_reembolso": novo.get("data_reembolso"),
            "banco_origem": novo.get("banco_origem") or "",
            "tipo_pagamento": novo.get("tipo_pagamento") or "",
            "id_transacao": novo.get("id_transacao") or "",
            "agencia": novo.get("agencia") or "",
            "conta": novo.get("conta") or "",
            "confianca": novo.get("confianca", 0),
            "tipo": novo.get("tipo") or campos_antigos.get("tipo"),
            "numero_remessa": campos_antigos.get("numero_remessa"),
            "caminho": caminho,
        }
    )

    faltando = [c for c in CAMPOS_OBRIGATORIOS if not campos_novos.get(c)]

    # Parte do JSON original para preservar _rotulos e chaves extras
    resultado = dict(dados)
    resultado["campos"] = campos_novos
    resultado["faltando"] = faltando
    resultado["texto"] = novo.get("texto") or dados.get("texto", "")
    resultado["confianca"] = novo.get("confianca", 0)

    return resultado


def atualizar_fila(somente_pendentes: bool = True) -> None:
    filtro = "WHERE resolvido = 0" if somente_pendentes else ""
    sql = (
        "SELECT id, tipo_documento, campos_json, confianca "
        "FROM fila_treinamento " + filtro
    )

    with db.cursor() as cur:
        cur.execute(sql)
        itens = cur.fetchall()

    print("Itens na fila: " + str(len(itens)))

    atualizados = 0
    melhorados = 0
    falhas = 0
    ignorados = 0

    for row in itens:
        item_id = row["id"]
        tipo = row["tipo_documento"]
        campos_json = row["campos_json"]
        conf_antiga = row["confianca"] or 0

        if tipo not in TIPOS_SUPORTADOS:
            ignorados += 1
            continue

        novo = recalcular_item(campos_json)
        if not novo:
            falhas += 1
            continue

        conf_nova = novo["confianca"]
        valor = novo["campos"].get("valor_reembolso")

        valor_antigo = _campos_do_json(campos_json).get("valor_reembolso")

        with db.cursor() as cur:
            cur.execute(
                "UPDATE fila_treinamento SET campos_json = ?, confianca = ? WHERE id = ?",
                (json.dumps(novo, ensure_ascii=False), conf_nova, item_id),
            )

        atualizados += 1

        if conf_nova > conf_antiga:
            melhorados += 1
            print(
                "  #"
                + str(item_id)
                + "  conf "
                + str(conf_antiga)
                + " -> "
                + str(conf_nova)
                + "  valor="
                + str(valor)
            )
        elif valor and not valor_antigo:
            melhorados += 1
            print("  #" + str(item_id) + "  valor recuperado: " + str(valor))

    print("")
    print("Atualizados: " + str(atualizados))
    print("Melhorados:  " + str(melhorados))
    print("Ignorados:   " + str(ignorados))
    print("Falhas:      " + str(falhas))


if __name__ == "__main__":
    todos = "--todos" in sys.argv
    atualizar_fila(somente_pendentes=not todos)

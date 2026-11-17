"""Cache de layouts por hash de estrutura de texto.

Quando um formato de documento aparece pela primeira vez, o extrator
descobre quais rótulos funcionaram e grava esse mapa. Na próxima vez que
o mesmo formato chegar, o extrator usa o mapa direto — sem tentar todos
os rótulos de novo.

O hash é calculado sobre a ESTRUTURA do texto (rótulos e ordem), não sobre
o conteúdo (valores e datas). Duas guias DARE com valores diferentes têm o
mesmo hash; um comprovante do Bradesco tem hash diferente de um do Santander.
"""

import hashlib
import json
import re
from datetime import datetime

import database as db
from log_manager import get_logger

log = get_logger("layout_cache")

# Quantos caracteres do texto entram no hash. 500 é suficiente para capturar
# o cabeçalho e os primeiros rótulos, que são o que define o formato.
TAMANHO_HASH = 500


def calcular_hash(texto: str) -> str:
    """Hash da estrutura do texto — ignora números, datas e valores.

    Normaliza para minúsculas, remove dígitos e colapsa espaços. Assim,
    'VALOR DO DOCUMENTO: 100,44' e 'VALOR DO DOCUMENTO: 250,00' geram o
    mesmo hash, porque o que define o formato é o rótulo, não o valor.
    """
    if not texto:
        return ""

    amostra = texto[:TAMANHO_HASH].lower()
    amostra = re.sub(r"\d", "", amostra)  # remove dígitos
    amostra = re.sub(r"[^\w\s]", " ", amostra)  # remove pontuação
    amostra = re.sub(r"\s+", " ", amostra).strip()

    return hashlib.sha256(amostra.encode("utf-8")).hexdigest()[:16]


def buscar_layout(tipo: str, texto: str) -> dict | None:
    """Busca um layout treinado para este formato.

    Retorna o dicionário de campos (campo -> rótulo que funcionou) ou None
    se o formato ainda não foi visto.
    """
    h = calcular_hash(texto)
    if not h:
        return None

    with db.cursor() as cur:
        cur.execute(
            """SELECT campos_json, taxa_acerto FROM layout_treinado
               WHERE tipo_documento=? AND hash_layout=?""",
            (tipo, h),
        )
        row = cur.fetchone()
        if not row:
            return None

        try:
            campos = json.loads(row["campos_json"])
        except json.JSONDecodeError:
            log.warning("Layout corrompido para hash %s", h)
            return None

        log.debug(
            "Layout encontrado: %s hash=%s acerto=%.1f%%",
            tipo,
            h,
            row["taxa_acerto"] or 0,
        )
        return campos


def gravar_layout(tipo: str, texto: str, campos: dict, rotulos_usados: dict) -> None:
    """Grava ou atualiza o layout deste formato.

    `rotulos_usados` mapeia campo -> rótulo que funcionou. Só os campos que
    vieram preenchidos entram no mapa — campos vazios não viram parte do
    layout, porque o rótulo deles não foi descoberto.

    Se o layout já existe, incrementa confirmações e recalcula a taxa de
    acerto. Se é novo, insere com taxa inicial de 100%.
    """
    h = calcular_hash(texto)
    if not h:
        return

    # Só guarda campos que vieram preenchidos E têm rótulo identificado
    mapa = {
        campo: rotulo
        for campo, rotulo in rotulos_usados.items()
        if campos.get(campo) and rotulo
    }
    if not mapa:
        return

    agora = datetime.now().isoformat(timespec="seconds")

    with db.cursor() as cur:
        cur.execute(
            """SELECT id, confirmacoes, taxa_acerto FROM layout_treinado
               WHERE tipo_documento=? AND hash_layout=?""",
            (tipo, h),
        )
        row = cur.fetchone()

        if row:
            confirmacoes = (row["confirmacoes"] or 0) + 1
            # Taxa sobe 1 ponto por confirmação, até 100
            taxa = min((row["taxa_acerto"] or 0) + 1, 100.0)
            cur.execute(
                """UPDATE layout_treinado
                   SET campos_json=?, confirmacoes=?, taxa_acerto=?,
                       ultima_utilizacao=?
                   WHERE id=?""",
                (
                    json.dumps(mapa, ensure_ascii=False),
                    confirmacoes,
                    taxa,
                    agora,
                    row["id"],
                ),
            )
            log.debug("Layout atualizado: %s hash=%s", tipo, h)
        else:
            cur.execute(
                """INSERT INTO layout_treinado
                   (tipo_documento, nome_layout, hash_layout, campos_json,
                    taxa_acerto, confirmacoes, data_criacao, ultima_utilizacao)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    tipo,
                    f"{tipo}_{h[:8]}",
                    h,
                    json.dumps(mapa, ensure_ascii=False),
                    100.0,
                    1,
                    agora,
                    agora,
                ),
            )
            log.info("Layout novo registrado: %s hash=%s", tipo, h)


def listar_layouts(tipo: str | None = None) -> list[dict]:
    """Lista os layouts treinados, opcionalmente filtrando por tipo."""
    sql = "SELECT * FROM layout_treinado"
    params = []
    if tipo:
        sql += " WHERE tipo_documento=?"
        params.append(tipo)
    sql += " ORDER BY confirmacoes DESC, taxa_acerto DESC"

    with db.cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

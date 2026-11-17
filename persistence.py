"""Persistência dos resultados de conciliação.

Grava o resultado do motor na tabela reembolsos e registra cada mudança
de status na tabela historico_conciliacao para auditoria.
"""

from datetime import datetime

from config import Status
from database import buscar_por_remessa, cursor, listar_pendentes_por_cnpj
from log_manager import get_logger

log = get_logger("persistence")

SCHEMA_HISTORICO = """
CREATE TABLE IF NOT EXISTS historico_conciliacao (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    numero_remessa    TEXT NOT NULL,
    status_anterior   TEXT,
    status_novo       TEXT NOT NULL,
    score_anterior    INTEGER,
    score_novo        INTEGER,
    camada            TEXT,
    valor_quitado     REAL,
    diferenca         REAL,
    motivo            TEXT,
    criado_em         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hist_remessa ON historico_conciliacao(numero_remessa);
CREATE INDEX IF NOT EXISTS idx_hist_data    ON historico_conciliacao(criado_em);
"""

# Garante que a tabela exista uma única vez por processo, independente
# de quem chame (pipeline, dashboard, exportador, novos layouts).
_INIT_FEITO = False


def init_historico():
    """Cria a tabela de histórico se não existir."""
    from database import get_conn

    conn = get_conn()
    conn.executescript(SCHEMA_HISTORICO)
    conn.commit()


def _garantir_historico():
    """Cria a tabela historico_conciliacao na primeira chamada do processo."""
    global _INIT_FEITO
    if not _INIT_FEITO:
        init_historico()
        _INIT_FEITO = True


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# GRAVAÇÃO DO RESULTADO
# ---------------------------------------------------------------------------


def salvar_resultado(resultado: dict) -> dict:
    """Grava o resultado do motor no banco.

    Atualiza reembolsos (status, score, valores) e registra no histórico.
    Retorna dict com o que foi alterado.
    """
    _garantir_historico()

    remessa = resultado.get("numero_remessa")
    if not remessa:
        log.warning("Resultado sem numero_remessa, ignorado")
        return {"ok": False, "motivo": "Sem número de remessa"}

    atual = buscar_por_remessa(remessa)
    if not atual:
        log.warning("Remessa %s não encontrada no banco", remessa)
        return {"ok": False, "motivo": "Remessa não encontrada"}

    status_anterior = atual.get("status")
    score_anterior = atual.get("score") or 0
    status_novo = resultado.get("status_final") or Status.REVISAO
    score_novo = resultado.get("score_final") or 0

    # Idempotência: mesmo status e mesmo score = nada a fazer
    if status_anterior == status_novo and score_anterior == score_novo:
        log.info(
            "Remessa %s já está em %s com score %s — ignorado",
            remessa,
            status_novo,
            score_novo,
        )
        return {"ok": True, "alterado": False, "motivo": "Estado idêntico"}

    # Extrai dados do lado cliente para atualizar valores
    lado_cliente = resultado.get("lado_cliente") or {}
    valor_quitado = lado_cliente.get("valor_quitado")
    diferenca = lado_cliente.get("diferenca")
    camada = lado_cliente.get("camada")

    # Atualiza a tabela reembolsos
    campos = {
        "status": status_novo,
        "score": score_novo,
        "data_atualizacao": _agora(),
    }
    if valor_quitado:
        campos["valor_reembolso_cliente"] = valor_quitado
    if lado_cliente.get("valor_comprovante") and not valor_quitado:
        campos["valor_reembolso_cliente"] = lado_cliente["valor_comprovante"]

    sets = ",".join(f"{k}=?" for k in campos)
    with cursor() as cur:
        cur.execute(
            f"UPDATE reembolsos SET {sets} WHERE numero_remessa=?",
            list(campos.values()) + [remessa],
        )

    # Registra no histórico
    with cursor() as cur:
        cur.execute(
            """INSERT INTO historico_conciliacao
               (numero_remessa, status_anterior, status_novo,
                score_anterior, score_novo, camada,
                valor_quitado, diferenca, motivo, criado_em)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                remessa,
                status_anterior,
                status_novo,
                score_anterior,
                score_novo,
                camada,
                valor_quitado,
                diferenca,
                lado_cliente.get("motivo") or resultado.get("motivo"),
                _agora(),
            ),
        )

    log.info(
        "Remessa %s: %s -> %s (score %s -> %s)",
        remessa,
        status_anterior,
        status_novo,
        score_anterior,
        score_novo,
    )

    return {
        "ok": True,
        "alterado": True,
        "remessa": remessa,
        "status_anterior": status_anterior,
        "status_novo": status_novo,
        "score_anterior": score_anterior,
        "score_novo": score_novo,
    }


# ---------------------------------------------------------------------------
# QUITAÇÃO DE MÚLTIPLOS REEMBOLSOS (comprovante agrupado)
# ---------------------------------------------------------------------------


def salvar_quitacao_agrupada(resultado: dict) -> dict:
    """Quando um comprovante quita N reembolsos, atualiza todos de uma vez."""
    _garantir_historico()

    lado_cliente = resultado.get("lado_cliente") or {}
    quitados = lado_cliente.get("quitados") or []

    if not quitados:
        return salvar_resultado(resultado)

    alterados = []
    for item in quitados:
        remessa = item.get("numero_remessa") or item.get("id")
        if not remessa:
            continue
        atual = buscar_por_remessa(str(remessa))
        if not atual:
            continue

        status_anterior = atual.get("status")
        with cursor() as cur:
            cur.execute(
                """UPDATE reembolsos
                   SET status=?, score=?, data_atualizacao=?
                   WHERE numero_remessa=?""",
                (Status.CONFERIDO, 100, _agora(), str(remessa)),
            )
            cur.execute(
                """INSERT INTO historico_conciliacao
                   (numero_remessa, status_anterior, status_novo,
                    score_anterior, score_novo, camada,
                    valor_quitado, diferenca, motivo, criado_em)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(remessa),
                    status_anterior,
                    Status.CONFERIDO,
                    atual.get("score") or 0,
                    100,
                    lado_cliente.get("camada"),
                    item.get("valor_reembolso_esperado"),
                    0.0,
                    "Quitado por comprovante agrupado",
                    _agora(),
                ),
            )
        alterados.append(str(remessa))

    log.info("Quitação agrupada: %d reembolsos atualizados", len(alterados))
    return {"ok": True, "alterado": bool(alterados), "quitados": alterados}


# ---------------------------------------------------------------------------
# CONSULTAS DE AUDITORIA
# ---------------------------------------------------------------------------


def historico_remessa(numero_remessa: str) -> list[dict]:
    """Retorna todas as mudanças de status de uma remessa."""
    _garantir_historico()
    with cursor() as cur:
        cur.execute(
            """SELECT * FROM historico_conciliacao
               WHERE numero_remessa=?
               ORDER BY criado_em DESC""",
            (numero_remessa,),
        )
        return [dict(r) for r in cur.fetchall()]


def historico_recente(limite: int = 100) -> list[dict]:
    """Retorna as últimas N mudanças de status do sistema."""
    _garantir_historico()
    with cursor() as cur:
        cur.execute(
            """SELECT * FROM historico_conciliacao
               ORDER BY criado_em DESC LIMIT ?""",
            (limite,),
        )
        return [dict(r) for r in cur.fetchall()]


def estatisticas_por_status() -> dict:
    """Conta reembolsos por status atual."""
    with cursor() as cur:
        cur.execute("SELECT status, COUNT(*) c FROM reembolsos GROUP BY status")
        return {r["status"]: r["c"] for r in cur.fetchall()}


def estatisticas_por_camada() -> dict:
    """Conta quantas conciliações caíram em cada camada."""
    _garantir_historico()
    with cursor() as cur:
        cur.execute(
            """SELECT camada, COUNT(*) c FROM historico_conciliacao
               WHERE camada IS NOT NULL GROUP BY camada"""
        )
        return {r["camada"]: r["c"] for r in cur.fetchall()}

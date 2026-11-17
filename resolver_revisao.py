"""Resolução manual de registros em revisão.

Permite ao usuário decidir o destino de reembolsos que o motor não
conseguiu conciliar sozinho. Toda decisão é registrada no histórico.
"""

import json
from datetime import datetime

import database as db
import persistence as ps
from config import Status
from log_manager import get_logger

log = get_logger("resolver_revisao")

# Status que exigem decisão humana
STATUS_REVISAO = {
    Status.REVISAO,
    Status.DIV_VALOR,
    Status.DIV_CNPJ,
    Status.DIV_NF,
    Status.DIV_DATA,
    Status.AGRUPADO,
}

# Ações possíveis
ACAO_CONFERIR = "CONFERIR"
ACAO_MANTER = "MANTER"
ACAO_DUPLICADO = "DUPLICADO"

MAPA_ACAO_STATUS = {
    ACAO_CONFERIR: Status.CONFERIDO,
    ACAO_MANTER: Status.REVISAO,
    ACAO_DUPLICADO: Status.DUPLICADO,
}


def listar_em_revisao() -> list[dict]:
    """Retorna todos os registros que precisam de decisão humana."""
    registros = db.listar()
    return [r for r in registros if r.get("status") in STATUS_REVISAO]


def _formatar(registro: dict, indice: int) -> str:
    """Monta o bloco de exibição de um registro em revisão."""
    linhas = []
    linhas.append("─" * 70)
    linhas.append(
        f"  [{indice}] {registro.get('numero_remessa')} — {registro.get('status')}"
    )
    linhas.append("─" * 70)

    cliente = registro.get("cliente") or "sem cliente"
    cnpj = registro.get("cnpj_cliente") or "sem CNPJ"
    linhas.append(f"  Cliente: {cliente}")
    linhas.append(f"  CNPJ:    {cnpj}")

    vg = registro.get("valor_guia")
    vj = registro.get("valor_pago_jadlog")
    vc = registro.get("valor_reembolso_cliente")
    ve = registro.get("valor_reembolso_esperado")

    linhas.append("")
    linhas.append(
        f"  Valor da guia:        R$ {vg:.2f}" if vg else "  Valor da guia:        —"
    )
    linhas.append(
        f"  Pago pela Jadlog:     R$ {vj:.2f}" if vj else "  Pago pela Jadlog:     —"
    )
    linhas.append(
        f"  Reembolso do cliente: R$ {vc:.2f}" if vc else "  Reembolso do cliente: —"
    )
    linhas.append(
        f"  Esperado:             R$ {ve:.2f}" if ve else "  Esperado:             —"
    )

    linhas.append("")
    linhas.append(f"  Emissão:     {registro.get('data_emissao') or '—'}")
    linhas.append(f"  Vencimento:  {registro.get('data_vencimento') or '—'}")
    linhas.append(f"  Pgto Jadlog: {registro.get('data_pagamento_jadlog') or '—'}")
    linhas.append(f"  Reembolso:   {registro.get('data_reembolso_cliente') or '—'}")

    linhas.append("")
    linhas.append(f"  Score: {registro.get('score') or 0}")

    # Mostra o motivo do último histórico, se existir
    hist = ps.historico_remessa(registro.get("numero_remessa"))
    if hist and hist[0].get("motivo"):
        linhas.append(f"  Motivo: {hist[0]['motivo']}")

    return "\n".join(linhas)


def aplicar_decisao(numero_remessa: str, acao: str, observacao: str = "") -> dict:
    """Aplica a decisão do usuário e registra no histórico."""
    if acao not in MAPA_ACAO_STATUS:
        return {"ok": False, "motivo": f"Ação inválida: {acao}"}

    atual = db.buscar_por_remessa(numero_remessa)
    if not atual:
        return {"ok": False, "motivo": f"Remessa {numero_remessa} não encontrada"}

    status_anterior = atual.get("status")
    status_novo = MAPA_ACAO_STATUS[acao]
    agora = datetime.now().isoformat(timespec="seconds")

    with db.cursor() as cur:
        cur.execute(
            """UPDATE reembolsos
               SET status=?, data_atualizacao=?
               WHERE numero_remessa=?""",
            (status_novo, agora, numero_remessa),
        )
        cur.execute(
            """INSERT INTO historico_conciliacao
               (numero_remessa, status_anterior, status_novo,
                score_anterior, score_novo, camada,
                valor_quitado, diferenca, motivo, criado_em)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                numero_remessa,
                status_anterior,
                status_novo,
                atual.get("score") or 0,
                atual.get("score") or 0,
                "DECISAO_MANUAL",
                atual.get("valor_reembolso_cliente"),
                0.0,
                observacao or f"Decisão manual: {acao}",
                agora,
            ),
        )

    log.info(
        "Decisão manual em %s: %s -> %s", numero_remessa, status_anterior, status_novo
    )

    return {
        "ok": True,
        "remessa": numero_remessa,
        "status_anterior": status_anterior,
        "status_novo": status_novo,
    }

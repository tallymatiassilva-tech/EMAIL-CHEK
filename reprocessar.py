"""Reprocessa remessas específicas sem rodar a pasta inteira.

Útil quando você corrigiu um layout, resolveu um item da fila ou
ajustou um dado manualmente e precisa que o motor recalcule.
"""

from pathlib import Path

import database as db
import persistence as ps
from config import PASTA_ANEXOS_PADRAO, Status
from log_manager import get_logger
from pipeline import processar_remessa

log = get_logger("reprocessar")

# Status que indicam remessa já processada e que pode ser recalculada
STATUS_REPROCESSAVEIS = {
    Status.REVISAO,
    Status.DIV_VALOR,
    Status.DIV_CNPJ,
    Status.DIV_NF,
    Status.DIV_DATA,
    Status.AGRUPADO,
    Status.AGUARDANDO,
}


def listar_reprocessaveis() -> list[dict]:
    """Retorna as remessas que estão em status que vale a pena reprocessar."""
    registros = db.listar()
    return [r for r in registros if r.get("status") in STATUS_REPROCESSAVEIS]


def _pasta_da_remessa(numero_remessa: str) -> Path | None:
    """Localiza a pasta da remessa dentro da pasta raiz de anexos."""
    raiz = Path(PASTA_ANEXOS_PADRAO)
    if not raiz.is_dir():
        return None

    for sub in raiz.iterdir():
        if sub.is_dir() and sub.name == numero_remessa:
            return sub
    return None


def reprocessar_remessa(numero_remessa: str) -> dict:
    """Reprocessa uma remessa específica."""
    pasta = _pasta_da_remessa(numero_remessa)

    if not pasta:
        return {
            "ok": False,
            "remessa": numero_remessa,
            "motivo": f"Pasta da remessa não encontrada em {PASTA_ANEXOS_PADRAO}",
        }

    log.info("Reprocessando remessa %s", numero_remessa)
    return processar_remessa(pasta, numero_remessa)


def reprocessar_lote(numeros: list[str]) -> dict:
    """Reprocessa várias remessas e devolve o consolidado."""
    resultados = []

    for numero in numeros:
        try:
            resultados.append(reprocessar_remessa(numero))
        except Exception as e:
            log.error("Erro ao reprocessar %s: %s", numero, e)
            resultados.append({"ok": False, "remessa": numero, "motivo": str(e)})

    ok = sum(1 for r in resultados if r.get("ok"))
    return {
        "ok": True,
        "total": len(resultados),
        "processadas": ok,
        "falhas": len(resultados) - ok,
        "resultados": resultados,
    }

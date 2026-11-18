"""Dashboard de acompanhamento no terminal.

Mostra o panorama atual: quantos reembolsos em cada status, quais estão
vencendo, quais precisam de revisão e o tempo médio de reembolso.

Divergências incluem tanto os status críticos quanto qualquer remessa com
CNPJ divergente entre guia e comprovante, mesmo que o status final tenha
sido outro.
"""

from datetime import datetime, timedelta

import database as db
import persistence as ps
from config import Status
from log_manager import get_logger

log = get_logger("dashboard")

STATUS_CRITICOS = {
    Status.DIV_VALOR,
    Status.DIV_CNPJ,
    Status.DIV_NF,
    Status.DIV_DATA,
    Status.REVISAO,
    Status.AGRUPADO,
}


def _linha(char="─", n=70):
    print(char * n)


def _titulo(texto):
    print()
    _linha("═")
    print(f"  {texto}")
    _linha("═")


def _barra(qtd: int, total: int, largura: int = 30) -> str:
    """Gera uma barra proporcional em texto."""
    if total == 0:
        return ""
    return "█" * int(round((qtd / total) * largura))


def _e_divergente_cnpj(registro: dict) -> bool:
    """Remessa com CNPJ divergente entre guia e comprovante."""
    return registro.get("cnpj_divergente") in (True, 1, "True")


def _cnpj_nao_lido(registro: dict) -> bool:
    """Remessa onde o CNPJ nao foi extraido em um dos documentos."""
    valor = registro.get("cnpj_divergente")
    return valor is None or valor == ""


def mostrar_dashboard():
    """Exibe o dashboard completo no terminal."""
    registros = db.listar()
    if not registros:
        _titulo("DASHBOARD")
        print("\n  Nenhum reembolso no banco ainda.")
        return

    total = len(registros)

    # ------------------------------------------------------------------
    # 1. PANORAMA POR STATUS
    # ------------------------------------------------------------------
    _titulo("PANORAMA POR STATUS")

    por_status = ps.estatisticas_por_status()
    print()
    for status, qtd in sorted(por_status.items(), key=lambda x: -x[1]):
        pct = (qtd / total) * 100
        print(f"  {status:<28} {qtd:>4}  {pct:>5.1f}%  {_barra(qtd, total)}")

    # ------------------------------------------------------------------
    # 2. INDICADORES
    # ------------------------------------------------------------------
    _titulo("INDICADORES")

    conferidos = por_status.get(Status.CONFERIDO, 0)
    criticos = sum(por_status.get(s, 0) for s in STATUS_CRITICOS)
    aguardando = por_status.get(Status.AGUARDANDO, 0)

    div_cnpj = sum(1 for r in registros if _e_divergente_cnpj(r))
    nao_lido = sum(1 for r in registros if _cnpj_nao_lido(r))

    print(f"\n  Total de reembolsos:       {total}")
    print(
        f"  Conferidos:                {conferidos} ({conferidos / total * 100:.1f}%)"
    )
    print(f"  Precisam de revisão:       {criticos} ({criticos / total * 100:.1f}%)")
    print(f"  Aguardando comprovante:    {aguardando}")
    print(f"  CNPJ divergente:           {div_cnpj}")
    print(f"  CNPJ não lido:             {nao_lido}")

    dias = [r["dias_reembolso"] for r in registros if r.get("dias_reembolso")]
    if dias:
        print(f"  Tempo médio de reembolso:  {sum(dias) / len(dias):.1f} dias")

    # ------------------------------------------------------------------
    # 3. VENCIMENTOS PRÓXIMOS
    # ------------------------------------------------------------------
    _titulo("VENCIMENTOS NOS PRÓXIMOS 7 DIAS")

    hoje = datetime.now().date()
    limite = hoje + timedelta(days=7)

    vencendo = []
    for r in registros:
        dv = r.get("data_vencimento")
        if not dv:
            continue
        try:
            data = datetime.fromisoformat(str(dv)[:10]).date()
        except (ValueError, TypeError):
            continue
        if hoje <= data <= limite:
            vencendo.append((data, r))

    if not vencendo:
        print("\n  Nenhum vencimento nos próximos 7 dias.")
    else:
        vencendo.sort(key=lambda x: x[0])
        for data, r in vencendo:
            dias_restantes = (data - hoje).days
            alerta = "!" if dias_restantes <= 2 else " "
            print(
                f"  {alerta} {data.strftime('%d/%m')} ({dias_restantes}d) — "
                f"{r['numero_remessa']} — {r.get('cliente') or 'sem cliente'} — "
                f"R$ {r.get('valor_guia') or 0:.2f}"
            )

    # ------------------------------------------------------------------
    # 4. DIVERGÊNCIAS PARA REVISÃO
    # Inclui os status críticos E qualquer remessa com CNPJ divergente,
    # mesmo que o status final tenha sido outro.
    # ------------------------------------------------------------------
    _titulo("DIVERGÊNCIAS PARA REVISÃO")

    divergentes = [
        r
        for r in registros
        if r.get("status") in STATUS_CRITICOS or _e_divergente_cnpj(r)
    ]
    if not divergentes:
        print("\n  Nenhuma divergência pendente.")
    else:
        divergentes.sort(key=lambda r: r.get("valor_guia") or 0, reverse=True)
        print()
        for r in divergentes[:15]:
            marca = "CNPJ" if _e_divergente_cnpj(r) else ""
            print(
                f"  {r['numero_remessa']:<18} {r['status']:<20} "
                f"R$ {r.get('valor_guia') or 0:>10.2f}  "
                f"{(r.get('cliente') or 'sem cliente'):<28} {marca}"
            )
        if len(divergentes) > 15:
            print(f"  ... e mais {len(divergentes) - 15} divergências")

    # ------------------------------------------------------------------
    # 5. ÚLTIMAS MOVIMENTAÇÕES
    # ------------------------------------------------------------------
    _titulo("ÚLTIMAS 10 MOVIMENTAÇÕES")

    print()
    for h in ps.historico_recente(10):
        print(
            f"  {h['criado_em'][:16]} | {h['numero_remessa']:<16} | "
            f"{h['status_anterior'] or '—':<22} → {h['status_novo']}"
        )

    print()

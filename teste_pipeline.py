"""Teste do pipeline em todas as remessas."""

from pathlib import Path

import database as db
from pipeline import processar_pasta_raiz

db.init_db()

raiz = Path("data/Reembolsos")
r = processar_pasta_raiz(raiz)

print()
print("=" * 70)
print("RESUMO")
print("=" * 70)
print("Total:", r["total"], "| OK:", r["processadas"], "| Falhas:", r["falhas"])
print()

for x in r["resultados"]:
    remessa = x["remessa"]
    status = x.get("status") or x.get("motivo") or "-"
    score = x.get("score")
    score_txt = f"{score:.0f}" if isinstance(score, (int, float)) else "-"
    ignorados = len(x.get("ignorados", []))
    desconhecidos = x.get("desconhecidos", [])
    print(
        f"{remessa:<20} | {status:<18} | score {score_txt:<4} "
        f"| ignorados: {ignorados} | desconhecidos: {desconhecidos}"
    )

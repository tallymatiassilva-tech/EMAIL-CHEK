"""Testa o pipeline numa remessa especifica (pasta indicada)."""

import sys
from pathlib import Path

import database as db
from pipeline import processar_remessa

if len(sys.argv) < 2:
    print("Uso: python teste_remessa.py <numero_remessa>")
    print("Exemplo: python teste_remessa.py 10084206883004")
    raise SystemExit(1)

REMESSA = sys.argv[1]
PASTA = Path("data/Reembolsos") / REMESSA

if not PASTA.is_dir():
    print(f"Pasta nao encontrada: {PASTA}")
    print()
    print("Pastas disponiveis:")
    raiz = Path("data/Reembolsos")
    if raiz.is_dir():
        for p in sorted(x for x in raiz.iterdir() if x.is_dir()):
            print(f"  {p.name}")
    raise SystemExit(1)

db.init_db()

print(f"Processando: {PASTA}")
print()

r = processar_remessa(PASTA, REMESSA)

print()
print("=" * 70)
print(f"REMESSA {REMESSA}")
print("=" * 70)
print("OK:            ", r.get("ok"))
print("Status:        ", r.get("status") or r.get("motivo"))
print("Score:         ", r.get("score"))
print("Guia:          ", r.get("guia"))
print("Comp Jadlog:   ", r.get("comp_jadlog"))
print("Comp Cliente:  ", r.get("comp_cliente"))
print("Ignorados:     ", r.get("ignorados"))
print("Desconhecidos: ", r.get("desconhecidos"))

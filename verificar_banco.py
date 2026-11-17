"""Mostra as tabelas e colunas do banco."""

import sqlite3

c = sqlite3.connect("data/reembolsos.db")

print("TABELAS:")
for (nome,) in c.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
):
    print(f"  - {nome}")

print()
print("COLUNAS POR TABELA:")
for (nome,) in c.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
):
    cols = [r[1] for r in c.execute(f"PRAGMA table_info({nome})")]
    print(f"  {nome}: {cols}")

c.close()

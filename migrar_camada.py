import sqlite3

c = sqlite3.connect("data/reembolsos.db")

print("=== ANTES ===")
for r in c.execute(
    "SELECT camada, COUNT(*) FROM historico_conciliacao GROUP BY camada ORDER BY 2 DESC"
):
    print(f"  {str(r[0]):<25} {r[1]}")

# Reclassifica: conciliações cujo status é AGUARDANDO e a camada ainda é REVISAO_MANUAL
cur = c.execute(
    """
    UPDATE historico_conciliacao
       SET camada = 'AGUARDANDO_DOCUMENTO'
     WHERE camada = 'REVISAO_MANUAL'
       AND status_novo = 'AGUARDANDO_REEMBOLSO'
    """
)
print(f"\nLinhas atualizadas: {cur.rowcount}")
c.commit()

print("\n=== DEPOIS ===")
for r in c.execute(
    "SELECT camada, COUNT(*) FROM historico_conciliacao GROUP BY camada ORDER BY 2 DESC"
):
    print(f"  {str(r[0]):<25} {r[1]}")

c.close()
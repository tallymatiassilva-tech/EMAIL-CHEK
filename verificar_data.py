import sqlite3
from config import DB_PATH

con = sqlite3.connect(DB_PATH)
con.row_factory = sqlite3.Row

print("=" * 70)
print("VERIFICACAO DO data_atualizacao")
print("=" * 70)

# 1. Quantos registros tem data_atualizacao preenchida
total = con.execute("SELECT COUNT(*) AS n FROM reembolsos").fetchone()["n"]
com_data = con.execute(
    "SELECT COUNT(*) AS n FROM reembolsos WHERE data_atualizacao IS NOT NULL"
).fetchone()["n"]
print(f"\n[1] Total de reembolsos: {total}")
print(f"    Com data_atualizacao: {com_data}")
print(f"    Sem data_atualizacao: {total - com_data}")

# 2. Datas mais recentes
print("\n[2] Ultimas 10 atualizacoes")
rows = con.execute(
    "SELECT numero_remessa, data_importacao, data_atualizacao "
    "FROM reembolsos ORDER BY data_atualizacao DESC LIMIT 10"
).fetchall()
for r in rows:
    print(f"    {r['numero_remessa']}: import={r['data_importacao']} "
          f"atual={r['data_atualizacao']}")

# 3. Registros onde data_atualizacao == data_importacao
#    (sinal de que nunca foi atualizado depois da criacao)
iguais = con.execute(
    "SELECT COUNT(*) AS n FROM reembolsos "
    "WHERE data_atualizacao = data_importacao"
).fetchone()["n"]
print(f"\n[3] Registros onde atualizacao == importacao: {iguais}")
if iguais > 0:
    print("    (esses nunca foram reprocessados ou o UPDATE nao mexe na data)")

# 4. Remessa especifica
print("\n[4] Remessa 18109909021256")
r = con.execute(
    "SELECT numero_remessa, status, data_importacao, data_atualizacao "
    "FROM reembolsos WHERE numero_remessa = '18109909021256'"
).fetchone()
if r:
    print(f"    status: {r['status']}")
    print(f"    importacao: {r['data_importacao']}")
    print(f"    atualizacao: {r['data_atualizacao']}")
else:
    print("    nao encontrada")

con.close()
print("\n" + "=" * 70)

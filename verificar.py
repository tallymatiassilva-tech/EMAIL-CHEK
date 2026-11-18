import sqlite3

from config import DB_PATH

con = sqlite3.connect(DB_PATH)
cols = [r[1] for r in con.execute("PRAGMA table_info(reembolsos)")]
novas = ["tomador", "cnpj_guia", "cnpj_comprovante", "cnpj_divergente", "motivo_cnpj"]
print("Encontradas:", [c for c in cols if c in novas])
print("Faltando:", [c for c in novas if c not in cols])
con.close()

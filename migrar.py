import sqlite3

from config import DB_PATH

con = sqlite3.connect(DB_PATH)
cur = con.cursor()

for col, tipo in [
    ("tomador", "TEXT"),
    ("cnpj_guia", "TEXT"),
    ("cnpj_comprovante", "TEXT"),
    ("cnpj_divergente", "INTEGER"),
    ("motivo_cnpj", "TEXT"),
]:
    try:
        cur.execute(f"ALTER TABLE reembolsos ADD COLUMN {col} {tipo}")
        print(f"Criada: {col}")
    except sqlite3.OperationalError:
        print(f"Ja existe: {col}")

con.commit()
con.close()

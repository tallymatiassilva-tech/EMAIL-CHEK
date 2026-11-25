import sqlite3

from config import DB_PATH

con = sqlite3.connect(DB_PATH)
cur = con.cursor()
cur.execute(
    "UPDATE fila_treinamento SET resolvido = 1 WHERE numero_remessa = ?",
    ("18264500050990",),
)
con.commit()
print(f"Linhas atualizadas: {cur.rowcount}")
con.close()

from config import PASTA_ANEXOS_PADRAO
from pathlib import Path
import sqlite3
from config import DB_PATH

print("=== PASTA DESTINO ===")
p = Path(PASTA_ANEXOS_PADRAO)
print("Caminho:", p)
print("Existe:", p.exists())
if p.exists():
    subpastas = [d.name for d in p.iterdir() if d.is_dir()]
    print("Subpastas:", len(subpastas))
    print("Primeiras 10:", subpastas[:10])

print()
print("=== E-MAILS JA REGISTRADOS ===")
con = sqlite3.connect(DB_PATH)
try:
    n = con.execute("SELECT COUNT(*) FROM email_processado").fetchone()[0]
    print("Total registrado:", n)
    ultimos = con.execute(
        "SELECT entry_id, numero_remessa, assunto FROM email_processado ORDER BY id DESC LIMIT 5"
    ).fetchall()
    for r in ultimos:
        print(" ", r)
except Exception as e:
    print("Erro:", e)
finally:
    con.close()

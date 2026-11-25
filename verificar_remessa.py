import sqlite3
from pathlib import Path
from config import DB_PATH, PASTA_ANEXOS_PADRAO

REMESSA = "18109909021256"

print("=" * 60)
print(f"VERIFICANDO REMESSA {REMESSA}")
print("=" * 60)

# ---- 1. Banco: tabela reembolsos
con = sqlite3.connect(DB_PATH)
con.row_factory = sqlite3.Row

print("\n[1] Tabela reembolsos")
r = con.execute(
    "SELECT id, numero_remessa, numero_nf, status, valor_guia, "
    "valor_pago_jadlog, valor_reembolso_cliente, data_atualizacao "
    "FROM reembolsos WHERE numero_remessa = ?", (REMESSA,)
).fetchone()
if r:
    for k in r.keys():
        print(f"    {k}: {r[k]}")
else:
    print("    NAO ENCONTRADO")

# ---- 2. Banco: tabela email_processado
print("\n[2] Tabela email_processado")
rows = con.execute(
    "SELECT entry_id, assunto, data_email FROM email_processado "
    "WHERE numero_remessa = ?", (REMESSA,)
).fetchall()
if rows:
    for r in rows:
        print(f"    assunto: {r['assunto']}")
        print(f"    data:    {r['data_email']}")
        print(f"    entry:   {r['entry_id'][:40]}...")
else:
    print("    Nenhum e-mail registrado")

# ---- 3. Banco: tabela fila_treinamento
print("\n[3] Tabela fila_treinamento")
rows = con.execute(
    "SELECT id, tipo_documento, caminho_arquivo, confianca, resolvido "
    "FROM fila_treinamento WHERE numero_remessa = ?", (REMESSA,)
).fetchall()
if rows:
    for r in rows:
        print(f"    #{r['id']} {r['tipo_documento']} conf={r['confianca']} "
              f"resolvido={r['resolvido']}")
        print(f"       {r['caminho_arquivo']}")
else:
    print("    Nenhum item na fila")

con.close()

# ---- 4. Pasta
print("\n[4] Pasta de anexos")
pasta = Path(PASTA_ANEXOS_PADRAO) / REMESSA
print(f"    Caminho: {pasta}")
print(f"    Existe:  {pasta.exists()}")
if pasta.exists():
    arquivos = list(pasta.iterdir())
    print(f"    Arquivos: {len(arquivos)}")
    for a in arquivos:
        print(f"       {a.name} ({a.stat().st_size} bytes)")

# ---- 5. Busca por nome parecido (caso a pasta tenha outro nome)
print("\n[5] Pastas com nome parecido")
raiz = Path(PASTA_ANEXOS_PADRAO)
if raiz.exists():
    parecidas = [d.name for d in raiz.iterdir()
                 if d.is_dir() and REMESSA[:8] in d.name]
    if parecidas:
        for p in parecidas[:10]:
            print(f"    {p}")
    else:
        print("    Nenhuma")

print("\n" + "=" * 60)

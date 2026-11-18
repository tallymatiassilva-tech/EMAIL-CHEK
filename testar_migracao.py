"""Simula um banco antigo e mostra a migracao em acao.

Cria um SQLite temporario com a tabela reembolsos na versao 1 (sem as
colunas de CNPJ), roda o init_db() e imprime o log da migracao.
Tambem verifica que os indices pos-migracao foram criados.
"""

import sqlite3
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# 1. Cria um banco "antigo" num arquivo temporario
# ---------------------------------------------------------------------------

SCHEMA_ANTIGO = """
CREATE TABLE reembolsos (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    numero_remessa              TEXT NOT NULL UNIQUE,
    numero_nf                   TEXT,
    numero_guia                 TEXT,
    cliente                     TEXT,
    cnpj_cliente                TEXT,
    uf                          TEXT,
    tributo                     TEXT,
    valor_guia                  REAL,
    data_emissao                TEXT,
    data_vencimento             TEXT,
    valor_pago_jadlog           REAL,
    data_pagamento_jadlog       TEXT,
    valor_reembolso_cliente     REAL,
    data_reembolso_cliente      TEXT,
    dias_reembolso              INTEGER,
    status                      TEXT NOT NULL DEFAULT 'AGUARDANDO_REEMBOLSO',
    score                       INTEGER DEFAULT 0,
    caminho_guia                TEXT,
    caminho_comp_jadlog         TEXT,
    caminho_comp_cliente        TEXT,
    data_importacao             TEXT NOT NULL,
    data_atualizacao            TEXT
);
"""

COLUNAS_NOVAS = [
    "tomador",
    "cnpj_guia",
    "cnpj_comprovante",
    "cnpj_divergente",
    "motivo_cnpj",
]

INDICES_NOVOS = ["idx_rem_tomador", "idx_rem_div_cnpj"]


def criar_banco_antigo(caminho: Path) -> None:
    con = sqlite3.connect(caminho)
    con.executescript(SCHEMA_ANTIGO)
    con.commit()
    con.close()
    print(f"[setup] Banco antigo criado em {caminho}")
    print("[setup] Tabela reembolsos SEM tomador, cnpj_guia, cnpj_comprovante,")
    print("        cnpj_divergente e motivo_cnpj")
    print("[setup] Sem os indices idx_rem_tomador e idx_rem_div_cnpj")


def colunas(caminho: Path) -> list[str]:
    con = sqlite3.connect(caminho)
    cols = [r[1] for r in con.execute("PRAGMA table_info(reembolsos)")]
    con.close()
    return cols


def indices(caminho: Path) -> list[str]:
    con = sqlite3.connect(caminho)
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND tbl_name='reembolsos' ORDER BY name"
    ).fetchall()
    con.close()
    return [r[0] for r in rows]


def versao(caminho: Path) -> int:
    con = sqlite3.connect(caminho)
    try:
        row = con.execute("SELECT MAX(versao) FROM schema_version").fetchone()
        return row[0] if row and row[0] is not None else 0
    except sqlite3.OperationalError:
        return 0
    finally:
        con.close()


def main() -> int:
    tmp = Path(tempfile.mkdtemp()) / "banco_antigo.db"
    criar_banco_antigo(tmp)

    # -----------------------------------------------------------------------
    # 2. Aponta o config para o banco temporario ANTES de importar database
    # -----------------------------------------------------------------------
    import config

    config.DB_PATH = str(tmp)

    import database as db

    # a conexao e por thread; zera para pegar o caminho novo
    db._local.conn = None

    print()
    print("=" * 70)
    print("ANTES DA MIGRACAO")
    print("=" * 70)
    antes_cols = colunas(tmp)
    antes_idx = indices(tmp)
    print(f"Versao do schema: {versao(tmp)}")
    print(f"Colunas novas presentes: {[c for c in COLUNAS_NOVAS if c in antes_cols]}")
    print(
        f"Colunas novas faltando:  {[c for c in COLUNAS_NOVAS if c not in antes_cols]}"
    )
    print(f"Indices novos presentes: {[i for i in INDICES_NOVOS if i in antes_idx]}")
    print(
        f"Indices novos faltando:  {[i for i in INDICES_NOVOS if i not in antes_idx]}"
    )

    # -----------------------------------------------------------------------
    # 3. Roda a migracao
    # -----------------------------------------------------------------------
    print()
    print("=" * 70)
    print("RODANDO init_db() — LOG DA MIGRACAO")
    print("=" * 70)
    db.init_db()

    # -----------------------------------------------------------------------
    # 4. Confere o resultado
    # -----------------------------------------------------------------------
    print()
    print("=" * 70)
    print("DEPOIS DA MIGRACAO")
    print("=" * 70)
    depois_cols = colunas(tmp)
    depois_idx = indices(tmp)
    print(f"Versao do schema: {versao(tmp)}")
    print(f"Colunas novas presentes: {[c for c in COLUNAS_NOVAS if c in depois_cols]}")
    print(
        f"Colunas novas faltando:  {[c for c in COLUNAS_NOVAS if c not in depois_cols]}"
    )
    print(f"Indices novos presentes: {[i for i in INDICES_NOVOS if i in depois_idx]}")
    print(
        f"Indices novos faltando:  {[i for i in INDICES_NOVOS if i not in depois_idx]}"
    )

    # -----------------------------------------------------------------------
    # 5. Roda de novo para provar que e idempotente
    # -----------------------------------------------------------------------
    print()
    print("=" * 70)
    print("SEGUNDA EXECUCAO (deve dizer que nao ha nada pendente)")
    print("=" * 70)
    db.init_db()

    # -----------------------------------------------------------------------
    # 6. Resultado final
    # -----------------------------------------------------------------------
    faltando_cols = [c for c in COLUNAS_NOVAS if c not in depois_cols]
    faltando_idx = [i for i in INDICES_NOVOS if i not in depois_idx]

    print()
    if faltando_cols or faltando_idx:
        if faltando_cols:
            print(f"FALHOU — colunas faltando: {faltando_cols}")
        if faltando_idx:
            print(f"FALHOU — indices faltando: {faltando_idx}")
        return 1

    print("OK — migracao aplicada, indices criados e idempotente")
    print(f"Banco de teste: {tmp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

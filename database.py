"""Camada de acesso ao SQLite. Thread-safe via conexão por thread."""

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

from config import DB_PATH
from log_manager import get_logger

log = get_logger("database")

_local = threading.local()

# Versão atual do schema. Incremente a cada migração nova.
VERSAO_SCHEMA = 3

# Tabelas e índices que NÃO dependem de colunas migradas.
# Índices sobre colunas novas ficam em _criar_indices, que roda depois
# do _migrar_colunas — senão o CREATE INDEX quebra em bancos antigos.
SCHEMA = """
CREATE TABLE IF NOT EXISTS reembolsos (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    numero_remessa              TEXT NOT NULL UNIQUE,
    numero_nf                   TEXT,
    tomador                     TEXT,
    numero_guia                 TEXT,
    cliente                     TEXT,
    cnpj_cliente                TEXT,
    cnpj_guia                   TEXT,
    cnpj_comprovante            TEXT,
    cnpj_divergente             INTEGER,
    motivo_cnpj                 TEXT,
    uf                          TEXT,
    tributo                     TEXT,
    valor_guia                  REAL,
    data_emissao                TEXT,
    data_vencimento             TEXT,
    valor_pago_jadlog           REAL,
    data_pagamento_jadlog       TEXT,
    valor_reembolso_cliente     REAL,
    data_reembolso_cliente      TEXT,
    valor_reembolso_esperado    REAL,
    dias_reembolso              INTEGER,
    status                      TEXT NOT NULL DEFAULT 'AGUARDANDO_REEMBOLSO',
    score                       INTEGER DEFAULT 0,
    caminho_guia                TEXT,
    caminho_comp_jadlog         TEXT,
    caminho_comp_cliente        TEXT,
    data_importacao             TEXT NOT NULL,
    data_atualizacao            TEXT
);

CREATE INDEX IF NOT EXISTS idx_rem_status   ON reembolsos(status);
CREATE INDEX IF NOT EXISTS idx_rem_cnpj     ON reembolsos(cnpj_cliente);
CREATE INDEX IF NOT EXISTS idx_rem_cliente  ON reembolsos(cliente);
CREATE INDEX IF NOT EXISTS idx_rem_nf       ON reembolsos(numero_nf);
CREATE INDEX IF NOT EXISTS idx_rem_uf       ON reembolsos(uf);
CREATE INDEX IF NOT EXISTS idx_rem_data_pg  ON reembolsos(data_pagamento_jadlog);
CREATE INDEX IF NOT EXISTS idx_rem_pend_cnpj ON reembolsos(cnpj_cliente, status);

CREATE TABLE IF NOT EXISTS layout_treinado (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo_documento      TEXT NOT NULL,
    nome_layout         TEXT NOT NULL,
    hash_layout         TEXT NOT NULL,
    campos_json         TEXT NOT NULL,
    taxa_acerto         REAL DEFAULT 0,
    confirmacoes        INTEGER DEFAULT 0,
    data_criacao        TEXT NOT NULL,
    ultima_utilizacao   TEXT,
    UNIQUE(tipo_documento, hash_layout)
);
CREATE INDEX IF NOT EXISTS idx_layout_tipo ON layout_treinado(tipo_documento);

CREATE TABLE IF NOT EXISTS fila_treinamento (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo_documento  TEXT NOT NULL,
    caminho_arquivo TEXT NOT NULL,
    numero_remessa  TEXT,
    confianca       REAL,
    campos_json     TEXT,
    criado_em       TEXT NOT NULL,
    resolvido       INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS email_processado (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id        TEXT NOT NULL UNIQUE,
    numero_remessa  TEXT,
    assunto         TEXT,
    remetente       TEXT,
    data_email      TEXT,
    ordem           INTEGER,
    processado_em   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_email_remessa ON email_processado(numero_remessa);

CREATE TABLE IF NOT EXISTS historico_conciliacao (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    numero_remessa    TEXT NOT NULL,
    status_anterior   TEXT,
    status_novo       TEXT NOT NULL,
    score_anterior    INTEGER,
    score_novo        INTEGER,
    camada            TEXT,
    valor_quitado     REAL,
    diferenca         REAL,
    motivo            TEXT,
    criado_em         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hist_remessa ON historico_conciliacao(numero_remessa);
CREATE INDEX IF NOT EXISTS idx_hist_data    ON historico_conciliacao(criado_em);

CREATE TABLE IF NOT EXISTS schema_version (
    versao      INTEGER NOT NULL,
    aplicada_em TEXT NOT NULL,
    descricao   TEXT
);
"""

# Migrações por versão. Cada entrada lista as colunas que aquela versão
# introduziu. O _migrar_colunas aplica só o que ainda falta.
MIGRACOES = {
    1: {
        "descricao": "valor_reembolso_esperado",
        "colunas": {"valor_reembolso_esperado": "REAL"},
    },
    2: {
        "descricao": "CNPJ guia x comprovante + tomador",
        "colunas": {
            "tomador": "TEXT",
            "cnpj_guia": "TEXT",
            "cnpj_comprovante": "TEXT",
            "cnpj_divergente": "INTEGER",
            "motivo_cnpj": "TEXT",
        },
    },
    3: {
        "descricao": "indices de tomador e divergencia",
        "colunas": {},
    },
}

# Índices que dependem de colunas migradas. Criados depois da migração.
INDICES_POS_MIGRACAO = [
    "CREATE INDEX IF NOT EXISTS idx_rem_tomador ON reembolsos(tomador)",
    "CREATE INDEX IF NOT EXISTS idx_rem_div_cnpj ON reembolsos(cnpj_divergente)",
]


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        _local.conn = conn
    return conn


@contextmanager
def cursor():
    conn = get_conn()
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def versao_atual(conn: sqlite3.Connection) -> int:
    """Le a ultima versao registrada. Retorna 0 quando nunca migrou."""
    try:
        row = conn.execute("SELECT MAX(versao) AS v FROM schema_version").fetchone()
        return int(row["v"]) if row and row["v"] is not None else 0
    except sqlite3.OperationalError:
        return 0


def _registrar_versao(conn: sqlite3.Connection, versao: int, descricao: str) -> None:
    conn.execute(
        "INSERT INTO schema_version (versao, aplicada_em, descricao) VALUES (?, ?, ?)",
        (versao, datetime.now().isoformat(timespec="seconds"), descricao),
    )


def _migrar_colunas(conn: sqlite3.Connection) -> None:
    """Aplica as migracoes pendentes e registra cada versao.

    O CREATE TABLE IF NOT EXISTS nao altera tabelas existentes, entao
    colunas adicionadas depois da criacao inicial precisam de ALTER TABLE.
    Cada versao aplicada fica gravada em schema_version.
    """
    existentes = {
        r["name"] for r in conn.execute("PRAGMA table_info(reembolsos)").fetchall()
    }
    atual = versao_atual(conn)

    if atual >= VERSAO_SCHEMA:
        log.debug("Schema na versao %d (atual) — nenhuma migracao pendente", atual)
        return

    log.info("Migrando schema: versao %d -> %d", atual, VERSAO_SCHEMA)

    for versao in sorted(MIGRACOES):
        if versao <= atual:
            continue

        migracao = MIGRACOES[versao]
        criadas = []

        for coluna, tipo in migracao.get("colunas", {}).items():
            if coluna not in existentes:
                conn.execute(f"ALTER TABLE reembolsos ADD COLUMN {coluna} {tipo}")
                existentes.add(coluna)
                criadas.append(f"{coluna} ({tipo})")

        _registrar_versao(conn, versao, migracao["descricao"])

        if criadas:
            log.info(
                "v%d aplicada — colunas criadas: %s",
                versao,
                ", ".join(criadas),
            )
        else:
            log.info(
                "v%d aplicada — %s (nenhuma coluna nova)",
                versao,
                migracao["descricao"],
            )

    log.info("Schema atualizado para a versao %d", VERSAO_SCHEMA)


def _criar_indices(conn: sqlite3.Connection) -> None:
    """Cria os indices que dependem de colunas migradas.

    Roda DEPOIS do _migrar_colunas: um indice sobre coluna inexistente
    quebra o executescript antes da migracao ter chance de rodar.
    """
    criados = []
    for sql in INDICES_POS_MIGRACAO:
        try:
            conn.execute(sql)
            criados.append(sql.split("idx_rem_")[1].split(" ")[0])
        except sqlite3.OperationalError as e:
            log.warning("Falha ao criar indice: %s", e)

    if criados:
        log.debug("Indices garantidos: %s", ", ".join(criados))


def init_db():
    """Inicializa o banco na ordem correta.

    1. Tabelas e indices que nao dependem de colunas novas
    2. Colunas migradas (ALTER TABLE)
    3. Indices sobre as colunas migradas
    """
    conn = get_conn()
    conn.executescript(SCHEMA)
    _migrar_colunas(conn)
    _criar_indices(conn)
    conn.commit()


def upsert_reembolso(dados: dict) -> int:
    """Insere ou atualiza por numero_remessa. Retorna o id."""
    agora = datetime.now().isoformat(timespec="seconds")
    dados.setdefault("data_importacao", agora)
    dados["data_atualizacao"] = agora

    campos = list(dados.keys())
    placeholders = ",".join("?" for _ in campos)
    updates = ",".join(f"{c}=excluded.{c}" for c in campos if c != "numero_remessa")

    sql = (
        f"INSERT INTO reembolsos ({','.join(campos)}) VALUES ({placeholders}) "
        f"ON CONFLICT(numero_remessa) DO UPDATE SET {updates}"
    )
    with cursor() as cur:
        cur.execute(sql, [dados[c] for c in campos])
        cur.execute(
            "SELECT id FROM reembolsos WHERE numero_remessa=?",
            (dados["numero_remessa"],),
        )
        return cur.fetchone()["id"]


def buscar_por_remessa(numero_remessa: str):
    with cursor() as cur:
        cur.execute(
            "SELECT * FROM reembolsos WHERE numero_remessa=?", (numero_remessa,)
        )
        row = cur.fetchone()
        return dict(row) if row else None


def listar_pendentes_por_cnpj(cnpj: str):
    with cursor() as cur:
        cur.execute(
            "SELECT * FROM reembolsos WHERE cnpj_cliente=? AND status=?",
            (cnpj, "AGUARDANDO_REEMBOLSO"),
        )
        return [dict(r) for r in cur.fetchall()]


def listar_divergencias_cnpj():
    """Remessas com CNPJ divergente entre guia e comprovante."""
    with cursor() as cur:
        cur.execute(
            "SELECT * FROM reembolsos WHERE cnpj_divergente = 1 "
            "ORDER BY data_atualizacao DESC, id DESC"
        )
        return [dict(r) for r in cur.fetchall()]


def listar_cnpj_nao_lido():
    """Remessas onde o CNPJ nao foi extraido em um dos documentos."""
    with cursor() as cur:
        cur.execute(
            "SELECT * FROM reembolsos WHERE cnpj_divergente IS NULL "
            "ORDER BY data_atualizacao DESC, id DESC"
        )
        return [dict(r) for r in cur.fetchall()]


def listar(filtros: dict | None = None):
    sql = "SELECT * FROM reembolsos WHERE 1=1"
    params = []
    filtros = filtros or {}

    if filtros.get("remessa"):
        sql += " AND numero_remessa LIKE ?"
        params.append(f"%{filtros['remessa']}%")
    if filtros.get("cliente"):
        sql += " AND cliente LIKE ?"
        params.append(f"%{filtros['cliente']}%")
    if filtros.get("tomador"):
        sql += " AND tomador LIKE ?"
        params.append(f"%{filtros['tomador']}%")
    if filtros.get("cnpj"):
        sql += " AND cnpj_cliente LIKE ?"
        params.append(f"%{filtros['cnpj']}%")
    if filtros.get("nf"):
        sql += " AND numero_nf LIKE ?"
        params.append(f"%{filtros['nf']}%")
    if filtros.get("status"):
        sql += " AND status = ?"
        params.append(filtros["status"])
    if filtros.get("uf"):
        sql += " AND uf = ?"
        params.append(filtros["uf"])
    if filtros.get("data_ini"):
        sql += " AND data_pagamento_jadlog >= ?"
        params.append(filtros["data_ini"])
    if filtros.get("data_fim"):
        sql += " AND data_pagamento_jadlog <= ?"
        params.append(filtros["data_fim"])
    if filtros.get("cnpj_divergente") is not None:
        sql += " AND cnpj_divergente = ?"
        params.append(1 if filtros["cnpj_divergente"] else 0)

    sql += " ORDER BY data_pagamento_jadlog DESC, id DESC"
    with cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def email_ja_processado(entry_id: str) -> bool:
    with cursor() as cur:
        cur.execute("SELECT 1 FROM email_processado WHERE entry_id=?", (entry_id,))
        return cur.fetchone() is not None


def registrar_email(entry_id, numero_remessa, assunto, remetente, data_email, ordem):
    with cursor() as cur:
        cur.execute(
            """INSERT OR IGNORE INTO email_processado
               (entry_id, numero_remessa, assunto, remetente, data_email, ordem, processado_em)
               VALUES (?,?,?,?,?,?,?)""",
            (
                entry_id,
                numero_remessa,
                assunto,
                remetente,
                data_email,
                ordem,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )


def contar_emails_remessa(numero_remessa: str) -> int:
    with cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) c FROM email_processado WHERE numero_remessa=?",
            (numero_remessa,),
        )
        return cur.fetchone()["c"]

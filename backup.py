"""Backup automático do banco SQLite.

Cria uma cópia datada antes de operações que alteram muitos registros.
Mantém as N cópias mais recentes e descarta as antigas.
"""

import shutil
from datetime import datetime
from pathlib import Path

import config
from log_manager import get_logger

log = get_logger("backup")

PASTA_BACKUP = config.DATA_DIR / "backups"
MAX_BACKUPS = 10


def _garantir_pasta() -> Path:
    """Cria a pasta de backups se não existir."""
    PASTA_BACKUP.mkdir(parents=True, exist_ok=True)
    return PASTA_BACKUP


def criar_backup(motivo: str = "manual") -> dict:
    """Cria uma cópia datada do banco.

    Retorna dict com ok, caminho e motivo.
    """
    origem = Path(config.DB_PATH)

    if not origem.is_file():
        return {"ok": False, "motivo": "Banco não encontrado"}

    pasta = _garantir_pasta()
    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
    nome = f"reembolsos_{carimbo}_{motivo}.db"
    destino = pasta / nome

    try:
        shutil.copy2(origem, destino)
        tamanho = destino.stat().st_size
        log.info("Backup criado: %s (%.1f KB)", nome, tamanho / 1024)
        _limpar_antigos()
        return {
            "ok": True,
            "caminho": str(destino),
            "nome": nome,
            "tamanho_kb": round(tamanho / 1024, 1),
        }
    except Exception as e:
        log.error("Falha no backup: %s", e)
        return {"ok": False, "motivo": str(e)}


def _limpar_antigos(manter: int = MAX_BACKUPS) -> int:
    """Remove os backups mais antigos, mantendo os N mais recentes."""
    backups = sorted(
        PASTA_BACKUP.glob("reembolsos_*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    removidos = 0
    for antigo in backups[manter:]:
        try:
            antigo.unlink()
            removidos += 1
        except Exception as e:
            log.warning("Não removi %s: %s", antigo.name, e)

    if removidos:
        log.info("Removidos %d backup(s) antigo(s)", removidos)

    return removidos


def listar_backups() -> list[dict]:
    """Lista os backups existentes, do mais novo para o mais antigo."""
    if not PASTA_BACKUP.is_dir():
        return []

    itens = []
    for arq in sorted(
        PASTA_BACKUP.glob("reembolsos_*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        st = arq.stat()
        itens.append(
            {
                "nome": arq.name,
                "caminho": str(arq),
                "tamanho_kb": round(st.st_size / 1024, 1),
                "modificado": datetime.fromtimestamp(st.st_mtime).strftime(
                    "%d/%m/%Y %H:%M"
                ),
            }
        )
    return itens

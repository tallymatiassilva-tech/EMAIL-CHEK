"""Leitura de e-mails do Outlook e download de anexos.

SOMENTE LEITURA: nunca move, marca, exclui ou altera nada no Outlook.
A pasta de origem e a pasta de destino dos anexos são definidas pelo usuário.
"""

import re
from datetime import datetime, timedelta
from pathlib import Path

import win32com.client
from config import (
    EMAIL_MAX_AGE_DAYS,
    PASTA_ANEXOS_PADRAO,
    PASTA_OUTLOOK_PADRAO,
    REGEX_REMESSA,
)
from database import email_ja_processado, registrar_email
from log_manager import get_logger

log = get_logger("outlook")

RE_REMESSA = re.compile(REGEX_REMESSA, re.IGNORECASE)


class OutlookIndisponivel(Exception):
    """Outlook não está aberto ou não pôde ser acessado."""


def _conectar():
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        return outlook.GetNamespace("MAPI")
    except Exception as e:
        raise OutlookIndisponivel(f"Não foi possível conectar ao Outlook: {e}") from e


# ---------------------------------------------------------------------------
# SELEÇÃO DE PASTA DE LEITURA
# ---------------------------------------------------------------------------


def _contar_itens(pasta) -> int:
    """Conta itens com fallback. Retorna -1 quando desconhecido."""
    try:
        n = pasta.Items.Count
        if isinstance(n, int):
            return n
    except Exception:
        pass
    try:
        n = pasta.Folder.Items.Count
        if isinstance(n, int):
            return n
    except Exception:
        pass
    return -1


def listar_pastas() -> list[dict]:
    """Lista TODAS as pastas de e-mail do Outlook, recursivamente (somente leitura).

    Retorna lista de dicts com: nome, caminho, total_itens, nivel.
    total_itens = -1 quando a contagem não pôde ser obtida.
    Nenhuma pasta é filtrada: o usuário decide qual ler.
    """
    ns = _conectar()
    pastas = []

    def _percorrer(pasta, caminho="", nivel=0):
        if nivel > 10:
            return
        try:
            nome = pasta.Name
            caminho_atual = f"{caminho}/{nome}" if caminho else nome
            pastas.append(
                {
                    "nome": nome,
                    "caminho": caminho_atual,
                    "total_itens": _contar_itens(pasta),
                    "nivel": nivel,
                }
            )
            for sub in pasta.Folders:
                _percorrer(sub, caminho_atual, nivel + 1)
        except Exception as e:
            log.debug("Falha ao percorrer pasta: %s", e)

    for store in ns.Folders:
        try:
            _percorrer(store)
        except Exception:
            continue

    log.info("Listadas %d pastas do Outlook", len(pastas))
    return pastas


def escolher_pasta(pastas: list[dict], termo: str | None = None) -> dict | None:
    """Filtra pastas por termo e retorna a melhor correspondência."""
    termo = (termo or PASTA_OUTLOOK_PADRAO).strip().upper()

    # 1) Nome exato
    exatas = [p for p in pastas if p["nome"].strip().upper() == termo]
    if len(exatas) == 1:
        return exatas[0]
    if len(exatas) > 1:
        exatas.sort(key=lambda p: (-p["total_itens"], p["nivel"]))
        return exatas[0]

    # 2) Termo contido no nome
    candidatas = [p for p in pastas if termo in p["nome"].upper()]

    # 3) Termo contido no caminho completo (acha subpastas por contexto)
    if not candidatas:
        candidatas = [p for p in pastas if termo in p["caminho"].upper()]

    if len(candidatas) == 1:
        return candidatas[0]
    if len(candidatas) > 1:
        candidatas.sort(key=lambda p: (-p["total_itens"], p["nivel"], p["caminho"]))
        return candidatas[0]
    return None


def _localizar_pasta_por_caminho(ns, caminho: str):
    """Localiza uma pasta pelo caminho completo 'Caixa/Sub/SubSub'."""
    partes = [p for p in caminho.split("/") if p]
    if not partes:
        return None

    def _buscar(pasta, restante):
        if not restante:
            return pasta
        alvo = restante[0].strip().upper()
        try:
            for sub in pasta.Folders:
                if sub.Name.strip().upper() == alvo:
                    return _buscar(sub, restante[1:])
        except Exception:
            pass
        return None

    for store in ns.Folders:
        if store.Name.strip().upper() == partes[0].strip().upper():
            return _buscar(store, partes[1:])
        try:
            for pasta in store.Folders:
                if pasta.Name.strip().upper() == partes[0].strip().upper():
                    return _buscar(pasta, partes[1:])
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# DESTINO DOS ANEXOS
# ---------------------------------------------------------------------------


def validar_pasta_destino(caminho: str | Path) -> Path:
    """Valida e cria a pasta de destino dos anexos.

    Aceita caminho absoluto ou relativo ao projeto.
    Levanta ValueError se o caminho for inválido ou não puder ser criado.
    """
    p = Path(caminho).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p

    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise ValueError(f"Não foi possível criar a pasta de destino '{p}': {e}") from e

    if not p.is_dir():
        raise ValueError(f"O caminho '{p}' existe mas não é uma pasta")

    teste = p / ".escrita_ok"
    try:
        teste.write_text("ok", encoding="utf-8")
        teste.unlink()
    except Exception as e:
        raise ValueError(f"A pasta '{p}' não permite escrita: {e}") from e

    return p


def _pasta_remessa(numero_remessa: str, pasta_destino: Path) -> Path:
    """Cria a subpasta da remessa dentro do destino escolhido."""
    pasta = pasta_destino / numero_remessa
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


# ---------------------------------------------------------------------------
# EXTRAÇÃO E DOWNLOAD (SOMENTE LEITURA)
# ---------------------------------------------------------------------------


def _extrair_remessa(assunto: str) -> str | None:
    if not assunto:
        return None
    m = RE_REMESSA.search(assunto)
    return m.group(1) if m else None


def _salvar_anexos(mensagem, destino: Path) -> list[Path]:
    """Salva anexos em disco. Não altera nada no e-mail."""
    salvos = []
    try:
        anexos = mensagem.Attachments
        for i in range(1, anexos.Count + 1):
            anexo = anexos.Item(i)
            nome = anexo.FileName
            if not nome:
                continue
            caminho = destino / nome
            if caminho.exists():
                stem, suf = caminho.stem, caminho.suffix
                n = 1
                while caminho.exists():
                    caminho = destino / f"{stem}_{n}{suf}"
                    n += 1
            anexo.SaveAsFile(str(caminho))
            salvos.append(caminho)
    except Exception as e:
        log.warning("Erro ao salvar anexos: %s", e)
    return salvos


def _coletar_itens_pasta(pasta, corte, prefixo: str, vistos: set, resultados: list):
    """Varre uma pasta e suas subpastas, acumulando e-mails de reembolso."""
    try:
        itens = pasta.Items
        itens.Sort("[ReceivedTime]", True)
        for item in itens:
            try:
                if getattr(item, "Class", None) != 43:
                    continue
                data = item.ReceivedTime
                if data and data.replace(tzinfo=None) < corte:
                    continue
                assunto = item.Subject or ""
                remessa = _extrair_remessa(assunto)
                if not remessa:
                    continue
                eid = item.EntryID
                if eid in vistos:
                    continue
                vistos.add(eid)
                resultados.append(
                    {
                        "entry_id": eid,
                        "assunto": assunto,
                        "remetente": item.SenderName or "",
                        "data_email": data.isoformat(timespec="seconds")
                        if data
                        else None,
                        "remessa": remessa,
                        "pasta_origem": prefixo,
                        "item": item,
                    }
                )
            except Exception as e:
                log.warning("Erro ao ler item: %s", e)
                continue
    except Exception as e:
        log.warning("Erro ao iterar pasta '%s': %s", prefixo, e)

    try:
        for sub in pasta.Folders:
            _coletar_itens_pasta(
                sub, corte, f"{prefixo}/{sub.Name}", vistos, resultados
            )
    except Exception:
        pass


def listar_emails_reembolso(caminho_pasta: str, dias: int | None = None) -> list[dict]:
    """Lista e-mails de uma pasta específica do Outlook (somente leitura)."""
    dias = dias or EMAIL_MAX_AGE_DAYS
    ns = _conectar()
    pasta = _localizar_pasta_por_caminho(ns, caminho_pasta)
    if not pasta:
        log.warning("Pasta '%s' não encontrada no Outlook", caminho_pasta)
        return []

    corte = datetime.now() - timedelta(days=dias)
    resultados = []
    vistos = set()
    _coletar_itens_pasta(pasta, corte, caminho_pasta, vistos, resultados)

    log.info(
        "Encontrados %d e-mails de reembolso em '%s'", len(resultados), caminho_pasta
    )
    return resultados


def listar_emails_recursivo(caminho_pasta: str, dias: int | None = None) -> list[dict]:
    """Lista e-mails de reembolso da pasta escolhida E de todas as subpastas."""
    dias = dias or EMAIL_MAX_AGE_DAYS
    ns = _conectar()
    raiz = _localizar_pasta_por_caminho(ns, caminho_pasta)
    if not raiz:
        log.warning("Pasta '%s' não encontrada no Outlook", caminho_pasta)
        return []

    corte = datetime.now() - timedelta(days=dias)
    resultados = []
    vistos = set()
    _coletar_itens_pasta(raiz, corte, caminho_pasta, vistos, resultados)

    log.info(
        "Encontrados %d e-mails de reembolso em '%s' (recursivo)",
        len(resultados),
        caminho_pasta,
    )
    return resultados


def processar_email(email: dict, pasta_destino: Path, ordem: int = 0) -> dict:
    """Baixa anexos de um e-mail para a pasta escolhida. Não altera o e-mail."""
    entry_id = email["entry_id"]
    remessa = email["remessa"]

    if email_ja_processado(entry_id):
        log.info("E-mail %s já processado, ignorado", entry_id[:20])
        return {"remessa": remessa, "anexos": [], "pulado": True}

    destino = _pasta_remessa(remessa, pasta_destino)
    anexos = _salvar_anexos(email["item"], destino)

    registrar_email(
        entry_id=entry_id,
        numero_remessa=remessa,
        assunto=email["assunto"],
        remetente=email["remetente"],
        data_email=email["data_email"],
        ordem=ordem,
    )

    log.info("Remessa %s: %d anexos salvos em %s", remessa, len(anexos), destino)
    return {"remessa": remessa, "anexos": anexos, "pulado": False}


def coletar_todos(
    caminho_pasta: str,
    pasta_destino: str | Path | None = None,
    dias: int | None = None,
    recursivo: bool = False,
) -> dict:
    """Coleta completa: lê a pasta escolhida e salva anexos no destino escolhido.

    caminho_pasta: caminho do Outlook retornado por listar_pastas()['caminho'].
    pasta_destino: onde salvar os anexos. Se None, usa PASTA_ANEXOS_PADRAO.
    recursivo: quando True, varre a pasta e todas as subpastas.
    """
    try:
        destino = validar_pasta_destino(pasta_destino or PASTA_ANEXOS_PADRAO)
    except ValueError as e:
        log.error("%s", e)
        return {"ok": False, "erro": str(e), "remessas": {}}

    try:
        if recursivo:
            emails = listar_emails_recursivo(caminho_pasta, dias)
        else:
            emails = listar_emails_reembolso(caminho_pasta, dias)
    except OutlookIndisponivel as e:
        log.error("%s", e)
        return {"ok": False, "erro": str(e), "remessas": {}}

    remessas = {}
    for ordem, email in enumerate(emails):
        try:
            r = processar_email(email, destino, ordem)
            rem = r["remessa"]
            if rem not in remessas:
                remessas[rem] = {"anexos": [], "pulado": r["pulado"]}
            remessas[rem]["anexos"].extend(r["anexos"])
        except Exception as e:
            log.warning("Erro ao processar e-mail %s: %s", email["entry_id"][:20], e)

    return {
        "ok": True,
        "pasta_origem": caminho_pasta,
        "pasta_destino": str(destino),
        "remessas": remessas,
        "total_emails": len(emails),
        "recursivo": recursivo,
    }

"""Ponto de entrada do sistema. Menu interativo de coleta e conciliação.

Fluxo:
1. Lista TODAS as pastas do Outlook (somente leitura)
2. Usuário escolhe a pasta de leitura (ou varre a árvore inteira)
3. Usuário define a pasta de destino dos anexos
4. Sistema mostra prévia (quantos e-mails, quantos anexos)
5. Usuário confirma
6. Sistema baixa os anexos
7. Usuário pode conciliar os anexos baixados
8. Usuário pode acompanhar pelo dashboard e exportar para Excel
"""

import sys
from pathlib import Path

import database as db
import persistence as ps
from config import EMAIL_MAX_AGE_DAYS, PASTA_ANEXOS_PADRAO, Status
from log_manager import get_logger
from outlook_reader import (
    OutlookIndisponivel,
    coletar_todos,
    escolher_pasta,
    listar_emails_recursivo,
    listar_emails_reembolso,
    listar_pastas,
    validar_pasta_destino,
)

log = get_logger("main")


# ---------------------------------------------------------------------------
# UTILITÁRIOS DE MENU
# ---------------------------------------------------------------------------


def _linha(char="─", n=70):
    print(char * n)


def _titulo(texto):
    print()
    _linha("═")
    print(f"  {texto}")
    _linha("═")


def _pedir_texto(msg, padrao=None):
    """Pede texto ao usuário. Enter vazio usa o padrão."""
    if padrao:
        entrada = input(f"{msg} [{padrao}]: ").strip()
        return entrada or padrao
    return input(f"{msg}: ").strip()


def _pedir_confirmacao(msg) -> bool:
    r = input(f"{msg} (s/N): ").strip().lower()
    return r in ("s", "sim", "y", "yes")


# ---------------------------------------------------------------------------
# ETAPA 1: ESCOLHER PASTA DE LEITURA
# ---------------------------------------------------------------------------


def escolher_pasta_leitura() -> tuple[str | None, bool]:
    """Retorna (caminho, recursivo).

    Mostra TODAS as pastas e subpastas do Outlook, sem nenhum filtro.
    recursivo=True quando o usuário escolhe varrer a pasta + todas as subpastas.
    """
    _titulo("1. Escolher pasta de leitura no Outlook")

    try:
        pastas = listar_pastas()
    except OutlookIndisponivel as e:
        print(f"\n  ERRO: {e}")
        print("  Verifique se o Outlook está aberto e tente novamente.")
        return None, False

    if not pastas:
        print("\n  Nenhuma pasta encontrada no Outlook.")
        return None, False

    print(f"\n  {len(pastas)} pastas encontradas (todas as contas e subpastas):\n")
    for i, p in enumerate(pastas, 1):
        indent = "   " * p["nivel"]
        qtd = f"{p['total_itens']:>5}" if p["total_itens"] >= 0 else "    ?"
        print(f"  {i:>3}. {indent}{p['nome']:<34} ({qtd} itens)")

    print()
    print(
        "  Digite o número, parte do nome, ou 'T' para varrer a pasta + todas as subpastas."
    )
    entrada = input("  Escolha (Enter = buscar 'REEMBOLSO'): ").strip()

    recursivo = False
    if entrada.upper() == "T":
        recursivo = True
        entrada = ""

    if not entrada:
        p = escolher_pasta(pastas)
        if p:
            print(f"\n  Pasta escolhida: {p['caminho']}")
            if recursivo:
                print("  Modo: pasta + todas as subpastas")
            return p["caminho"], recursivo
        print("\n  Nenhuma pasta corresponde ao termo padrão.")
        return None, False

    if entrada.isdigit():
        idx = int(entrada) - 1
        if 0 <= idx < len(pastas):
            p = pastas[idx]
            print(f"\n  Pasta escolhida: {p['caminho']}")
            if recursivo:
                print("  Modo: pasta + todas as subpastas")
            return p["caminho"], recursivo
        print(f"\n  Número inválido. Digite entre 1 e {len(pastas)}.")
        return None, False

    p = escolher_pasta(pastas, entrada)
    if p:
        print(f"\n  Pasta escolhida: {p['caminho']}")
        if recursivo:
            print("  Modo: pasta + todas as subpastas")
        return p["caminho"], recursivo

    print(f"\n  Nenhuma pasta encontrada com '{entrada}'.")
    return None, False


# ---------------------------------------------------------------------------
# ETAPA 2: DEFINIR DESTINO DOS ANEXOS
# ---------------------------------------------------------------------------


def escolher_destino() -> Path | None:
    _titulo("2. Definir pasta de destino dos anexos")
    print(f"\n  Padrão: {PASTA_ANEXOS_PADRAO}")
    print("  Aceita caminho absoluto (D:\\Reembolsos) ou relativo (data\\Anexos)\n")

    while True:
        entrada = _pedir_texto("  Pasta de destino", str(PASTA_ANEXOS_PADRAO))
        try:
            destino = validar_pasta_destino(entrada)
            print(f"\n  Destino validado: {destino}")
            return destino
        except ValueError as e:
            print(f"\n  ERRO: {e}")
            if not _pedir_confirmacao("  Tentar outro caminho?"):
                return None


# ---------------------------------------------------------------------------
# ETAPA 3: PRÉVIA
# ---------------------------------------------------------------------------


def mostrar_previa(
    caminho_pasta: str, destino: Path, recursivo: bool = False
) -> list[dict] | None:
    _titulo("3. Prévia do processamento")
    print(f"\n  Lendo e-mails de: {caminho_pasta}")
    if recursivo:
        print("  Modo: pasta + todas as subpastas")
    print(f"  Salvando anexos em: {destino}")
    print(f"  Período: últimos {EMAIL_MAX_AGE_DAYS} dias\n")

    try:
        if recursivo:
            emails = listar_emails_recursivo(caminho_pasta)
        else:
            emails = listar_emails_reembolso(caminho_pasta)
    except OutlookIndisponivel as e:
        print(f"  ERRO: {e}")
        return None

    if not emails:
        print("  Nenhum e-mail de reembolso encontrado nessa pasta.")
        return None

    por_remessa = {}
    for e in emails:
        por_remessa.setdefault(e["remessa"], []).append(e)

    total_anexos = 0
    ja_processados = 0
    novos = 0

    for remessa, lista in por_remessa.items():
        for e in lista:
            try:
                total_anexos += e["item"].Attachments.Count
            except Exception:
                pass
            if db.email_ja_processado(e["entry_id"]):
                ja_processados += 1
            else:
                novos += 1

    print(f"  E-mails encontrados:      {len(emails)}")
    print(f"  Remessas distintas:       {len(por_remessa)}")
    print(f"  Anexos no total:          {total_anexos}")
    print(f"  E-mails já processados:   {ja_processados} (serão ignorados)")
    print(f"  E-mails novos:            {novos}")

    if novos == 0:
        print("\n  Todos os e-mails já foram processados antes. Nada a fazer.")
        return None

    if recursivo:
        por_pasta = {}
        for e in emails:
            origem = e.get("pasta_origem", "?")
            por_pasta[origem] = por_pasta.get(origem, 0) + 1
        if len(por_pasta) > 1:
            print("\n  E-mails por pasta de origem:")
            for origem, qtd in sorted(por_pasta.items(), key=lambda x: -x[1]):
                print(f"    {qtd:>4}  {origem}")

    print("\n  Primeiras remessas:")
    for i, (remessa, lista) in enumerate(list(por_remessa.items())[:15], 1):
        anexos = sum(e["item"].Attachments.Count for e in lista)
        print(f"    {i:>2}. {remessa} — {len(lista)} e-mail(s), {anexos} anexo(s)")
    if len(por_remessa) > 15:
        print(f"    ... e mais {len(por_remessa) - 15} remessas")

    return emails


# ---------------------------------------------------------------------------
# ETAPA 4: EXECUÇÃO DA COLETA
# ---------------------------------------------------------------------------


def executar_coleta(caminho_pasta: str, destino: Path, recursivo: bool = False) -> dict:
    _titulo("4. Baixando anexos")
    print("\n  Processando... (pode levar alguns minutos)\n")

    resultado = coletar_todos(caminho_pasta, destino, recursivo=recursivo)

    if not resultado.get("ok"):
        print(f"\n  ERRO: {resultado.get('erro')}")
        return resultado

    remessas = resultado.get("remessas", {})
    total_anexos = sum(len(r["anexos"]) for r in remessas.values())
    pulados = sum(1 for r in remessas.values() if r.get("pulado"))

    print(f"\n  E-mails processados: {resultado['total_emails']}")
    print(f"  Remessas com anexos: {len(remessas)}")
    print(f"  Anexos salvos:       {total_anexos}")
    print(f"  Remessas puladas:    {pulados}")

    return resultado


# ---------------------------------------------------------------------------
# MENU: COLETA
# ---------------------------------------------------------------------------


def menu_coleta():
    """Fluxo completo de coleta: pasta → destino → prévia → confirmação → download."""
    _titulo("COLETA DE ANEXOS DO OUTLOOK")

    caminho_pasta, recursivo = escolher_pasta_leitura()
    if not caminho_pasta:
        print("\n  Coleta cancelada.")
        return

    destino = escolher_destino()
    if not destino:
        print("\n  Coleta cancelada.")
        return

    emails = mostrar_previa(caminho_pasta, destino, recursivo=recursivo)
    if not emails:
        print("\n  Coleta cancelada.")
        return

    print()
    _linha()
    if not _pedir_confirmacao("  Confirma o download dos anexos?"):
        print("\n  Coleta cancelada.")
        return

    executar_coleta(caminho_pasta, destino, recursivo=recursivo)


# ---------------------------------------------------------------------------
# MENU: CONCILIAÇÃO
# ---------------------------------------------------------------------------


def menu_conciliacao():
    """Concilia os anexos já baixados."""
    from pipeline import processar_pasta_raiz

    _titulo("CONCILIAÇÃO DE ANEXOS BAIXADOS")

    entrada = _pedir_texto("  Pasta raiz com as remessas", str(PASTA_ANEXOS_PADRAO))
    raiz = Path(entrada)

    if not raiz.is_dir():
        print(f"\n  ERRO: pasta não encontrada: {raiz}")
        return

    subpastas = sorted(p for p in raiz.iterdir() if p.is_dir())
    if not subpastas:
        print("\n  Nenhuma subpasta de remessa encontrada.")
        return

    print(f"\n  {len(subpastas)} remessas encontradas:")
    for s in subpastas[:15]:
        n = len(list(s.iterdir()))
        print(f"    {s.name} — {n} arquivo(s)")
    if len(subpastas) > 15:
        print(f"    ... e mais {len(subpastas) - 15}")

    print()
    if not _pedir_confirmacao("  Confirma a conciliação?"):
        print("\n  Cancelado.")
        return

    resultado = processar_pasta_raiz(raiz)

    if not resultado.get("ok"):
        print(f"\n  ERRO: {resultado.get('motivo')}")
        return

    print(f"\n  Remessas processadas: {resultado['processadas']}")
    print(f"  Falhas:               {resultado['falhas']}")
    print("\n  Detalhe:")
    for r in resultado["resultados"]:
        if r.get("ok"):
            print(f"    {r['remessa']:<20} {str(r['status']):<24} score {r['score']}")
        else:
            print(f"    {r['remessa']:<20} ERRO: {r.get('motivo')}")


# ---------------------------------------------------------------------------
# MENU: DASHBOARD
# ---------------------------------------------------------------------------


def menu_dashboard():
    """Mostra o dashboard de acompanhamento."""
    from dashboard import mostrar_dashboard

    mostrar_dashboard()


# ---------------------------------------------------------------------------
# MENU: EXPORTAÇÃO
# ---------------------------------------------------------------------------


def menu_exportacao():
    """Exporta os reembolsos para Excel."""
    from export_excel import exportar

    _titulo("EXPORTAÇÃO PARA EXCEL")
    entrada = _pedir_texto("  Caminho do arquivo (Enter = automático)", "")
    caminho = entrada.strip() or None

    try:
        arquivo = exportar(caminho)
        print(f"\n  Arquivo gerado: {arquivo}")
    except Exception as e:
        print(f"\n  ERRO: {e}")


# ---------------------------------------------------------------------------
# MENU: ESTATÍSTICAS
# ---------------------------------------------------------------------------


def menu_estatisticas():
    """Mostra estatísticas do banco."""
    _titulo("ESTATÍSTICAS DO SISTEMA")

    por_status = ps.estatisticas_por_status()
    if not por_status:
        print("\n  Nenhum reembolso no banco ainda.")
        return

    print("\n  Reembolsos por status:")
    for status, qtd in sorted(por_status.items(), key=lambda x: -x[1]):
        print(f"    {status:<32} {qtd:>5}")

    por_camada = ps.estatisticas_por_camada()
    if por_camada:
        print("\n  Conciliações por camada:")
        for camada, qtd in sorted(por_camada.items(), key=lambda x: -x[1]):
            print(f"    {camada:<32} {qtd:>5}")

    print("\n  Últimas 10 mudanças de status:")
    for h in ps.historico_recente(10):
        print(
            f"    {h['criado_em'][:19]} | {h['numero_remessa']} | "
            f"{h['status_anterior']} -> {h['status_novo']}"
        )


# ---------------------------------------------------------------------------
# MENU: FILA DE TREINAMENTO
# ---------------------------------------------------------------------------


def menu_ver_fila():
    """Lista a fila de treinamento formatada."""
    import ver_fila

    _titulo("FILA DE TREINAMENTO")

    entrada = input("  Filtrar por remessa (Enter = todas): ").strip()
    remessa = entrada or None

    entradas = ver_fila._carregar(remessa=remessa)

    if not entradas:
        print("\n  Fila vazia. Nenhum documento pendente de treinamento.")
        return

    print()
    print(f"  {ver_fila._resumo(entradas)}")
    print()

    limite = 5
    for e in entradas[:limite]:
        print(ver_fila._formatar(e, 800))

    if len(entradas) > limite:
        print(f"  ... e mais {len(entradas) - limite} entrada(s)")
        print("  Use a opção 7 para resolver, ou filtre por remessa.")


def menu_resolver_fila():
    """Marca entradas da fila como resolvidas."""
    import resolver_fila
    import ver_fila

    _titulo("RESOLVER ITENS DA FILA")

    entradas = ver_fila._carregar()
    if not entradas:
        print("\n  Fila vazia. Nada a resolver.")
        return

    print(f"\n  {len(entradas)} entrada(s) pendente(s):\n")
    for e in entradas:
        print(resolver_fila._resumo_entrada(e))

    print()
    entrada = input(
        "  IDs para marcar como resolvido (ex: 1 2 3, Enter = cancelar): "
    ).strip()
    if not entrada:
        print("\n  Cancelado.")
        return

    try:
        ids = [int(x) for x in entrada.split()]
    except ValueError:
        print("\n  ERRO: use apenas números separados por espaço.")
        return

    alvo = [e for e in entradas if e["id"] in ids]
    if not alvo:
        print("\n  Nenhum ID válido entre os pendentes.")
        return

    print(f"\n  Serão marcadas {len(alvo)} entrada(s):")
    for e in alvo:
        print(resolver_fila._resumo_entrada(e))

    print()
    if not _pedir_confirmacao("  Confirma?"):
        print("\n  Cancelado. Nada foi alterado.")
        return

    alteradas = resolver_fila._aplicar(ids, 1)
    print(f"\n  OK: {alteradas} entrada(s) marcada(s) como resolvida(s).")


def menu_ver_layouts():
    """Lista os layouts treinados."""
    import ver_layouts

    _titulo("LAYOUTS TREINADOS")

    entradas = ver_layouts._carregar()

    if not entradas:
        print("\n  Nenhum layout treinado ainda.")
        print("  Os layouts são gravados quando o pdf_reader extrai campos")
        print("  com rótulo identificado.")
        return

    print()
    print(f"  {ver_layouts._resumo(entradas)}")
    print()

    for e in entradas:
        print(ver_layouts._formatar(e))


# ---------------------------------------------------------------------------
# MENU: RESOLVER REVISÃO MANUAL
# ---------------------------------------------------------------------------


def menu_resolver_revisao():
    """Permite decidir o destino de registros em revisão."""
    import resolver_revisao as rr

    _titulo("RESOLVER REVISÃO MANUAL")

    registros = rr.listar_em_revisao()

    if not registros:
        print("\n  Nenhum registro em revisão. Tudo conciliado.")
        return

    print(f"\n  {len(registros)} registro(s) precisam de decisão:\n")
    for i, r in enumerate(registros, 1):
        print(rr._formatar(r, i))

    print()
    print("  Ações disponíveis:")
    print("    C = Conferir (aceitar como correto)")
    print("    M = Manter em revisão (não decide agora)")
    print("    D = Marcar como duplicado")
    print()

    entrada = input("  Número do registro (Enter = cancelar): ").strip()
    if not entrada.isdigit():
        print("\n  Cancelado.")
        return

    idx = int(entrada) - 1
    if not (0 <= idx < len(registros)):
        print("\n  Número inválido.")
        return

    alvo = registros[idx]

    acao = input("  Ação (C/M/D): ").strip().upper()
    mapa = {"C": "CONFERIR", "M": "MANTER", "D": "DUPLICADO"}
    if acao not in mapa:
        print("\n  Ação inválida. Cancelado.")
        return

    obs = input("  Observação (opcional, Enter = padrão): ").strip()

    if not _pedir_confirmacao(f"  Aplicar {mapa[acao]} em {alvo['numero_remessa']}?"):
        print("\n  Cancelado. Nada foi alterado.")
        return

    resultado = rr.aplicar_decisao(alvo["numero_remessa"], mapa[acao], obs)

    if resultado.get("ok"):
        print(f"\n  OK: {resultado['remessa']}")
        print(f"  {resultado['status_anterior']} -> {resultado['status_novo']}")
    else:
        print(f"\n  ERRO: {resultado.get('motivo')}")


# ---------------------------------------------------------------------------
# MENU: REPROCESSAR
# ---------------------------------------------------------------------------


def menu_reprocessar():
    """Reprocessa remessas específicas."""
    import reprocessar as rp

    _titulo("REPROCESSAR REMESSAS")

    registros = rp.listar_reprocessaveis()
    print()

    if not _pedir_confirmacao("  Confirma o reprocessamento?"):
        print("\n  Cancelado.")
        return

    import backup as bk

    bk.criar_backup("reprocessar")

    numeros = [r["numero_remessa"] for r in alvos]
    resultado = rp.reprocessar_lote(numeros)
    if not registros:
        print("\n  Nenhuma remessa em status reprocessável.")
        print("  (Só entram aqui: revisão, divergências e aguardando reembolso.)")
        return

    print(f"\n  {len(registros)} remessa(s) reprocessável(is):\n")
    for i, r in enumerate(registros, 1):
        print(
            f"    {i:>2}. {r['numero_remessa']:<20} "
            f"{r.get('status'):<24} score {r.get('score') or 0}"
        )

    print()
    print("  Digite os números separados por espaço (ex: 1 3 5)")
    print("  Ou 'T' para reprocessar todas")
    entrada = input("  Escolha (Enter = cancelar): ").strip()

    if not entrada:
        print("\n  Cancelado.")
        return

    if entrada.upper() == "T":
        alvos = registros
    else:
        try:
            indices = [int(x) - 1 for x in entrada.split()]
        except ValueError:
            print("\n  ERRO: use apenas números separados por espaço.")
            return

        alvos = [registros[i] for i in indices if 0 <= i < len(registros)]
        if not alvos:
            print("\n  Nenhum número válido.")
            return

    print(f"\n  Serão reprocessadas {len(alvos)} remessa(s):")
    for r in alvos:
        print(f"    {r['numero_remessa']}")

    print()
    if not _pedir_confirmacao("  Confirma o reprocessamento?"):
        print("\n  Cancelado.")
        return

    numeros = [r["numero_remessa"] for r in alvos]
    resultado = rp.reprocessar_lote(numeros)

    print(f"\n  Processadas: {resultado['processadas']}")
    print(f"  Falhas:      {resultado['falhas']}")
    print("\n  Detalhe:")
    for r in resultado["resultados"]:
        if r.get("ok"):
            print(
                f"    {r['remessa']:<20} {str(r.get('status')):<24} "
                f"score {r.get('score')}"
            )
        else:
            print(f"    {r['remessa']:<20} ERRO: {r.get('motivo')}")


# ---------------------------------------------------------------------------
# MENU: BACKUP
# ---------------------------------------------------------------------------


def menu_backup():
    """Gerencia os backups do banco."""
    import backup as bk

    _titulo("BACKUP DO BANCO")

    backups = bk.listar_backups()

    if backups:
        print(f"\n  {len(backups)} backup(s) existente(s):\n")
        for b in backups:
            print(f"    {b['nome']:<45} {b['tamanho_kb']:>7} KB  {b['modificado']}")
    else:
        print("\n  Nenhum backup ainda.")

    print()
    print("  B = Criar backup agora")
    print("  Enter = voltar")

    escolha = input("  Escolha: ").strip().upper()

    if escolha != "B":
        return

    resultado = bk.criar_backup("manual")

    if resultado.get("ok"):
        print(f"\n  OK: {resultado['nome']} ({resultado['tamanho_kb']} KB)")
    else:
        print(f"\n  ERRO: {resultado.get('motivo')}")


# ---------------------------------------------------------------------------
# MENU PRINCIPAL
# ---------------------------------------------------------------------------


def menu_principal():
    """Menu principal do sistema."""
    db.init_db()
    ps.init_historico()

    while True:
        _titulo("SISTEMA DE CONCILIAÇÃO DE REEMBOLSOS — JADLOG")
        print("\n  1. Coletar anexos do Outlook")
        print("  2. Conciliar anexos baixados")
        print("  3. Dashboard de acompanhamento")
        print("  4. Exportar para Excel")
        print("  5. Ver estatísticas do banco")
        print("  6. Ver fila de treinamento")
        print("  7. Resolver itens da fila")
        print("  8. Ver layouts treinados")
        print("  9. Resolver revisão manual")
        print(" 10. Reprocessar remessas")
        print(" 11. Backup do banco")
        print(" 12. Sair\n")
        opcao = input("  Escolha uma opção: ").strip()

        if opcao == "1":
            menu_coleta()
        elif opcao == "2":
            menu_conciliacao()
        elif opcao == "3":
            menu_dashboard()
        elif opcao == "4":
            menu_exportacao()
        elif opcao == "5":
            menu_estatisticas()
        elif opcao == "6":
            menu_ver_fila()
        elif opcao == "7":
            menu_resolver_fila()
        elif opcao == "8":
            menu_ver_layouts()
        elif opcao == "9":
            menu_resolver_revisao()
        elif opcao == "10":
            menu_reprocessar()
        elif opcao == "11":
            menu_backup()
        elif opcao == "12":
            print("\n  Até logo.")
            break
        else:
            print("\n  Opção inválida.")

        print()
        input("  Pressione Enter para continuar...")


if __name__ == "__main__":
    try:
        menu_principal()
    except KeyboardInterrupt:
        print("\n\n  Interrompido pelo usuário.")
        sys.exit(0)

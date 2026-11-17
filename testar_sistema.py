"""Diagnóstico do sistema: valida imports, pastas, banco e assinaturas.

Uso:
    python testar_sistema.py

Não altera nada no banco. Só lê e reporta.
"""

import inspect
import sys
import traceback
from pathlib import Path

# ---------------------------------------------------------------------------
# CONTADORES
# ---------------------------------------------------------------------------

_ok = 0
_falhas = 0
_avisos = 0


def _titulo(texto):
    print()
    print("═" * 70)
    print(f"  {texto}")
    print("═" * 70)


def _ok_msg(msg):
    global _ok
    _ok += 1
    print(f"  [OK]    {msg}")


def _erro(msg, detalhe=None):
    global _falhas
    _falhas += 1
    print(f"  [FALHA] {msg}")
    if detalhe:
        for linha in str(detalhe).splitlines():
            print(f"          {linha}")


def _aviso(msg):
    global _avisos
    _avisos += 1
    print(f"  [AVISO] {msg}")


# ---------------------------------------------------------------------------
# 1. MÓDULOS DO PROJETO
# ---------------------------------------------------------------------------

MODULOS = [
    "config",
    "log_manager",
    "utils",
    "database",
    "persistence",
    "ocr_engine",
    "pdf_reader",
    "excel_reader",
    "document_classifier",
    "conciliation_engine",
    "outlook_reader",
    "pipeline",
    "export_excel",
    "dashboard",
]


def testar_imports():
    _titulo("1. IMPORTS DOS MÓDULOS")

    importados = {}
    for nome in MODULOS:
        try:
            modulo = __import__(nome)
            importados[nome] = modulo
            _ok_msg(f"{nome}.py")
        except ModuleNotFoundError as e:
            if e.name == nome:
                _erro(f"{nome}.py não encontrado na raiz do projeto")
            else:
                _erro(f"{nome}.py — dependência ausente: {e.name}")
        except Exception as e:
            _erro(f"{nome}.py — erro ao importar", e)

    return importados


# ---------------------------------------------------------------------------
# 2. DEPENDÊNCIAS EXTERNAS
# ---------------------------------------------------------------------------

DEPENDENCIAS = [
    ("pandas", "pandas"),
    ("openpyxl", "openpyxl"),
    ("rapidfuzz", "rapidfuzz"),
    ("pytesseract", "pytesseract"),
    ("pdfplumber", "pdfplumber"),
    ("PIL", "Pillow"),
    ("win32com", "pywin32"),
]


def testar_dependencias():
    _titulo("2. DEPENDÊNCIAS EXTERNAS")

    for import_name, pip_name in DEPENDENCIAS:
        try:
            mod = __import__(import_name)
            versao = getattr(mod, "__version__", "?")
            _ok_msg(f"{pip_name} (versão {versao})")
        except ImportError:
            _erro(f"{pip_name} não instalado — rode: pip install {pip_name}")


# ---------------------------------------------------------------------------
# 3. PASTAS E CAMINHOS
# ---------------------------------------------------------------------------


def testar_pastas():
    _titulo("3. PASTAS E CAMINHOS")

    try:
        import config
    except Exception as e:
        _erro("config.py não pôde ser importado", e)
        return

    caminhos = [
        ("BASE_DIR", config.BASE_DIR),
        ("DATA_DIR", config.DATA_DIR),
        ("REEMBOLSOS_DIR", config.REEMBOLSOS_DIR),
        ("LOGS_DIR", config.LOGS_DIR),
        ("PASTA_EXPORTACAO", getattr(config, "PASTA_EXPORTACAO", None)),
    ]

    for nome, caminho in caminhos:
        if caminho is None:
            _aviso(f"{nome} não definido no config.py")
            continue
        if Path(caminho).is_dir():
            _ok_msg(f"{nome}: {caminho}")
        else:
            _erro(f"{nome} não existe: {caminho}")

    # Tesseract
    tesseract = Path(getattr(config, "TESSERACT_CMD", ""))
    if tesseract.is_file():
        _ok_msg(f"Tesseract: {tesseract}")
    else:
        _aviso(f"Tesseract não encontrado em {tesseract} — OCR de imagens vai falhar")


# ---------------------------------------------------------------------------
# 4. BANCO DE DADOS
# ---------------------------------------------------------------------------

TABELAS_ESPERADAS = [
    "reembolsos",
    "historico_conciliacao",
    "layout_treinado",
    "fila_treinamento",
    "email_processado",
]

COLUNAS_REEMBOLSOS = [
    "id",
    "numero_remessa",
    "numero_nf",
    "numero_guia",
    "cliente",
    "cnpj_cliente",
    "uf",
    "tributo",
    "valor_guia",
    "data_emissao",
    "data_vencimento",
    "valor_pago_jadlog",
    "data_pagamento_jadlog",
    "valor_reembolso_cliente",
    "data_reembolso_cliente",
    "dias_reembolso",
    "status",
    "score",
    "caminho_guia",
    "caminho_comp_jadlog",
    "caminho_comp_cliente",
    "data_importacao",
    "data_atualizacao",
]


def testar_banco():
    _titulo("4. BANCO DE DADOS")

    try:
        import database as db
        import persistence as ps
        from config import DB_PATH
    except Exception as e:
        _erro("Não foi possível importar database/persistence", e)
        return

    # Cria as tabelas se não existirem
    try:
        db.init_db()
        ps.init_historico()
        _ok_msg("init_db() e init_historico() executados")
    except Exception as e:
        _erro("Erro ao inicializar o banco", e)
        return

    if not Path(DB_PATH).is_file():
        _erro(f"Arquivo do banco não criado: {DB_PATH}")
        return
    _ok_msg(f"Arquivo do banco: {DB_PATH}")

    # Tabelas
    try:
        with db.cursor() as cur:
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            existentes = {r["name"] for r in cur.fetchall()}
    except Exception as e:
        _erro("Erro ao listar tabelas", e)
        return

    for tabela in TABELAS_ESPERADAS:
        if tabela in existentes:
            _ok_msg(f"Tabela {tabela}")
        else:
            _erro(f"Tabela {tabela} não existe")

    # Colunas da tabela reembolsos
    try:
        with db.cursor() as cur:
            cur.execute("PRAGMA table_info(reembolsos)")
            colunas = {r["name"] for r in cur.fetchall()}
    except Exception as e:
        _erro("Erro ao ler colunas de reembolsos", e)
        return

    faltando = [c for c in COLUNAS_REEMBOLSOS if c not in colunas]
    if faltando:
        _erro(f"Colunas ausentes em reembolsos: {', '.join(faltando)}")
    else:
        _ok_msg(f"Tabela reembolsos com as {len(COLUNAS_REEMBOLSOS)} colunas esperadas")

    # Contagem de registros
    try:
        with db.cursor() as cur:
            cur.execute("SELECT COUNT(*) c FROM reembolsos")
            total = cur.fetchone()["c"]
        _ok_msg(f"Reembolsos no banco: {total}")
    except Exception as e:
        _erro("Erro ao contar reembolsos", e)


# ---------------------------------------------------------------------------
# 5. ASSINATURAS DAS FUNÇÕES
# ---------------------------------------------------------------------------

ASSINATURAS = [
    # (módulo, função, nº de parâmetros obrigatórios, nomes dos parâmetros)
    ("document_classifier", "classificar", 1, ["caminho", "texto"]),
    ("pdf_reader", "extrair_por_tipo", 2, ["caminho", "tipo"]),
    ("excel_reader", "extrair_linhas_planilha", 1, ["caminho"]),
    (
        "conciliation_engine",
        "conciliar_remessa",
        3,
        ["guia", "comp_jadlog", "comp_cliente", "pendentes_cliente"],
    ),
    ("database", "upsert_reembolso", 1, ["dados"]),
    ("database", "listar_pendentes_por_cnpj", 1, ["cnpj"]),
    ("database", "buscar_por_remessa", 1, ["numero_remessa"]),
    ("persistence", "salvar_resultado", 1, ["resultado"]),
    ("persistence", "salvar_quitacao_agrupada", 1, ["resultado"]),
    ("pipeline", "processar_remessa", 2, ["pasta", "remessa"]),
    ("pipeline", "processar_pasta_raiz", 1, ["pasta_raiz"]),
    ("export_excel", "exportar", 0, ["caminho_destino"]),
    ("dashboard", "mostrar_dashboard", 0, []),
]


def testar_assinaturas(importados):
    _titulo("5. ASSINATURAS DAS FUNÇÕES")

    for modulo_nome, func_nome, min_obrig, params_esperados in ASSINATURAS:
        modulo = importados.get(modulo_nome)
        if modulo is None:
            _erro(f"{modulo_nome}.{func_nome} — módulo não importado")
            continue

        func = getattr(modulo, func_nome, None)
        if func is None:
            _erro(f"{modulo_nome}.{func_nome} não existe")
            continue

        try:
            sig = inspect.signature(func)
        except (ValueError, TypeError):
            _aviso(f"{modulo_nome}.{func_nome} — assinatura não inspecionável")
            continue

        params = list(sig.parameters.values())
        obrigatorios = [
            p
            for p in params
            if p.default is inspect.Parameter.empty
            and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]

        if len(obrigatorios) < min_obrig:
            _erro(
                f"{modulo_nome}.{func_nome} espera {min_obrig} "
                f"parâmetro(s) obrigatório(s), encontrou {len(obrigatorios)}"
            )
            continue

        nomes = [p.name for p in params]
        divergentes = [n for n in params_esperados if n not in nomes]
        if divergentes:
            _aviso(
                f"{modulo_nome}.{func_nome} — parâmetros com nome diferente: "
                f"{', '.join(divergentes)} (encontrado: {', '.join(nomes)})"
            )
        else:
            _ok_msg(f"{modulo_nome}.{func_nome}{sig}")


# ---------------------------------------------------------------------------
# 6. ENUMS DO CONFIG
# ---------------------------------------------------------------------------


def testar_enums():
    _titulo("6. ENUMS DO CONFIG")

    try:
        from config import Status, TipoDoc
    except Exception as e:
        _erro("Não foi possível importar Status/TipoDoc", e)
        return

    status_esperados = [
        "AGUARDANDO",
        "CONFERIDO",
        "REVISAO",
        "DIV_CNPJ",
        "DIV_NF",
        "DIV_VALOR",
        "DIV_DATA",
        "AGRUPADO",
        "AGRUPADO_CONF",
        "DUPLICADO",
    ]
    faltando = [s for s in status_esperados if not hasattr(Status, s)]
    if faltando:
        _erro(f"Status sem atributos: {', '.join(faltando)}")
    else:
        _ok_msg(f"Status com os {len(status_esperados)} valores esperados")

    tipos_esperados = [
        "GUIA",
        "COMP_JADLOG",
        "COMP_CLIENTE",
        "PLANILHA",
        "DESCONHECIDO",
    ]
    faltando = [t for t in tipos_esperados if not hasattr(TipoDoc, t)]
    if faltando:
        _erro(f"TipoDoc sem atributos: {', '.join(faltando)}")
    else:
        _ok_msg(f"TipoDoc com os {len(tipos_esperados)} valores esperados")


# ---------------------------------------------------------------------------
# 7. TESTE FUNCIONAL DO PIPELINE (SEM ARQUIVOS REAIS)
# ---------------------------------------------------------------------------


def testar_pipeline_vazio():
    _titulo("7. PIPELINE COM PASTA VAZIA")

    try:
        from pipeline import processar_pasta_raiz
    except Exception as e:
        _erro("pipeline não importado", e)
        return

    # Pasta inexistente deve retornar erro tratado, não exceção
    try:
        r = processar_pasta_raiz(Path("__pasta_que_nao_existe__"))
        if r.get("ok") is False:
            _ok_msg("Pasta inexistente tratada corretamente")
        else:
            _aviso(f"Pasta inexistente retornou ok=True: {r}")
    except Exception as e:
        _erro("Pasta inexistente gerou exceção não tratada", e)


# ---------------------------------------------------------------------------
# RELATÓRIO FINAL
# ---------------------------------------------------------------------------


def relatorio():
    _titulo("RESULTADO")

    print(f"\n  Sucessos: {_ok}")
    print(f"  Avisos:   {_avisos}")
    print(f"  Falhas:   {_falhas}")

    if _falhas == 0:
        print("\n  Sistema pronto para uso.")
        if _avisos:
            print("  Revise os avisos acima — não impedem a execução.")
        return 0

    print("\n  Corrija as falhas acima antes de rodar o sistema.")
    return 1


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------


def main():
    print()
    print("  DIAGNÓSTICO DO SISTEMA DE CONCILIAÇÃO — JADLOG")
    print(f"  Python {sys.version.split()[0]}")
    print(f"  Diretório: {Path.cwd()}")

    importados = testar_imports()
    testar_dependencias()
    testar_pastas()
    testar_banco()
    testar_assinaturas(importados)
    testar_enums()
    testar_pipeline_vazio()

    return relatorio()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n  Interrompido pelo usuário.")
        sys.exit(1)
    except Exception:
        print("\n  ERRO INESPERADO NO DIAGNÓSTICO:")
        traceback.print_exc()
        sys.exit(1)

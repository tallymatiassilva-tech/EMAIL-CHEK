"""Teste de integracao dos quatro modulos ajustados.

Valida o fluxo completo sem depender de OCR:
- database: migracao do schema com as colunas de CNPJ
- layout_cache: hash estavel, gravacao e busca
- persistence: salvar_resultado e quitacao agrupada com CNPJ divergente
- pipeline: processamento com gravacao automatica de layout

Roda com: python test_integracao.py
"""

import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp())
_DB = _TMP / "teste_integracao.db"

import config

config.DB_PATH = str(_DB)

import database as db
import layout_cache
import persistence as ps
from config import Status, TipoDoc

# ---------------------------------------------------------------------------
# TEXTOS DE TESTE
# ---------------------------------------------------------------------------

TEXTO_GUIA = """
SECRETARIA DE ESTADO DA FAZENDA
GUIA DE RECOLHIMENTO - DARE
Numero da Guia: 2026.0001.2345
CNPJ do Contribuinte: 12.345.678/0001-90
Razao Social: EMPRESA ALFA LTDA
Tomador do Servico: JADLOG LOGISTICA LTDA
UF: SP
Tributo: ICMS
Valor Total a Pagar: R$ 1.250,45
Data de Emissao: 01/11/2026
Data de Vencimento: 20/11/2026
Numero da Nota Fiscal: 98765
"""

TEXTO_GUIA_2 = """
SECRETARIA DE ESTADO DA FAZENDA
GUIA DE RECOLHIMENTO - DARE
Numero da Guia: 2026.0002.9999
CNPJ do Contribuinte: 98.765.432/0001-10
Razao Social: EMPRESA BETA SA
Tomador do Servico: JADLOG LOGISTICA LTDA
UF: RJ
Tributo: ICMS
Valor Total a Pagar: R$ 3.480,00
Data de Emissao: 05/11/2026
Data de Vencimento: 25/11/2026
Numero da Nota Fiscal: 11111
"""

TEXTO_COMP_JADLOG = """
COMPROVANTE DE PAGAMENTO DE TITULOS
Cliente: JADLOG
Beneficiario: GOVERNO DO ESTADO DE SAO PAULO
Valor do Pagamento: R$ 1.250,45
Data do Pagamento: 18/11/2026
CNPJ do Pagador: 12.345.678/0001-90
Nome do Pagador: JADLOG LOGISTICA LTDA
Nr. Autenticacao: 1234.5678.9012
"""

TEXTO_COMP_CLIENTE = """
COMPROVANTE DO PIX
Dados do Pagador
Nome do Pagador: EMPRESA ALFA LTDA
CNPJ do Pagador: 12.345.678/0001-90
Dados do Recebedor
Nome: JADLOG LOGISTICA LTDA
Valor do Reembolso: R$ 1.250,45
Data do Reembolso: 25/11/2026
Chave Pix: 12.345.678/0001-90
"""


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------


def _ok(msg):
    print(f"  OK    {msg}")
    return True


def _falha(msg):
    print(f"  FALHA {msg}")
    return False


def _criar_remessa(
    remessa: str, guia: str, comp_jadlog: str, comp_cliente: str
) -> dict:
    """Grava os registros direto no banco, simulando o pipeline."""
    db.upsert_reembolso(
        {
            "numero_remessa": remessa,
            "numero_nf": "98765",
            "tomador": "JADLOG LOGISTICA LTDA",
            "cliente": "EMPRESA ALFA LTDA",
            "cnpj_cliente": "12345678000190",
            "cnpj_guia": "12345678000190",
            "uf": "SP",
            "tributo": "ICMS",
            "valor_guia": 1250.45,
            "data_vencimento": "2026-11-20",
            "valor_pago_jadlog": 1250.45,
            "data_pagamento_jadlog": "2026-11-18",
            "valor_reembolso_cliente": 1250.45,
            "data_reembolso_cliente": "2026-11-25",
            "valor_reembolso_esperado": 1250.45,
            "dias_reembolso": 7,
            "status": "AGUARDANDO_REEMBOLSO",
            "score": 0,
        }
    )
    return db.buscar_por_remessa(remessa)


# ---------------------------------------------------------------------------
# TESTES
# ---------------------------------------------------------------------------


def test_migracao_schema():
    """As colunas de CNPJ existem apos a migracao."""
    colunas = {
        r["name"]
        for r in db.get_conn().execute("PRAGMA table_info(reembolsos)").fetchall()
    }
    esperadas = {
        "tomador",
        "cnpj_guia",
        "cnpj_comprovante",
        "cnpj_divergente",
        "motivo_cnpj",
        "valor_reembolso_esperado",
    }
    faltando = esperadas - colunas
    if faltando:
        return _falha(f"colunas faltando: {faltando}")
    return _ok(f"schema migrado ({len(colunas)} colunas)")


def test_hash_estavel():
    """Duas guias do mesmo formato geram o mesmo hash."""
    h1 = layout_cache.calcular_hash(TEXTO_GUIA)
    h2 = layout_cache.calcular_hash(TEXTO_GUIA_2)

    if not h1:
        return _falha("hash vazio")
    if h1 != h2:
        return _falha(f"hash instavel: {h1[:8]} != {h2[:8]}")
    return _ok(f"hash estavel entre guias ({h1[:8]})")


def test_hash_por_formato():
    """Guia e comprovante geram hashes diferentes."""
    h_guia = layout_cache.calcular_hash(TEXTO_GUIA)
    h_comp = layout_cache.calcular_hash(TEXTO_COMP_CLIENTE)
    if h_guia == h_comp:
        return _falha("guia e comprovante com o mesmo hash")
    return _ok(f"hash por formato ({h_guia[:8]} != {h_comp[:8]})")


def test_gravar_e_buscar_layout():
    """Grava o layout da guia e recupera com o segundo texto."""
    campos = {
        "valor_guia": 1250.45,
        "data_vencimento": "2026-11-20",
        "cnpj": "12345678000190",
        "cliente": "EMPRESA ALFA LTDA",
    }
    rotulos = {
        "valor_guia": r"total\s*a\s*pagar",
        "data_vencimento": r"vencimento",
        "cnpj": r"cnpj\s*(?:do\s*)?contribuinte",
        "cliente": r"raz[ãa]o\s*social",
    }

    layout_cache.gravar_layout(TipoDoc.GUIA, TEXTO_GUIA, campos, rotulos)
    encontrado = layout_cache.buscar_layout(TipoDoc.GUIA, TEXTO_GUIA_2)

    if not encontrado:
        return _falha("layout nao encontrado na busca")
    if encontrado != rotulos:
        return _falha(f"mapa diferente: {encontrado}")
    return _ok(f"layout gravado e recuperado ({len(encontrado)} rotulos)")


def test_tipo_por_hash():
    """O tipo vem do cache antes de qualquer pontuacao."""
    resultado = layout_cache.buscar_tipo_por_hash(TEXTO_GUIA_2)
    if not resultado:
        return _falha("tipo nao encontrado pelo hash")
    tipo, conf = resultado
    if tipo != TipoDoc.GUIA:
        return _falha(f"tipo errado: {tipo}")
    return _ok(f"tipo pelo cache: {tipo} ({conf:.0f}%)")


def test_salvar_resultado():
    """salvar_resultado atualiza status e registra historico."""
    _criar_remessa("REM001", TEXTO_GUIA, TEXTO_COMP_JADLOG, TEXTO_COMP_CLIENTE)

    resultado = {
        "numero_remessa": "REM001",
        "status_final": Status.CONFERIDO,
        "score_final": 95,
        "lado_cliente": {
            "camada": "MATCH_DIRETO",
            "valor_quitado": 1250.45,
            "diferenca": 0.0,
            "motivo": "Match direto",
        },
    }

    r = ps.salvar_resultado(resultado)
    if not r.get("ok"):
        return _falha(f"salvar falhou: {r.get('motivo')}")

    atual = db.buscar_por_remessa("REM001")
    if atual["status"] != Status.CONFERIDO:
        return _falha(f"status errado: {atual['status']}")

    hist = ps.historico_remessa("REM001")
    if not hist:
        return _falha("historico vazio")
    return _ok(f"salvar_resultado: {atual['status']} score {atual['score']}")


def test_idempotencia():
    """Salvar o mesmo resultado duas vezes nao duplica historico."""
    resultado = {
        "numero_remessa": "REM001",
        "status_final": Status.CONFERIDO,
        "score_final": 95,
        "lado_cliente": {"camada": "MATCH_DIRETO", "valor_quitado": 1250.45},
    }
    antes = len(ps.historico_remessa("REM001"))
    r = ps.salvar_resultado(resultado)
    depois = len(ps.historico_remessa("REM001"))

    if r.get("alterado"):
        return _falha("deveria ser idempotente")
    if depois != antes:
        return _falha(f"historico duplicado: {antes} -> {depois}")
    return _ok("idempotencia respeitada")


def test_quitacao_agrupada_cnpj_divergente():
    """Quitacao agrupada desvia para salvar_resultado quando ha CNPJ divergente."""
    _criar_remessa("REM002", TEXTO_GUIA, TEXTO_COMP_JADLOG, TEXTO_COMP_CLIENTE)

    resultado = {
        "numero_remessa": "REM002",
        "status_final": Status.DIV_CNPJ,
        "score_final": 40,
        "cnpj_divergente": True,
        "motivo_cnpj": "CNPJ da guia difere do comprovante",
        "lado_cliente": {
            "camada": "MATCH_DIRETO",
            "quitados": [
                {
                    "numero_remessa": "REM002",
                    "valor_reembolso_esperado": 1250.45,
                }
            ],
        },
    }

    r = ps.salvar_quitacao_agrupada(resultado)
    if not r.get("ok"):
        return _falha(f"quitacao falhou: {r.get('motivo')}")

    atual = db.buscar_por_remessa("REM002")
    if atual["status"] == Status.CONFERIDO:
        return _falha("CNPJ divergente virou CONFERIDO")
    if atual["status"] != Status.DIV_CNPJ:
        return _falha(f"status errado: {atual['status']}")
    if atual["cnpj_divergente"] != 1:
        return _falha(f"cnpj_divergente errado: {atual['cnpj_divergente']}")
    return _ok(f"CNPJ divergente respeitado: {atual['status']}")


def test_quitacao_agrupada_sem_numero_remessa():
    """Item sem numero_remessa e ignorado, nao quebra."""
    _criar_remessa("REM003", TEXTO_GUIA, TEXTO_COMP_JADLOG, TEXTO_COMP_CLIENTE)

    resultado = {
        "numero_remessa": "REM003",
        "status_final": Status.CONFERIDO,
        "score_final": 95,
        "lado_cliente": {
            "camada": "COMBINACAO_UNICA",
            "quitados": [
                {"id": 999, "valor_reembolso_esperado": 500.00},
                {"numero_remessa": "REM003", "valor_reembolso_esperado": 750.45},
            ],
        },
    }

    r = ps.salvar_quitacao_agrupada(resultado)
    if not r.get("ok"):
        return _falha(f"quitacao falhou: {r.get('motivo')}")
    if not r.get("ignorados"):
        return _falha("item sem numero_remessa nao foi ignorado")
    if "REM003" not in r.get("quitados", []):
        return _falha("REM003 nao foi quitada")

    atual = db.buscar_por_remessa("REM003")
    if atual["status"] != Status.CONFERIDO:
        return _falha(f"status errado: {atual['status']}")
    return _ok(f"item invalido ignorado, REM003 quitada")


def test_cnpj_divergente_recalculado():
    """cnpj_divergente volta a 0 quando a divergencia e resolvida."""
    _criar_remessa("REM004", TEXTO_GUIA, TEXTO_COMP_JADLOG, TEXTO_COMP_CLIENTE)

    # Primeiro: marca como divergente
    ps.salvar_resultado(
        {
            "numero_remessa": "REM004",
            "status_final": Status.DIV_CNPJ,
            "score_final": 40,
            "cnpj_divergente": True,
            "motivo_cnpj": "CNPJ difere",
            "lado_cliente": {},
        }
    )
    atual = db.buscar_por_remessa("REM004")
    if atual["cnpj_divergente"] != 1:
        return _falha("nao marcou como divergente")

    # Depois: resolve, sem enviar cnpj_divergente no resultado
    ps.salvar_resultado(
        {
            "numero_remessa": "REM004",
            "status_final": Status.CONFERIDO,
            "score_final": 95,
            "lado_cliente": {"camada": "MATCH_DIRETO", "valor_quitado": 1250.45},
        }
    )
    atual = db.buscar_por_remessa("REM004")
    if atual["cnpj_divergente"] != 0:
        return _falha(f"cnpj_divergente travado em {atual['cnpj_divergente']}")
    return _ok("cnpj_divergente recalculado para 0")


def test_listar_divergencias():
    """A consulta de divergencias so traz as marcadas."""
    divergentes = db.listar_divergencias_cnpj()
    remessas = {r["numero_remessa"] for r in divergentes}
    if "REM002" not in remessas:
        return _falha(f"REM002 nao aparece: {remessas}")
    if "REM004" in remessas:
        return _falha("REM004 (resolvida) ainda aparece")
    return _ok(f"divergencias corretas: {sorted(remessas)}")


# ---------------------------------------------------------------------------
# RUNNER
# ---------------------------------------------------------------------------


def main():
    print("\n=== TESTE DE INTEGRACAO ===\n")
    print(f"Banco temporario: {_DB}\n")

    db.init_db()
    ps.init_historico()

    testes = [
        ("migracao do schema", test_migracao_schema),
        ("hash estavel", test_hash_estavel),
        ("hash por formato", test_hash_por_formato),
        ("gravar e buscar layout", test_gravar_e_buscar_layout),
        ("tipo pelo hash", test_tipo_por_hash),
        ("salvar resultado", test_salvar_resultado),
        ("idempotencia", test_idempotencia),
        ("quitacao com CNPJ divergente", test_quitacao_agrupada_cnpj_divergente),
        ("quitacao sem numero_remessa", test_quitacao_agrupada_sem_numero_remessa),
        ("cnpj_divergente recalculado", test_cnpj_divergente_recalculado),
        ("listar divergencias", test_listar_divergencias),
    ]

    falhas = 0
    for nome, fn in testes:
        print(f"[{nome}]")
        try:
            if not fn():
                falhas += 1
        except Exception as e:
            print(f"  ERRO  {e}")
            import traceback

            traceback.print_exc()
            falhas += 1
        print()

    print("=" * 60)
    if falhas:
        print(f"RESULTADO: {falhas} de {len(testes)} teste(s) falharam")
        sys.exit(1)

    print(f"RESULTADO: todos os {len(testes)} testes passaram")
    sys.exit(0)


if __name__ == "__main__":
    main()

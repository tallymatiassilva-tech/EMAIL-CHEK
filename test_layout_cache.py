"""Teste do cache de layouts.

Valida tres coisas:
1. O hash e estavel: dois textos com valores diferentes geram o mesmo hash.
2. O hash muda quando o formato muda.
3. Gravar e buscar devolve o mesmo mapa de rotulos.

Roda com: python test_layout_cache.py
"""

import sys
import tempfile
from pathlib import Path

# Banco temporario para nao sujar o banco real
_TMP = Path(tempfile.mkdtemp()) / "teste_layout.db"

import config

config.DB_PATH = str(_TMP)

import database as db
import layout_cache
from config import TipoDoc

GUIA_A = """
SECRETARIA DE ESTADO DA FAZENDA
GUIA DE RECOLHIMENTO - DARE
CNPJ do Contribuinte: 12.345.678/0001-90
Razao Social: EMPRESA ALFA LTDA
Periodo de Apuracao: 01/10/2026 a 31/10/2026
Valor Total a Pagar: R$ 1.250,45
Data de Vencimento: 20/11/2026
"""

GUIA_B = """
SECRETARIA DE ESTADO DA FAZENDA
GUIA DE RECOLHIMENTO - DARE
CNPJ do Contribuinte: 98.765.432/0001-10
Razao Social: EMPRESA BETA SA
Periodo de Apuracao: 01/09/2026 a 30/09/2026
Valor Total a Pagar: R$ 3.480,00
Data de Vencimento: 15/10/2026
"""

COMPROVANTE = """
COMPROVANTE DE PAGAMENTO
Cliente: JADLOG
Beneficiario: GOVERNO DO ESTADO
Valor do Documento: R$ 1.250,45
Data do Pagamento: 18/11/2026
Nr. Autenticacao: 1234.5678.9012
"""


def _ok(msg):
    print(f"  OK   {msg}")


def _falha(msg):
    print(f"  FALHA {msg}")
    return False


def test_hash_estavel():
    """Duas guias do mesmo formato, valores diferentes, mesmo hash."""
    h1 = layout_cache.calcular_hash(GUIA_A)
    h2 = layout_cache.calcular_hash(GUIA_B)

    if not h1:
        return _falha("hash da guia A veio vazio")
    if h1 != h2:
        return _falha(f"hash deveria ser igual: {h1} != {h2}")

    _ok(f"hash estavel entre guias do mesmo formato ({h1[:8]})")
    return True


def test_hash_diferente_por_formato():
    """Guia e comprovante tem formatos diferentes, hash diferente."""
    h_guia = layout_cache.calcular_hash(GUIA_A)
    h_comp = layout_cache.calcular_hash(COMPROVANTE)

    if h_guia == h_comp:
        return _falha("guia e comprovante nao deveriam ter o mesmo hash")

    _ok(f"hash diferente por formato ({h_guia[:8]} != {h_comp[:8]})")
    return True


def test_hash_vazio():
    """Texto vazio devolve hash vazio."""
    if layout_cache.calcular_hash("") != "":
        return _falha("texto vazio deveria devolver hash vazio")
    _ok("texto vazio devolve hash vazio")
    return True


def test_gravar_e_buscar():
    """Grava um layout, busca de novo e confirma que o mapa bate."""
    campos = {
        "cnpj": "12345678000190",
        "cliente": "EMPRESA ALFA LTDA",
        "valor_guia": 1250.45,
        "data_vencimento": "2026-11-20",
        "numero_nf": "",
    }
    rotulos = {
        "cnpj": r"cnpj\s*(?:do\s*)?contribuinte",
        "cliente": r"raz[ãa]o\s*social",
        "valor_guia": r"total\s*a\s*pagar",
        "data_vencimento": r"vencimento",
        "numero_nf": r"nota\s*fiscal",
    }

    layout_cache.gravar_layout(TipoDoc.GUIA, GUIA_A, campos, rotulos)

    # Busca com o MESMO formato (valores diferentes) — deve achar
    encontrado = layout_cache.buscar_layout(TipoDoc.GUIA, GUIA_B)
    if not encontrado:
        return _falha("layout gravado nao foi encontrado na busca")

    esperado = {k: v for k, v in rotulos.items() if campos.get(k)}
    if encontrado != esperado:
        return _falha(f"mapa diferente do gravado: {encontrado} != {esperado}")

    _ok(f"layout gravado e recuperado ({len(encontrado)} rotulos)")

    # numero_nf veio vazio: nao deve entrar no mapa
    if "numero_nf" in encontrado:
        return _falha("campo vazio entrou no mapa de rotulos")

    _ok("campo vazio ficou fora do mapa")
    return True


def test_busca_formato_desconhecido():
    """Formato nunca visto devolve None."""
    if layout_cache.buscar_layout(TipoDoc.COMP_CLIENTE, COMPROVANTE) is not None:
        return _falha("formato desconhecido deveria devolver None")
    _ok("formato desconhecido devolve None")
    return True


def test_taxa_sobe_e_desce():
    """Taxa sobe com extracao boa e cai com extracao fraca."""
    campos_bom = {
        "cnpj": "12345678000190",
        "cliente": "EMPRESA ALFA LTDA",
        "valor_guia": 1250.45,
        "data_vencimento": "2026-11-20",
    }
    rotulos_bom = {
        "cnpj": "cnpj contribuinte",
        "cliente": "razao social",
        "valor_guia": "total a pagar",
        "data_vencimento": "vencimento",
    }

    # Grava com extracao boa (4 campos >= MIN_CAMPOS_BOM)
    layout_cache.gravar_layout(TipoDoc.GUIA, GUIA_A, campos_bom, rotulos_bom)
    layouts = layout_cache.listar_layouts(TipoDoc.GUIA)
    if not layouts:
        return _falha("layout nao foi listado")
    taxa_inicial = layouts[0]["taxa_acerto"]
    if taxa_inicial < 100:
        return _falha(f"taxa inicial deveria ser 100, veio {taxa_inicial}")

    # Grava de novo com extracao fraca (1 campo)
    campos_fraco = {"cnpj": "12345678000190"}
    rotulos_fraco = {"cnpj": "cnpj contribuinte"}
    layout_cache.gravar_layout(TipoDoc.GUIA, GUIA_A, campos_fraco, rotulos_fraco)

    layouts = layout_cache.listar_layouts(TipoDoc.GUIA)
    taxa_depois = layouts[0]["taxa_acerto"]
    if taxa_depois >= taxa_inicial:
        return _falha(
            f"taxa deveria cair com extracao fraca: {taxa_inicial} -> {taxa_depois}"
        )

    _ok(f"taxa cai com extracao fraca ({taxa_inicial} -> {taxa_depois})")
    return True


def main():
    print("\n=== TESTE DO LAYOUT CACHE ===\n")
    print(f"Banco temporario: {_TMP}\n")

    db.init_db()

    testes = [
        ("hash estavel", test_hash_estavel),
        ("hash por formato", test_hash_diferente_por_formato),
        ("hash vazio", test_hash_vazio),
        ("gravar e buscar", test_gravar_e_buscar),
        ("formato desconhecido", test_busca_formato_desconhecido),
        ("taxa sobe e desce", test_taxa_sobe_e_desce),
    ]

    falhas = 0
    for nome, fn in testes:
        print(f"[{nome}]")
        try:
            if not fn():
                falhas += 1
        except Exception as e:
            print(f"  ERRO  {e}")
            falhas += 1
        print()

    if falhas:
        print(f"RESULTADO: {falhas} teste(s) falharam")
        sys.exit(1)

    print("RESULTADO: todos os testes passaram")
    sys.exit(0)


if __name__ == "__main__":
    main()

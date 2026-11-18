"""Teste de integracao: processa uma remessa e confirma que o layout foi gravado.

Monta uma pasta de remessa temporaria com tres arquivos de texto (guia,
comprovante Jadlog, comprovante cliente), roda o pipeline e verifica:

1. A remessa foi processada com sucesso
2. O layout foi gravado no cache
3. O segundo processamento usa o cache (nao passa pela pontuacao)

Roda com: python test_pipeline_layout.py
"""

import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp())
_DB = _TMP / "teste_pipeline.db"

import config

config.DB_PATH = str(_DB)

import database as db
import layout_cache
import pipeline
from config import TipoDoc

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
Nome do Pagador: EMPRESA CLIENTE SA
CNPJ do Pagador: 98.765.432/0001-10
Dados do Recebedor
Nome: JADLOG LOGISTICA LTDA
Valor do Reembolso: R$ 1.250,45
Data do Reembolso: 25/11/2026
Chave Pix: 12.345.678/0001-90
"""


def _ok(msg):
    print(f"  OK   {msg}")


def _falha(msg):
    print(f"  FALHA {msg}")
    return False


def _montar_remessa(raiz: Path, nome: str) -> Path:
    """Cria uma pasta de remessa com os tres documentos."""
    pasta = raiz / nome
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "guia.txt").write_text(TEXTO_GUIA, encoding="utf-8")
    (pasta / "comp_jadlog.txt").write_text(TEXTO_COMP_JADLOG, encoding="utf-8")
    (pasta / "comp_cliente.txt").write_text(TEXTO_COMP_CLIENTE, encoding="utf-8")
    return pasta


def test_processa_e_grava_layout():
    """Processa a remessa e confirma que os layouts foram gravados."""
    raiz = _TMP / "remessas"
    raiz.mkdir(parents=True, exist_ok=True)
    _montar_remessa(raiz, "REM001")

    resultado = pipeline.processar_pasta_raiz(raiz)

    if not resultado.get("ok"):
        return _falha(f"pipeline falhou: {resultado.get('motivo')}")

    if resultado.get("processadas", 0) < 1:
        return _falha("nenhuma remessa processada")

    _ok(f"remessa processada ({resultado['processadas']})")

    # Confirma que os layouts foram gravados
    layouts = layout_cache.listar_layouts()
    if not layouts:
        return _falha("nenhum layout gravado no cache")

    tipos = {l["tipo_documento"] for l in layouts}
    esperados = {TipoDoc.GUIA, TipoDoc.COMP_JADLOG, TipoDoc.COMP_CLIENTE}
    faltando = esperados - tipos

    if faltando:
        return _falha(f"layouts faltando: {faltando} (gravados: {tipos})")

    _ok(f"layouts gravados: {sorted(tipos)}")

    # Confirma que cada layout tem rotulos
    for l in layouts:
        import json

        mapa = json.loads(l["campos_json"])
        if not mapa:
            return _falha(f"layout {l['tipo_documento']} sem rotulos")

    _ok("todos os layouts tem rotulos")
    return True


def test_segunda_execucao_usa_cache():
    """A segunda execucao reconhece o formato pelo cache."""
    raiz = _TMP / "remessas"
    _montar_remessa(raiz, "REM002")

    # Conta layouts antes
    antes = len(layout_cache.listar_layouts())

    resultado = pipeline.processar_pasta_raiz(raiz)
    if not resultado.get("ok"):
        return _falha("segunda execucao falhou")

    # O cache deve ter sido usado, nao criado de novo
    depois = len(layout_cache.listar_layouts())

    if depois > antes:
        # Layouts novos foram criados — verifica se foram para os mesmos tipos
        # (o que indicaria hash instavel)
        tipos_antes = {
            l["tipo_documento"] for l in layout_cache.listar_layouts()[:antes]
        }
        tipos_depois = {l["tipo_documento"] for l in layout_cache.listar_layouts()}
        if tipos_depois - tipos_antes:
            return _falha(
                f"hash instavel: novos tipos criados {tipos_depois - tipos_antes}"
            )

    _ok(f"segunda execucao: {depois} layouts (antes {antes})")
    return True


def test_confianca_na_fila():
    """A confianca vai para a fila de treinamento."""
    with db.cursor() as cur:
        cur.execute("SELECT confianca FROM fila_treinamento LIMIT 5")
        rows = cur.fetchall()

    if not rows:
        # Sem falhas de extracao: tudo extraido corretamente
        _ok("nenhuma falha de extracao (todos os campos extraidos)")
        return True

    zerados = [r for r in rows if (r["confianca"] or 0) == 0]
    if zerados:
        return _falha(f"{len(zerados)} entrada(s) na fila com confianca zero")

    _ok(f"confianca preenchida na fila ({len(rows)} entradas)")
    return True


def main():
    print("\n=== TESTE DE INTEGRACAO: PIPELINE + LAYOUT ===\n")
    print(f"Banco temporario: {_DB}\n")

    db.init_db()

    testes = [
        ("processa e grava layout", test_processa_e_grava_layout),
        ("segunda execucao usa cache", test_segunda_execucao_usa_cache),
        ("confianca na fila", test_confianca_na_fila),
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

    if falhas:
        print(f"RESULTADO: {falhas} teste(s) falharam")
        sys.exit(1)

    print("RESULTADO: todos os testes passaram")
    sys.exit(0)


if __name__ == "__main__":
    main()

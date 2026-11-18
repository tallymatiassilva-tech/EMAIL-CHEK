"""Teste da extracao de data nos tres formatos.

Valida que o pdf_reader aceita:
1. Numerico: 21/08/2026, 21-08-2026, 21.08.2026
2. Por extenso: 21/agosto/2026, 9/setembro/2026
3. ISO: 2026-08-21

E sempre devolve dd/mm/yyyy, que e o formato da planilha.

Dois caminhos sao testados:
- _data_apos_rotulo: quando ha um rotulo antes da data
- _data_fallback: quando nao ha rotulo

Roda com: python test_data_formatos.py
"""

import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp())

import config

config.DB_PATH = str(_TMP / "teste_data.db")

import database as db
import pdf_reader


def _ok(msg):
    print(f"  OK    {msg}")
    return True


def _falha(msg):
    print(f"  FALHA {msg}")
    return False


def _testar_com_rotulo(texto: str, esperado: str, rotulo: str) -> bool:
    data, _ = pdf_reader._data_apos_rotulo(texto, [r"data"])
    if data != esperado:
        return _falha(f"{rotulo}: esperado {esperado}, obtido {data}")
    return _ok(f"{rotulo}: {data}")


def _testar_fallback(texto: str, esperado: str, rotulo: str) -> bool:
    data = pdf_reader._data_fallback(texto)
    if data != esperado:
        return _falha(f"{rotulo}: esperado {esperado}, obtido {data}")
    return _ok(f"{rotulo}: {data}")


# --- NUMERICO (com rotulo) ---


def test_numerico_barra():
    return _testar_com_rotulo(
        "Data do pagamento: 21/08/2026", "21/08/2026", "numerico barra"
    )


def test_numerico_hifen():
    return _testar_com_rotulo(
        "Data do pagamento: 21-08-2026", "21/08/2026", "numerico hifen"
    )


def test_numerico_ponto():
    return _testar_com_rotulo(
        "Data do pagamento: 21.08.2026", "21/08/2026", "numerico ponto"
    )


# --- EXTENSO (com rotulo) ---


def test_extenso_marco_acento():
    return _testar_com_rotulo(
        "Data: 15/março/2026", "15/03/2026", "extenso marco acento"
    )


def test_extenso_marco_sem_acento():
    return _testar_com_rotulo("Data: 15/marco/2026", "15/03/2026", "extenso marco")


def test_extenso_agosto_com_rotulo():
    return _testar_com_rotulo(
        "Data do reembolso: 21/agosto/2026", "21/08/2026", "extenso agosto rotulo"
    )


# --- EXTENSO (sem rotulo, via fallback) ---


def test_extenso_agosto_fallback():
    texto = "Comprovante de Pix\n21/agosto/2026 as 12:34:40"
    return _testar_fallback(texto, "21/08/2026", "extenso agosto fallback")


def test_extenso_setembro_fallback():
    texto = "Comprovante de Pix\n9/setembro/2026 as 10:49:42"
    return _testar_fallback(texto, "09/09/2026", "extenso setembro fallback")


def test_extenso_outubro_fallback():
    texto = "Comprovante de Pix\n17/outubro/2026 as 16:35:37"
    return _testar_fallback(texto, "17/10/2026", "extenso outubro fallback")


# --- ISO ---


def test_iso_fallback():
    texto = "Data do pagamento: 2026-08-21"
    return _testar_fallback(texto, "21/08/2026", "ISO fallback")


def test_iso_sem_rotulo():
    texto = "Comprovante\n2026-08-21 12:34:40"
    return _testar_fallback(texto, "21/08/2026", "ISO sem rotulo")


# --- BORDAS ---


def test_mes_invalido():
    data, _ = pdf_reader._data_apos_rotulo("Data: 21/xyzembro/2026", [r"data"])
    if data is not None:
        return _falha(f"mes invalido deveria ser None, obtido {data}")
    return _ok("mes invalido ignorado")


def test_dia_um_digito():
    return _testar_com_rotulo("Data: 5/agosto/2026", "05/08/2026", "dia um digito")


def test_ano_fora_do_intervalo():
    data = pdf_reader._data_fallback("Comprovante\n21/08/1995")
    if data is not None:
        return _falha(f"ano 1995 deveria ser ignorado, obtido {data}")
    return _ok("ano fora do intervalo ignorado")


def test_texto_vazio():
    data = pdf_reader._data_fallback("")
    if data is not None:
        return _falha(f"texto vazio deveria ser None, obtido {data}")
    return _ok("texto vazio retorna None")


def test_sem_data():
    data = pdf_reader._data_fallback("Comprovante sem nenhuma data")
    if data is not None:
        return _falha(f"texto sem data deveria ser None, obtido {data}")
    return _ok("texto sem data retorna None")


def test_preferencia_extenso():
    texto = "Comprovante\n21/agosto/2026 e depois 15/09/2026"
    data = pdf_reader._data_fallback(texto)
    if data != "21/08/2026":
        return _falha(f"esperado 21/08/2026 (extenso), obtido {data}")
    return _ok(f"extenso tem prioridade: {data}")


def main():
    print("\n=== TESTE DE EXTRACAO DE DATA ===\n")
    print(f"Banco temporario: {_TMP / 'teste_data.db'}\n")

    db.init_db()

    testes = [
        ("numerico barra", test_numerico_barra),
        ("numerico hifen", test_numerico_hifen),
        ("numerico ponto", test_numerico_ponto),
        ("extenso marco acento", test_extenso_marco_acento),
        ("extenso marco", test_extenso_marco_sem_acento),
        ("extenso agosto rotulo", test_extenso_agosto_com_rotulo),
        ("extenso agosto fallback", test_extenso_agosto_fallback),
        ("extenso setembro fallback", test_extenso_setembro_fallback),
        ("extenso outubro fallback", test_extenso_outubro_fallback),
        ("ISO fallback", test_iso_fallback),
        ("ISO sem rotulo", test_iso_sem_rotulo),
        ("mes invalido", test_mes_invalido),
        ("dia um digito", test_dia_um_digito),
        ("ano fora do intervalo", test_ano_fora_do_intervalo),
        ("texto vazio", test_texto_vazio),
        ("sem data", test_sem_data),
        ("preferencia extenso", test_preferencia_extenso),
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

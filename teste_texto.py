"""Mostra o texto extraído de cada PDF de uma remessa."""

from pathlib import Path

from ocr_engine import extrair_texto

pasta = Path("data/Reembolsos/10084206883004")

for arq in sorted(pasta.iterdir()):
    if arq.suffix.lower() != ".pdf":
        continue
    texto = extrair_texto(arq)
    print("=" * 70)
    print(arq.name)
    print("=" * 70)
    print(texto[:800])
    print()

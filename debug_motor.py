"""Mostra o que o motor recebe em cada documento."""

from pathlib import Path

import database as db
from config import TipoDoc
from document_classifier import classificar
from pdf_reader import extrair_por_tipo

db.init_db()

pasta = Path("data/Reembolsos/10084206883004")

for arq in sorted(pasta.iterdir()):
    if not arq.is_file() or arq.suffix.lower() not in {".pdf", ".jpg", ".jpeg", ".png"}:
        continue
    if arq.stem.lower().startswith(("image", "img", "logo")):
        continue
    tipo, conf = classificar(arq)
    if tipo == TipoDoc.DESCONHECIDO:
        continue
    campos = extrair_por_tipo(arq, tipo)
    print()
    print("=" * 70)
    print(f"{arq.name} -> {tipo} ({conf:.0f}%)")
    print("=" * 70)
    for k, v in campos.items():
        print(f"  {k}: {v!r}")
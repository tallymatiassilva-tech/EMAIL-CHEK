from pathlib import Path

import ocr_engine

for nome in ("unnamed.png", "unnamed_1.png"):
    caminho = Path("anexos/18131502322673") / nome
    if not caminho.is_file():
        # tenta achar em qualquer subpasta
        achados = list(Path(".").rglob(nome))
        if not achados:
            print(f"{nome}: nao encontrado")
            continue
        caminho = achados[0]

    ocr_engine._CACHE.clear()
    texto = ocr_engine.extrair_texto(caminho)
    print(f"\n{'=' * 60}")
    print(f"{nome} — {len(texto)} chars")
    print("=" * 60)
    print(texto)

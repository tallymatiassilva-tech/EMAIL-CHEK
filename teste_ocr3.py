"""Teste de OCR com tessdata-dir sem aspas."""

import pytesseract
from PIL import Image

from config import TESSDATA_DIR, TESSERACT_CMD, TESSERACT_LANG

pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

# SEM aspas — o caminho não tem espaços
CONFIG = f"--tessdata-dir {TESSDATA_DIR}"

print("CONFIG:", CONFIG)
print()

print("--- Idiomas ---")
print(pytesseract.get_languages(config=CONFIG))

print("\n--- OCR imagem limpa ---")
try:
    texto = pytesseract.image_to_string(
        Image.open("C:/reembolsos-jadlog/teste_simples.png"),
        lang=TESSERACT_LANG,
        config=CONFIG,
    )
    print("Texto:", repr(texto))
except Exception as e:
    print("ERRO:", e)

print("\n--- OCR image.png do e-mail ---")
try:
    texto = pytesseract.image_to_string(
        Image.open("C:/reembolsos-jadlog/data/Reembolsos/10084206883004/image.png"),
        lang=TESSERACT_LANG,
        config=CONFIG,
    )
    print("Texto:", repr(texto[:300]))
except Exception as e:
    print("ERRO:", e)

"""Teste de OCR com imagem gerada localmente."""

import pytesseract
from PIL import Image

from config import TESSDATA_DIR, TESSERACT_CMD, TESSERACT_LANG

pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
CONFIG = f'--tessdata-dir "{TESSDATA_DIR}"'

print("CMD:     ", TESSERACT_CMD)
print("TESSDATA:", TESSDATA_DIR)
print("LANG:    ", TESSERACT_LANG)
print()

# 1) Lista idiomas
print("--- Idiomas ---")
try:
    print(pytesseract.get_languages(config=CONFIG))
except Exception as e:
    print("ERRO:", e)

# 2) OCR na imagem gerada localmente
print("\n--- OCR imagem limpa ---")
try:
    texto = pytesseract.image_to_string(
        Image.open("C:/reembolsos-jadlog/teste_simples.png"),
        lang=TESSERACT_LANG,
        config=CONFIG,
    )
    print("Texto:", repr(texto))
except Exception as e:
    print("ERRO:", type(e).__name__, e)

# 3) Inspeciona a imagem do e-mail
print("\n--- Inspeção image.png do e-mail ---")
alvo = "C:/reembolsos-jadlog/data/Reembolsos/10084206883004/image.png"
try:
    img = Image.open(alvo)
    print("Formato:", img.format)
    print("Modo:   ", img.mode)
    print("Tamanho:", img.size)
    print("Info:   ", img.info)
except Exception as e:
    print("ERRO:", type(e).__name__, e)

# 4) OCR na imagem do e-mail
print("\n--- OCR image.png do e-mail ---")
try:
    texto = pytesseract.image_to_string(
        Image.open(alvo),
        lang=TESSERACT_LANG,
        config=CONFIG,
    )
    print("Texto:", repr(texto[:300]))
except Exception as e:
    print("ERRO:", type(e).__name__, e)

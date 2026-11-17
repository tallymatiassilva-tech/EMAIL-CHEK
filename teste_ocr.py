"""Teste isolado de OCR com o Tesseract portátil."""

from pathlib import Path

import pytesseract
from config import TESSDATA_DIR, TESSERACT_CMD, TESSERACT_LANG
from PIL import Image

print("CMD:     ", TESSERACT_CMD)
print("TESSDATA:", TESSDATA_DIR)
print("LANG:    ", TESSERACT_LANG)
print()

pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

# 1) Sem --tessdata-dir (deve falhar)
print("--- SEM tessdata-dir ---")
try:
    langs = pytesseract.get_languages()
    print("Idiomas:", langs)
except Exception as e:
    print("ERRO:", e)

# 2) Com --tessdata-dir (deve funcionar)
print("\n--- COM tessdata-dir ---")
try:
    langs = pytesseract.get_languages(config=f'--tessdata-dir "{TESSDATA_DIR}"')
    print("Idiomas:", langs)
except Exception as e:
    print("ERRO:", e)

# 3) OCR real numa imagem
alvo = Path("data/Reembolsos")
imagens = sorted(alvo.rglob("*.png")) if alvo.is_dir() else []

if imagens:
    img = imagens[0]
    print(f"\n--- OCR em {img.name} ---")
    try:
        texto = pytesseract.image_to_string(
            Image.open(img),
            lang=TESSERACT_LANG,
            config=f'--tessdata-dir "{TESSDATA_DIR}"',
        )
        print("Texto:", repr(texto[:200]))
    except Exception as e:
        print("ERRO:", e)

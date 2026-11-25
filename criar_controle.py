import time
from pathlib import Path
from config import PASTA_ANEXOS_PADRAO

raiz = Path(PASTA_ANEXOS_PADRAO)
maior = 0.0
for sub in raiz.iterdir():
    if not sub.is_dir():
        continue
    for arq in sub.iterdir():
        if arq.is_file():
            try:
                m = arq.stat().st_mtime
                if m > maior:
                    maior = m
            except OSError:
                pass

(raiz / ".ultimo_processamento").write_text(str(maior))
print(f"Controle criado: {time.strftime('%d/%m/%Y %H:%M:%S', time.localtime(maior))}")

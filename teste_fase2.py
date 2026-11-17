from ocr_engine import extrair_texto, hash_do_documento
from utils import (
    dias_entre,
    formatar_cnpj,
    hash_layout,
    normalizar_cnpj,
    parse_data,
    parse_valor,
    similaridade,
    validar_cnpj,
)

print("--- CNPJ ---")
print(normalizar_cnpj("12.345.678/0001-99"))
print(validar_cnpj("12.345.678/0001-99"))
print(formatar_cnpj("12345678000199"))

print("\n--- Valores ---")
for v in ["R$ 1.250,45", "1,250.45", "1250.45", "R$ 0,01"]:
    print(f"{v} -> {parse_valor(v)}")

print("\n--- Datas ---")
for d in ["01/11/2026", "2026-11-01", "01.11.2026"]:
    print(f"{d} -> {parse_data(d)}")
print("Dias:", dias_entre("2026-11-01", "2026-11-10"))

print("\n--- Similaridade ---")
print(similaridade("JADLOG LOGISTICA LTDA", "JADLOG LOGÍSTICA LTDA"))

print("\n--- Hash de layout ---")
t1 = "DARE PR\nValor: 1250,00\nVencimento: 10/11/2026"
t2 = "DARE PR\nValor: 980,50\nVencimento: 15/11/2026"
print("Hash 1:", hash_layout(t1))
print("Hash 2:", hash_layout(t2))
print("Iguais (mesmo layout):", hash_layout(t1) == hash_layout(t2))

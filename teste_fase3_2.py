from config import TipoDoc
from pdf_reader import _extrair_nf, _primeiro_cnpj, extrair_por_tipo

print("=== Extração de NF pela seção correta ===")
texto_guia = """
DARE - PR
Guia de Recolhimento
Número da Guia: 1234567890
Valor Total: R$ 1.250,45
Emissão: 01/11/2026
Vencimento: 10/11/2026
CNPJ: 04.884.082/0001-35
UF: PR
Notas Fiscais
NF: 305323
Chave de acesso: 41250104884082000135550010000305323123456789
"""
nf = _extrair_nf(texto_guia)
cnpj = _primeiro_cnpj(texto_guia)
print("  NF extraída:", nf)
print("  CNPJ extraído:", cnpj)
print("  NF correta (305323)?", nf == "305323")
print("  CNPJ correto?", cnpj == "04884082000135")

print("\n=== NF sem seção explícita (fallback) ===")
texto_sem_secao = "DARE PR\nNota Fiscal: 998877\nValor: 500,00\nVencimento: 15/11/2026"
print("  NF extraída:", _extrair_nf(texto_sem_secao))

print("\n=== NF inexistente ===")
texto_sem_nf = "DARE PR\nValor: 500,00\nVencimento: 15/11/2026"
print("  NF extraída:", repr(_extrair_nf(texto_sem_nf)))

print("\n=== Despacho por tipo ===")
print("  GUIA ->", extrair_por_tipo.__name__)
print("  TipoDoc.GUIA =", TipoDoc.GUIA)
print("  TipoDoc.COMP_JADLOG =", TipoDoc.COMP_JADLOG)
print("  TipoDoc.COMP_CLIENTE =", TipoDoc.COMP_CLIENTE)

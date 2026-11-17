from pathlib import Path

import pandas as pd
from document_classifier import classificar
from excel_reader import extrair_linhas_planilha, ler_planilha_bruta, mapear_colunas

print("=== Classificador por conteúdo ===")
textos = {
    "arquivo_qualquer.pdf": (
        "DARE PR\nGuia de Recolhimento\nValor: 1250,00\n"
        "Vencimento: 10/11/2026\nChave de acesso: 41250123456789012345678901234567890123456789"
    ),
    "scan001.pdf": (
        "Comprovante de Pagamento\nPagador: JADLOG LOGISTICA LTDA\n"
        "Beneficiário: SECRETARIA DA FAZENDA\nAutenticação: 123456789\nBanco: ITAU"
    ),
    "doc_final_v3.pdf": (
        "Comprovante PIX\nPagador: CLIENTE TESTE LTDA\nValor: 1250,00\n"
        "Data: 05/11/2026\nID: E123456789012345678901234"
    ),
}
for nome, texto in textos.items():
    tipo, conf = classificar(Path(nome), texto=texto)
    print(f"  {nome}: {tipo} ({conf}%)")

print("\n=== Mapeamento de colunas (nomes variados) ===")
df = pd.DataFrame(
    {
        "Nr. Remessa": ["18249200526787"],
        "Nº NF": ["305323"],
        "Valor Reembolso": ["1250,00"],
        "Cliente": ["CLIENTE A"],
        "CNPJ": ["04.884.082/0001-35"],
    }
)
print("  Mapa:", mapear_colunas(df))

print("\n=== Extração de planilha ===")
df2 = pd.DataFrame(
    {
        "Nr. Remessa": ["18249200526787", "18249200526788"],
        "Nº NF": ["305323", "305324"],
        "Valor Reembolso": ["1250,00", "980,50"],
        "Cliente": ["CLIENTE A", "CLIENTE B"],
        "CNPJ": ["04.884.082/0001-35", "12.345.678/0001-99"],
    }
)
df2.to_excel("planilha_teste.xlsx", index=False)
for linha in extrair_linhas_planilha(Path("planilha_teste.xlsx")):
    print(" ", linha)

print("\n=== Planilha sem coluna de remessa ===")
df3 = pd.DataFrame({"Foo": ["a"], "Bar": ["b"]})
df3.to_excel("planilha_sem_remessa.xlsx", index=False)
print("  Resultado:", extrair_linhas_planilha(Path("planilha_sem_remessa.xlsx")))

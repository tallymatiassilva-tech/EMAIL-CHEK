from pathlib import Path

from outlook_reader import (
    OutlookIndisponivel,
    _extrair_remessa,
    escolher_pasta,
    listar_emails_reembolso,
    listar_pastas,
    validar_pasta_destino,
)

print("=== Extração de remessa do assunto ===")
for a in [
    "REEMBOLSO DE RETENÇÃO FISCAL - 18249200526787",
    "REEMBOLSO DE RETENCAO FISCAL: 18249200526788",
    "Outro assunto qualquer",
]:
    print(f"  {a[:50]:<52} -> {_extrair_remessa(a)}")

print("\n=== Pastas de leitura disponíveis ===")
try:
    pastas = listar_pastas()
    print(f"  {len(pastas)} pastas encontradas")
    p = escolher_pasta(pastas, "REEMBOLSO")
    print(f"  Pasta de leitura escolhida: {p['caminho'] if p else 'nenhuma'}")
except OutlookIndisponivel as e:
    print(f"  Outlook indisponível: {e}")
    p = None

print("\n=== Validação de pasta de destino ===")
for caminho in [r"data\Reembolsos", r"C:\Temp\anexos_jadlog", r"Z:\invalido\teste"]:
    try:
        d = validar_pasta_destino(caminho)
        print(f"  OK   {caminho} -> {d}")
    except ValueError as e:
        print(f"  ERRO {caminho} -> {e}")

if p:
    print(f"\n=== Prévia: e-mails em '{p['caminho']}' ===")
    emails = listar_emails_reembolso(p["caminho"])
    print(f"  {len(emails)} e-mails com assunto de reembolso")
    for e in emails[:5]:
        print(f"    {e['remessa']} | {e['data_email']} | {e['assunto'][:60]}")

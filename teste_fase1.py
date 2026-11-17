from database import buscar_por_remessa, init_db, listar, upsert_reembolso
from log_manager import get_logger

log = get_logger("teste")

init_db()
log.info("Banco inicializado")

upsert_reembolso(
    {
        "numero_remessa": "18249200526787",
        "numero_nf": "305323",
        "cliente": "CLIENTE TESTE LTDA",
        "cnpj_cliente": "12345678000199",
        "uf": "PR",
        "tributo": "DARE",
        "valor_guia": 1250.00,
        "valor_pago_jadlog": 1250.00,
        "data_pagamento_jadlog": "2026-11-01",
        "status": "AGUARDANDO_REEMBOLSO",
    }
)

registro = buscar_por_remessa("18249200526787")
print("Registro:", registro)
print("Total na base:", len(listar()))

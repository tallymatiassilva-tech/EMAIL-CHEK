from conciliation_engine import (
    calcular_score,
    conciliar_pagamento_jadlog,
    conciliar_reembolso_cliente,
    conciliar_remessa,
)
from config import Status

print("=== LADO JADLOG: 1 comprovante = 1 guia ===")
guia = {
    "numero_remessa": "18249200526787",
    "numero_nf": "305323",
    "valor_guia": 1250.00,
    "cnpj": "04884082000135",
    "cliente": "JADLOG LOGISTICA LTDA",
}
for vp in (1250.00, 1250.01, 1000.00, 1300.00):
    r = conciliar_pagamento_jadlog(guia, {"valor_pago_jadlog": vp})
    print(f"  Pago R$ {vp:>8.2f} -> {r['status']} (score {r['score']}) | {r['motivo']}")

print("\n=== LADO CLIENTE: Opção C (conservadora) ===")
pendentes = [
    {
        "id": 1,
        "valor_reembolso_esperado": 300.00,
        "data_pagamento_jadlog": "2026-10-01",
    },
    {
        "id": 2,
        "valor_reembolso_esperado": 450.00,
        "data_pagamento_jadlog": "2026-10-05",
    },
    {
        "id": 3,
        "valor_reembolso_esperado": 200.00,
        "data_pagamento_jadlog": "2026-10-10",
    },
    {
        "id": 4,
        "valor_reembolso_esperado": 600.00,
        "data_pagamento_jadlog": "2026-10-15",
    },
    {
        "id": 5,
        "valor_reembolso_esperado": 150.00,
        "data_pagamento_jadlog": "2026-10-20",
    },
]

casos = [
    ("Match direto único", 200.00),
    ("Ambíguo (2 combinações)", 950.00),
    ("Ambíguo (600 sozinho vs 450+150)", 600.00),
    ("Ambíguo (750 = 300+450 vs 600+150)", 750.00),
    ("Parcial (sem combinação)", 137.00),
    ("Divergente (excede)", 2000.00),
]
for nome, valor in casos:
    r = conciliar_reembolso_cliente(pendentes, {"valor_reembolso": valor})
    ids = [x["id"] for x in r["quitados"]]
    print(
        f"  [{nome}] R$ {valor:.2f} -> {r['status']} | {r['camada']} | quitados {ids}"
    )

print("\n  [Ambiguidade real] R$ 750,00 com 300+450 e 750 sozinho")
pend_amb = [
    {
        "id": 1,
        "valor_reembolso_esperado": 300.00,
        "data_pagamento_jadlog": "2026-10-01",
    },
    {
        "id": 2,
        "valor_reembolso_esperado": 450.00,
        "data_pagamento_jadlog": "2026-10-05",
    },
    {
        "id": 3,
        "valor_reembolso_esperado": 750.00,
        "data_pagamento_jadlog": "2026-10-10",
    },
]
r = conciliar_reembolso_cliente(pend_amb, {"valor_reembolso": 750.00})
print(f"    -> {r['status']} | {r['camada']} | {r['motivo']}")

print("\n=== SCORE POR CRITÉRIO ===")
comp_j = {"valor_pago_jadlog": 1250.00, "data_pagamento_jadlog": "2026-11-01"}
comp_c = {
    "valor_reembolso": 1250.00,
    "cnpj_pagador": "04884082000135",
    "nome_pagador": "JADLOG LOGISTICA LTDA",
    "data_reembolso": "2026-11-05",
}
score, det = calcular_score(guia, comp_j, comp_c)
print("  Detalhe:", det)
print("  Score total:", score)

print("\n=== CONSOLIDADO ===")
res = conciliar_remessa(
    guia,
    comp_j,
    comp_c,
    [
        {
            "id": 1,
            "valor_reembolso_esperado": 1250.00,
            "data_pagamento_jadlog": "2026-11-01",
        }
    ],
)
print("  Status final:", res["status_final"])
print("  Score final:", res["score_final"])
print(
    "  Jadlog:",
    res["lado_jadlog"]["status"],
    "| Cliente:",
    res["lado_cliente"]["status"],
)

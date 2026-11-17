from pathlib import Path

import database as db
import persistence as ps
from config import Status

# Garante schema atualizado
db.init_db()
ps.init_historico()

print("=== Preparando dados de teste ===")
# Limpa registros de teste anteriores
with db.cursor() as cur:
    cur.execute("DELETE FROM reembolsos WHERE numero_remessa LIKE 'TESTE%'")
    cur.execute("DELETE FROM historico_conciliacao WHERE numero_remessa LIKE 'TESTE%'")

# Insere 3 reembolsos de teste
for i, (rem, valor) in enumerate(
    [
        ("TESTE001", 1250.00),
        ("TESTE002", 450.00),
        ("TESTE003", 200.00),
    ],
    start=1,
):
    db.upsert_reembolso(
        {
            "numero_remessa": rem,
            "numero_nf": f"30532{i}",
            "cliente": "CLIENTE TESTE LTDA",
            "cnpj_cliente": "04884082000135",
            "valor_guia": valor,
            "valor_pago_jadlog": valor,
            "status": Status.AGUARDANDO,
            "score": 0,
        }
    )
print("  3 reembolsos inseridos")

print("\n=== Gravação de resultado individual ===")
resultado = {
    "numero_remessa": "TESTE001",
    "status_final": Status.CONFERIDO,
    "score_final": 100,
    "lado_cliente": {
        "camada": "MATCH_DIRETO",
        "valor_quitado": 1250.00,
        "valor_comprovante": 1250.00,
        "diferenca": 0.0,
        "motivo": "Match direto: um reembolso com valor idêntico",
        "quitados": [],
    },
}
r = ps.salvar_resultado(resultado)
print("  Resultado:", r)

print("\n=== Idempotência: gravar o mesmo resultado de novo ===")
r2 = ps.salvar_resultado(resultado)
print("  Resultado:", r2)

print("\n=== Gravação de quitação agrupada ===")
resultado_agrup = {
    "numero_remessa": "TESTE002",
    "status_final": Status.CONFERIDO,
    "score_final": 95,
    "lado_cliente": {
        "camada": "COMBINACAO_UNICA",
        "valor_quitado": 650.00,
        "valor_comprovante": 650.00,
        "diferenca": 0.0,
        "motivo": "Combinação única de 2 reembolsos",
        "quitados": [
            {"numero_remessa": "TESTE002", "valor_reembolso_esperado": 450.00},
            {"numero_remessa": "TESTE003", "valor_reembolso_esperado": 200.00},
        ],
    },
}
r3 = ps.salvar_quitacao_agrupada(resultado_agrup)
print("  Resultado:", r3)

print("\n=== Histórico da remessa TESTE001 ===")
for h in ps.historico_remessa("TESTE001"):
    print(
        f"  {h['criado_em']} | {h['status_anterior']} -> {h['status_novo']} | score {h['score_novo']}"
    )

print("\n=== Histórico recente (últimos 5) ===")
for h in ps.historico_recente(5):
    print(f"  {h['numero_remessa']}: {h['status_anterior']} -> {h['status_novo']}")

print("\n=== Estatísticas por status ===")
print(" ", ps.estatisticas_por_status())

print("\n=== Estatísticas por camada ===")
print(" ", ps.estatisticas_por_camada())

print("\n=== Estado final dos reembolsos de teste ===")
for rem in ("TESTE001", "TESTE002", "TESTE003"):
    r = db.buscar_por_remessa(rem)
    print(
        f"  {rem}: {r['status']} | score {r['score']} | reembolso {r['valor_reembolso_cliente']}"
    )

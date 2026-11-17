"""Motor de conciliação: casa guias, pagamentos Jadlog e reembolsos do cliente.

Regras de negócio:
- Lado Jadlog: 1 comprovante = 1 guia (comparação direta de valor).
- Lado cliente: 1 comprovante pode cobrir N reembolsos.
- Cascata: match direto -> combinação única -> FIFO -> revisão manual.
- Tolerância de valor: R$ 0,01.
- Cliente paga menos -> PARCIAL com saldo pendente.
- Cliente paga mais -> DIVERGENTE para revisão manual.
- Score por critério: valor, cnpj, cliente, nf, data (pesos do config).
"""

from itertools import combinations

from config import MAX_COMBINACOES, SCORE_PESOS, TOLERANCIA_VALOR, Status
from log_manager import get_logger
from utils import normalizar_cnpj, similaridade

log = get_logger("conciliation")

# Camadas de resolução (internas do motor)
CAMADA_DIRETA = "MATCH_DIRETO"
CAMADA_UNICA = "COMBINACAO_UNICA"
CAMADA_FIFO = "FIFO"
CAMADA_MANUAL = "REVISAO_MANUAL"


def _igual(a: float, b: float, tol: float = TOLERANCIA_VALOR) -> bool:
    return abs((a or 0) - (b or 0)) <= tol


def _soma(valores: list[float]) -> float:
    return round(sum(valores), 2)


# ---------------------------------------------------------------------------
# SCORE POR CRITÉRIO
# ---------------------------------------------------------------------------


def calcular_score(
    guia: dict, comp_jadlog: dict | None, comp_cliente: dict | None
) -> tuple[int, dict]:
    """Calcula score 0-100 pelos pesos do config e devolve detalhamento."""
    detalhe = {"valor": 0, "cnpj": 0, "cliente": 0, "nf": 0, "data": 0}

    vg = guia.get("valor_guia")
    vj = (comp_jadlog or {}).get("valor_pago_jadlog")
    vc = (comp_cliente or {}).get("valor_reembolso")

    # Valor: guia == pagamento Jadlog == reembolso cliente
    if vg is not None and vj is not None and _igual(vg, vj):
        detalhe["valor"] = SCORE_PESOS["valor"]
        if vc is not None and _igual(vg, vc):
            pass  # mantém pontuação cheia
        elif vc is not None:
            detalhe["valor"] = int(SCORE_PESOS["valor"] * 0.6)

    # CNPJ: guia == CNPJ do comprovante do cliente
    cg = normalizar_cnpj(guia.get("cnpj") or "")
    cc = normalizar_cnpj((comp_cliente or {}).get("cnpj_pagador") or "")
    if cg and cc and cg == cc:
        detalhe["cnpj"] = SCORE_PESOS["cnpj"]

    # Cliente: nome similar
    ng = (guia.get("cliente") or "").strip()
    nc = ((comp_cliente or {}).get("nome_pagador") or "").strip()
    if ng and nc and similaridade(ng, nc) >= 85:
        detalhe["cliente"] = SCORE_PESOS["cliente"]

    # NF: presente na guia
    if guia.get("numero_nf"):
        detalhe["nf"] = SCORE_PESOS["nf"]

    # Data: reembolso >= pagamento Jadlog
    dj = (comp_jadlog or {}).get("data_pagamento_jadlog")
    dc = (comp_cliente or {}).get("data_reembolso")
    if dj and dc and dc >= dj:
        detalhe["data"] = SCORE_PESOS["data"]

    return sum(detalhe.values()), detalhe


# ---------------------------------------------------------------------------
# LADO JADLOG: 1 comprovante = 1 guia
# ---------------------------------------------------------------------------


def conciliar_pagamento_jadlog(guia: dict, comprovante: dict) -> dict:
    """Compara valor da guia com valor pago pela Jadlog."""
    vg = guia.get("valor_guia")
    vp = comprovante.get("valor_pago_jadlog")

    if vg is None or vp is None:
        return {
            "status": Status.REVISAO,
            "score": 0,
            "diferenca": None,
            "motivo": "Valor ausente na guia ou no comprovante",
        }

    if _igual(vg, vp):
        return {
            "status": Status.CONFERIDO,
            "score": 100,
            "diferenca": round(vp - vg, 2),
            "motivo": "Valor da guia confere com o pagamento da Jadlog",
        }

    dif = round(vp - vg, 2)
    if dif < 0:
        return {
            "status": Status.DIV_VALOR,
            "score": 60,
            "diferenca": dif,
            "motivo": f"Jadlog pagou R$ {abs(dif):.2f} a menos que a guia",
        }
    return {
        "status": Status.DIV_VALOR,
        "score": 30,
        "diferenca": dif,
        "motivo": f"Jadlog pagou R$ {dif:.2f} a mais que a guia",
    }


# ---------------------------------------------------------------------------
# LADO CLIENTE: 1 comprovante pode cobrir N reembolsos
# ---------------------------------------------------------------------------


def _combinacoes_validas(
    pendentes: list[dict], alvo: float, tol: float = TOLERANCIA_VALOR
) -> list[list[dict]]:
    """Retorna todas as combinações de reembolsos que fecham o valor alvo."""
    validas = []
    n = len(pendentes)
    for tamanho in range(1, n + 1):
        for combo in combinations(pendentes, tamanho):
            valores = [r.get("valor_reembolso_esperado") or 0 for r in combo]
            if _igual(_soma(valores), alvo, tol):
                validas.append(list(combo))
                if len(validas) > MAX_COMBINACOES:
                    log.warning("Limite de combinações atingido (%d)", MAX_COMBINACOES)
                    return validas
    return validas


def _ordenar_fifo(reembolsos: list[dict]) -> list[dict]:
    """Ordena por data de pagamento Jadlog (mais antigo primeiro)."""
    return sorted(
        reembolsos,
        key=lambda r: (
            r.get("data_pagamento_jadlog") or "9999-12-31",
            r.get("id") or 0,
        ),
    )


def _escolher_por_fifo(combos: list[list[dict]]) -> list[dict] | None:
    """Entre várias combinações válidas, escolhe a que quita o mais antigo primeiro."""
    if not combos:
        return None
    if len(combos) == 1:
        return combos[0]

    def chave(combo):
        ordenado = _ordenar_fifo(combo)
        return (
            ordenado[0].get("data_pagamento_jadlog") or "9999-12-31",
            len(combo),
            _soma([r.get("valor_reembolso_esperado") or 0 for r in combo]),
        )

    combos_ord = sorted(combos, key=chave)
    if chave(combos_ord[0]) == chave(combos_ord[1]):
        return None
    return combos_ord[0]


def conciliar_reembolso_cliente(pendentes: list[dict], comprovante: dict) -> dict:
    """Casa um comprovante do cliente com um ou mais reembolsos pendentes.

    Opção C (conservadora): só quita automaticamente quando existe UMA única
    interpretação possível. Qualquer ambiguidade vai para revisão manual.
    """
    valor = comprovante.get("valor_reembolso")
    if valor is None:
        return {
            "status": Status.REVISAO,
            "camada": CAMADA_MANUAL,
            "score": 0,
            "quitados": [],
            "diferenca": None,
            "motivo": "Comprovante sem valor legível",
        }

    if not pendentes:
        return {
            "status": Status.REVISAO,
            "camada": CAMADA_MANUAL,
            "score": 0,
            "quitados": [],
            "diferenca": valor,
            "motivo": "Nenhum reembolso pendente para este CNPJ",
        }

    # Calcula TODAS as combinações válidas antes de decidir.
    combos = _combinacoes_validas(pendentes, valor)

    # Nenhuma combinação: parcial ou divergente
    if not combos:
        total_pendente = _soma(
            [r.get("valor_reembolso_esperado") or 0 for r in pendentes]
        )
        if valor < total_pendente - TOLERANCIA_VALOR:
            return _resultado(
                Status.DIV_VALOR,
                CAMADA_MANUAL,
                50,
                [],
                valor,
                f"Pago R$ {valor:.2f} de R$ {total_pendente:.2f} pendentes",
            )
        return _resultado(
            Status.DIV_VALOR,
            CAMADA_MANUAL,
            20,
            [],
            valor,
            f"Pago R$ {valor:.2f} excede o pendente de R$ {total_pendente:.2f}",
        )

    # Múltiplas combinações: NUNCA decide sozinho (Opção C)
    if len(combos) > 1:
        return _resultado(
            Status.AGRUPADO,
            CAMADA_MANUAL,
            40,
            [],
            valor,
            f"{len(combos)} combinações válidas — revisão manual obrigatória",
        )

    # Combinação única: único caso em que quita automaticamente
    combo = combos[0]
    if len(combo) == 1:
        return _resultado(
            Status.CONFERIDO,
            CAMADA_DIRETA,
            100,
            combo,
            valor,
            "Match direto: um reembolso com valor idêntico",
        )
    return _resultado(
        Status.CONFERIDO,
        CAMADA_UNICA,
        95,
        combo,
        valor,
        f"Combinação única de {len(combo)} reembolsos",
    )


def _resultado(status, camada, score, quitados, valor, motivo) -> dict:
    soma = _soma([r.get("valor_reembolso_esperado") or 0 for r in quitados])
    return {
        "status": status,
        "camada": camada,
        "score": score,
        "quitados": quitados,
        "valor_comprovante": valor,
        "valor_quitado": soma,
        "diferenca": round((valor or 0) - soma, 2),
        "motivo": motivo,
    }


# ---------------------------------------------------------------------------
# CONCILIAÇÃO COMPLETA DE UMA REMESSA
# ---------------------------------------------------------------------------


def conciliar_remessa(
    guia: dict,
    comp_jadlog: dict | None,
    comp_cliente: dict | None,
    pendentes_cliente: list[dict] | None = None,
) -> dict:
    """Executa as duas pontas e devolve o resultado consolidado."""
    resultado = {
        "numero_remessa": guia.get("numero_remessa"),
        "numero_nf": guia.get("numero_nf"),
        "lado_jadlog": None,
        "lado_cliente": None,
        "status_final": None,
        "score_final": 0,
        "detalhe_score": {},
    }

    # Ponta Jadlog
    if comp_jadlog:
        resultado["lado_jadlog"] = conciliar_pagamento_jadlog(guia, comp_jadlog)
    else:
        resultado["lado_jadlog"] = {
            "status": Status.AGUARDANDO,
            "score": 0,
            "diferenca": None,
            "motivo": "Comprovante da Jadlog não localizado",
        }

    # Ponta cliente
    if comp_cliente:
        resultado["lado_cliente"] = conciliar_reembolso_cliente(
            pendentes_cliente or [], comp_cliente
        )
    else:
        resultado["lado_cliente"] = {
            "status": Status.AGUARDANDO,
            "camada": CAMADA_MANUAL,
            "score": 0,
            "quitados": [],
            "diferenca": None,
            "motivo": "Comprovante do cliente não localizado",
        }

    # Score por critério (pesos do config)
    score, detalhe = calcular_score(guia, comp_jadlog, comp_cliente)
    resultado["score_final"] = score
    resultado["detalhe_score"] = detalhe

    # Status final: divergência específica tem prioridade
    prioridade = [
        Status.DIV_CNPJ,
        Status.DIV_NF,
        Status.DIV_VALOR,
        Status.DIV_DATA,
        Status.AGRUPADO,
        Status.REVISAO,
        Status.AGUARDANDO,
        Status.AGRUPADO_CONF,
        Status.CONFERIDO,
    ]
    status_lados = {
        resultado["lado_jadlog"]["status"],
        resultado["lado_cliente"]["status"],
    }
    for st in prioridade:
        if st in status_lados:
            resultado["status_final"] = st
            break

    # Refina divergência de valor para CNPJ/NF quando aplicável
    if resultado["status_final"] == Status.DIV_VALOR:
        if detalhe["cnpj"] == 0 and guia.get("cnpj"):
            resultado["status_final"] = Status.DIV_CNPJ
        elif detalhe["nf"] == 0 and guia.get("numero_nf"):
            resultado["status_final"] = Status.DIV_NF

    return resultado

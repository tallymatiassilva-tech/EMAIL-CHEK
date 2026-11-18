"""Cache de layouts por hash de estrutura de texto.

O cache guarda DUAS coisas por formato:
1. O tipo do documento (guia, comprovante Jadlog, comprovante cliente)
2. O mapa de rótulos que funcionou na extração

Isso permite a inversão de ordem: o classificador consulta o cache ANTES
de pontuar sinais. Se o formato já foi visto, o tipo vem do cache e a
pontuação é dispensada. Formatos novos passam pela pontuação e alimentam
o cache.

O hash é calculado sobre a ESTRUTURA do texto (rótulos e ordem), não sobre
o conteúdo (valores, datas, nomes de empresa). Duas guias DARE com razão
social, CNPJ e valor diferentes têm o mesmo hash porque os rótulos são os
mesmos.
"""

import hashlib
import json
import re
from datetime import datetime

import database as db
from log_manager import get_logger

log = get_logger("layout_cache")

# Quantos caracteres do texto entram no hash. 500 captura o cabeçalho e os
# primeiros rótulos, que é o que define o formato.
TAMANHO_HASH = 500

# Abaixo deste número de campos extraídos, o layout é considerado ruim e
# a taxa de acerto cai em vez de subir.
MIN_CAMPOS_BOM = 3

# Confiança devolvida quando o tipo vem do cache. É alta porque o formato
# já foi confirmado antes — mas não 100, porque o texto pode ter mudado.
CONFIANCA_CACHE = 92.0

# Palavras que identificam rótulos de documento fiscal. O hash considera
# apenas estas palavras, porque são elas que definem o formato.
ROTULOS_CONHECIDOS = {
    # guia
    "dare",
    "gnre",
    "guia",
    "recolhimento",
    "arrecadacao",
    "documento",
    "contribuinte",
    "razao",
    "social",
    "cnpj",
    "cpf",
    "inscricao",
    "estadual",
    "periodo",
    "apuracao",
    "valor",
    "total",
    "pagar",
    "principal",
    "multa",
    "juros",
    "vencimento",
    "emissao",
    "tributo",
    "codigo",
    "barras",
    "linha",
    "digitavel",
    "chave",
    "acesso",
    "secretaria",
    "estado",
    "fazenda",
    "sefaz",
    "prefeitura",
    "municipio",
    "uf",
    "referencia",
    "competencia",
    "exercicio",
    # comprovante
    "comprovante",
    "pagamento",
    "pago",
    "pagador",
    "recebedor",
    "beneficiario",
    "cliente",
    "banco",
    "agencia",
    "conta",
    "autenticacao",
    "nosso",
    "numero",
    "data",
    "hora",
    "transacao",
    "pix",
    "ted",
    "doc",
    "transferencia",
    "reembolso",
    "retencao",
    "fiscal",
    "origem",
    "destino",
}


def calcular_hash(texto: str) -> str:
    """Hash da estrutura do texto — ignora conteúdo variável.

    O que define o formato é o conjunto de rótulos e a ordem deles, não
    os valores. Duas guias DARE com razão social, CNPJ e valor diferentes
    têm o mesmo hash porque os rótulos são os mesmos.
    """
    if not texto:
        return ""

    amostra = texto[:TAMANHO_HASH].lower()
    palavras = re.findall(r"[a-zà-ú]+", amostra)

    # Mantém só as palavras que são rótulos conhecidos
    rotulos = [p for p in palavras if p in ROTULOS_CONHECIDOS]

    if not rotulos:
        # Sem rótulos conhecidos: hash sobre o texto normalizado
        base = re.sub(r"\d+", "#", amostra)
        base = re.sub(r"\s+", " ", base).strip()
        return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]

    base = " ".join(rotulos)
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def buscar_tipo_por_hash(texto: str) -> tuple[str, float] | None:
    """Busca o tipo do documento pelo hash, sem saber o tipo antes.

    É a consulta da inversão de ordem: o classificador chama esta função
    antes de pontuar sinais. Se o formato já foi visto, devolve o tipo
    gravado e a confiança do cache.

    Retorna (tipo, confianca) ou None se o formato é novo.
    """
    h = calcular_hash(texto)
    if not h:
        return None

    with db.cursor() as cur:
        cur.execute(
            """SELECT tipo_documento, taxa_acerto FROM layout_treinado
               WHERE hash_layout=?
               ORDER BY taxa_acerto DESC
               LIMIT 1""",
            (h,),
        )
        row = cur.fetchone()
        if not row:
            return None

        tipo = row["tipo_documento"]
        taxa = row["taxa_acerto"] or 0.0

        # Confiança proporcional à taxa de acerto do layout
        confianca = round(CONFIANCA_CACHE * (taxa / 100.0), 1)
        confianca = max(confianca, 50.0)  # piso: layout ruim ainda vale 50

        log.info(
            "Tipo pelo cache: %s (hash %s, taxa %.1f%%)",
            tipo,
            h[:8],
            taxa,
        )
        return tipo, confianca


def buscar_layout(tipo: str, texto: str) -> dict | None:
    """Busca o mapa de rótulos de um formato já conhecido.

    Retorna o dicionário (campo -> rótulo que funcionou) ou None se o
    formato ainda não foi visto para este tipo.
    """
    h = calcular_hash(texto)
    if not h:
        return None

    with db.cursor() as cur:
        cur.execute(
            """SELECT campos_json, taxa_acerto FROM layout_treinado
               WHERE tipo_documento=? AND hash_layout=?""",
            (tipo, h),
        )
        row = cur.fetchone()
        if not row:
            return None

        try:
            campos = json.loads(row["campos_json"])
        except json.JSONDecodeError:
            log.warning("Layout corrompido para hash %s", h)
            return None

        log.debug(
            "Layout encontrado: %s hash=%s acerto=%.1f%%",
            tipo,
            h,
            row["taxa_acerto"] or 0,
        )
        return campos


def gravar_layout(tipo: str, texto: str, campos: dict, rotulos_usados: dict) -> None:
    """Grava ou atualiza o layout deste formato.

    `rotulos_usados` mapeia campo -> rótulo que funcionou. Só os campos que
    vieram preenchidos entram no mapa — campos vazios não viram parte do
    layout, porque o rótulo deles não foi descoberto.

    A taxa de acerto sobe quando o layout extraiu pelo menos MIN_CAMPOS_BOM
    campos e cai quando extraiu menos. Assim um layout ruim não chega a
    100% só por ser usado muitas vezes.
    """
    h = calcular_hash(texto)
    if not h:
        return

    # Só guarda campos que vieram preenchidos E têm rótulo identificado
    mapa = {
        campo: rotulo
        for campo, rotulo in rotulos_usados.items()
        if campos.get(campo) and rotulo
    }
    if not mapa:
        return

    agora = datetime.now().isoformat(timespec="seconds")
    campos_ok = sum(1 for c in mapa if campos.get(c))

    with db.cursor() as cur:
        cur.execute(
            """SELECT id, confirmacoes, taxa_acerto FROM layout_treinado
               WHERE tipo_documento=? AND hash_layout=?""",
            (tipo, h),
        )
        row = cur.fetchone()

        if row:
            confirmacoes = (row["confirmacoes"] or 0) + 1
            taxa_atual = row["taxa_acerto"] or 0.0

            # Sobe quando o layout extraiu bem, cai quando extraiu pouco
            if campos_ok >= MIN_CAMPOS_BOM:
                taxa = min(taxa_atual + 2, 100.0)
            else:
                taxa = max(taxa_atual - 5, 0.0)

            cur.execute(
                """UPDATE layout_treinado
                   SET campos_json=?, confirmacoes=?, taxa_acerto=?,
                       ultima_utilizacao=?
                   WHERE id=?""",
                (
                    json.dumps(mapa, ensure_ascii=False),
                    confirmacoes,
                    taxa,
                    agora,
                    row["id"],
                ),
            )
            log.debug(
                "Layout atualizado: %s hash=%s taxa=%.1f%% (%d campos)",
                tipo,
                h,
                taxa,
                campos_ok,
            )
        else:
            # Layout novo: taxa inicial proporcional aos campos extraídos
            taxa_inicial = 100.0 if campos_ok >= MIN_CAMPOS_BOM else 60.0
            cur.execute(
                """INSERT INTO layout_treinado
                   (tipo_documento, nome_layout, hash_layout, campos_json,
                    taxa_acerto, confirmacoes, data_criacao, ultima_utilizacao)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    tipo,
                    f"{tipo}_{h[:8]}",
                    h,
                    json.dumps(mapa, ensure_ascii=False),
                    taxa_inicial,
                    1,
                    agora,
                    agora,
                ),
            )
            log.info(
                "Layout novo registrado: %s hash=%s taxa=%.1f%%",
                tipo,
                h,
                taxa_inicial,
            )


def listar_layouts(tipo: str | None = None) -> list[dict]:
    """Lista os layouts treinados, opcionalmente filtrando por tipo."""
    sql = "SELECT * FROM layout_treinado"
    params = []
    if tipo:
        sql += " WHERE tipo_documento=?"
        params.append(tipo)
    sql += " ORDER BY confirmacoes DESC, taxa_acerto DESC"

    with db.cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

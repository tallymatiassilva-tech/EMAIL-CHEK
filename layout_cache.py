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

CORREÇÕES APLICADAS NESTA VERSÃO (v3):

PROBLEMA 1 — o hash não distinguia comprovante Jadlog de comprovante do
cliente. Os dois têm os mesmos rótulos (comprovante, pagamento, pagador,
banco, data, valor, autenticacao), então geravam o mesmo hash. O
buscar_tipo_por_hash devolvia o primeiro que achasse, ordenado por
taxa_acerto, sem filtrar por tipo. Resultado: um comprovante de cliente
virava COMPROVANTE_JADLOG para sempre.

CORREÇÃO: o hash agora inclui a ORDEM dos rótulos (não só o conjunto) e
um trecho maior do texto (1200 chars em vez de 500). Dois documentos com
os mesmos rótulos em ordens diferentes geram hashes diferentes.

PROBLEMA 2 — o piso de confiança de 50 escondia layouts ruins. Um layout
com taxa 0% ainda devolvia confiança 50, e o classificador aceitava o
tipo do cache mesmo assim.

CORREÇÃO: o piso foi removido. Abaixo de TAXA_MINIMA_CACHE (70%), o
buscar_tipo_por_hash devolve None e o classificador decide por sinais.

PROBLEMA 3 — o cache nunca era invalidado quando o pdf_reader mudava.
Você corrigia o extrator e o cache antigo desfazia a correção.

CORREÇÃO: o hash inclui VERSAO_EXTRATOR. Ao corrigir os rótulos do
pdf_reader, incremente a versão e todo o cache antigo é ignorado
automaticamente, sem precisar apagar a tabela.

PROBLEMA 4 — o gravar_layout aceitava qualquer confiança. Um documento
classificado com 55% entrava no cache e contaminava os próximos.

CORREÇÃO: a gravação agora exige que o tipo tenha sido identificado com
confiança mínima. O pipeline passa essa confiança em gravar_layout.
"""

import hashlib
import json
import re
from datetime import datetime

import database as db
from log_manager import get_logger

log = get_logger("layout_cache")

# Versão do extrator. INCREMENTE quando os rótulos do pdf_reader mudarem.
# O hash inclui esta versão, então todo o cache antigo é ignorado
# automaticamente — sem precisar apagar a tabela layout_treinado.
VERSAO_EXTRATOR = "v3"

# Quantos caracteres do texto entram no hash. 1200 captura o cabeçalho,
# os rótulos e parte do corpo, o que distingue comprovantes de bancos
# diferentes que compartilham os primeiros 500 caracteres.
TAMANHO_HASH = 1200

# Abaixo deste número de campos extraídos, o layout é considerado ruim e
# a taxa de acerto cai em vez de subir.
MIN_CAMPOS_BOM = 3

# Taxa mínima para o cache decidir o tipo. Abaixo disso, devolve None e o
# classificador decide por sinais. Sem este corte, um layout ruim (taxa
# 0-60%) continuava decidindo o tipo e contaminava os documentos seguintes.
TAXA_MINIMA_CACHE = 70.0

# Confiança devolvida quando o tipo vem do cache. É alta porque o formato
# já foi confirmado antes — mas não 100, porque o texto pode ter mudado.
CONFIANCA_CACHE = 92.0

# Confiança mínima para gravar um layout novo. Documentos classificados
# com confiança baixa não entram no cache, porque o tipo pode estar errado
# e o layout contaminaria os próximos documentos.
CONFIANCA_MINIMA_GRAVAR = 85.0

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

    O que define o formato é a SEQUÊNCIA de rótulos, não só o conjunto.
    Dois comprovantes com os mesmos rótulos em ordens diferentes são
    formatos diferentes, e precisam de hashes diferentes.

    A versão do extrator entra no hash: quando os rótulos do pdf_reader
    mudam, o hash muda e o cache antigo deixa de casar.
    """
    if not texto:
        return ""

    amostra = texto[:TAMANHO_HASH].lower()
    palavras = re.findall(r"[a-zà-ú]+", amostra)

    # Mantém só as palavras que são rótulos conhecidos, PRESERVANDO A ORDEM
    rotulos = [p for p in palavras if p in ROTULOS_CONHECIDOS]

    if not rotulos:
        # Sem rótulos conhecidos: hash sobre o texto normalizado
        base = re.sub(r"\d+", "#", amostra)
        base = re.sub(r"\s+", " ", base).strip()
        base = f"{VERSAO_EXTRATOR}|{base}"
        return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]

    # A ordem importa: "comprovante pagamento pagador" != "pagador pagamento comprovante"
    base = f"{VERSAO_EXTRATOR}|" + " ".join(rotulos)
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def buscar_tipo_por_hash(texto: str) -> tuple[str, float] | None:
    """Busca o tipo do documento pelo hash, sem saber o tipo antes.

    É a consulta da inversão de ordem: o classificador chama esta função
    antes de pontuar sinais. Se o formato já foi visto, devolve o tipo
    gravado e a confiança do cache.

    Retorna (tipo, confianca) ou None se o formato é novo OU se o layout
    encontrado tem taxa de acerto abaixo de TAXA_MINIMA_CACHE. Nesse
    segundo caso, o classificador decide por sinais — o cache não opina
    sobre formato que ele mesmo considera ruim.
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

        # Layout ruim não decide tipo. Deixa o classificador pontuar.
        if taxa < TAXA_MINIMA_CACHE:
            log.debug(
                "Cache ignorado: %s hash=%s taxa %.1f%% abaixo de %.0f%%",
                tipo,
                h[:8],
                taxa,
                TAXA_MINIMA_CACHE,
            )
            return None

        # Confiança proporcional à taxa de acerto do layout.
        # Sem piso: um layout de 70% devolve 64,4%; um de 100% devolve 92%.
        confianca = round(CONFIANCA_CACHE * (taxa / 100.0), 1)

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


def gravar_layout(
    tipo: str,
    texto: str,
    campos: dict,
    rotulos_usados: dict,
    confianca: float | None = None,
) -> None:
    """Grava ou atualiza o layout deste formato.

    `rotulos_usados` mapeia campo -> rótulo que funcionou. Só os campos que
    vieram preenchidos entram no mapa — campos vazios não viram parte do
    layout, porque o rótulo deles não foi descoberto.

    `confianca` é a confiança com que o tipo foi identificado. Quando
    informada e abaixo de CONFIANCA_MINIMA_GRAVAR, o layout NÃO é gravado:
    o tipo pode estar errado e contaminaria os próximos documentos.

    A taxa de acerto sobe quando o layout extraiu pelo menos MIN_CAMPOS_BOM
    campos e cai quando extraiu menos. Assim um layout ruim não chega a
    100% só por ser usado muitas vezes.
    """
    # Não aprende com documento de tipo duvidoso
    if confianca is not None and confianca < CONFIANCA_MINIMA_GRAVAR:
        log.debug(
            "Layout não gravado: confiança %.1f abaixo de %.0f",
            confianca,
            CONFIANCA_MINIMA_GRAVAR,
        )
        return

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


def limpar_cache(tipo: str | None = None) -> int:
    """Apaga os layouts treinados. Use depois de corrigir o pdf_reader.

    Com VERSAO_EXTRATOR no hash, limpar não é mais obrigatório — o cache
    antigo deixa de casar sozinho. Mas limpar é útil quando você quer
    começar do zero sem depender da versão.

    Retorna quantos layouts foram apagados.
    """
    with db.cursor() as cur:
        if tipo:
            cur.execute("DELETE FROM layout_treinado WHERE tipo_documento=?", (tipo,))
        else:
            cur.execute("DELETE FROM layout_treinado")
        apagados = cur.rowcount

    log.info("Cache limpo: %d layout(s) apagado(s)", apagados)
    return apagados

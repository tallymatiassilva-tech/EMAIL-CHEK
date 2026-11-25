"""Pipeline de conciliação: liga classificação, extração, banco e motor.

Fluxo por remessa:
1. Garante o banco inicializado (tabelas criadas)
2. Lista os arquivos da pasta da remessa
3. Descarta imagens embutidas (assinaturas e logos do e-mail)
4. Classifica cada arquivo por conteúdo (com consulta ao cache de layout)
5. Extrai os campos conforme o tipo
6. Grava o layout aprendido quando a extração foi bem-sucedida
7. Registra falhas de extração na fila de treinamento
8. Grava no banco (upsert_reembolso)
9. Busca pendentes do cliente por CNPJ
10. Concilia com o motor
11. Persiste o resultado

Processamento seletivo:
- processar_pasta_raiz(pasta)                    -> todas as subpastas
- processar_pasta_raiz(pasta, remessas=[...])    -> apenas as remessas listadas
- processar_pasta_raiz(pasta, apenas_novas=True) -> apenas pastas com arquivo
                                                    modificado depois do último
                                                    processamento registrado

Controle de "arquivo novo":
O filtro usa o arquivo .ultimo_processamento na pasta raiz, que guarda em
JSON o timestamp da última execução e o resumo do que foi processado.
Isso é independente do campo data_atualizacao do banco, que o persistence.py
só atualiza quando status ou score mudam. Assim, uma remessa reprocessada
com resultado idêntico não volta a entrar como "nova" na execução seguinte.

Formato do arquivo de controle:
    {"timestamp": 1764086400.123, "total": 42, "processadas": 40,
     "falhas": 2, "quando": "2026-11-25T16:00:00"}

Compatibilidade: se o arquivo contiver apenas um número (formato antigo),
ele é lido como timestamp e os demais campos ficam vazios.
"""

import json
import time
from datetime import date, datetime
from pathlib import Path

import database as db
import layout_cache
import persistence as ps
from conciliation_engine import conciliar_remessa
from config import TipoDoc
from document_classifier import classificar
from excel_reader import extrair_linhas_planilha
from log_manager import get_logger
from pdf_reader import extrair_por_tipo

log = get_logger("pipeline")

EXT_SUPORTADAS = {
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".tiff",
    ".tif",
    ".bmp",
    ".xlsx",
    ".xls",
    ".csv",
}

EXT_IMAGEM = {".png", ".jpg", ".jpeg", ".gif", ".bmp"}

# Prefixos de nome típicos de imagens embutidas no corpo do e-mail
PREFIXOS_IMAGEM_EMBUTIDA = (
    "image",
    "img",
    "logo",
    "assinatura",
    "signature",
    "banner",
    "icone",
    "icon",
)

# Imagens abaixo deste tamanho são tratadas como assinatura/logo
LIMITE_BYTES_IMAGEM_EMBUTIDA = 50_000

# Confiança mínima para gravar o layout aprendido. Abaixo disso, o
# documento pode ter sido classificado errado e o layout contaminaria
# o cache com um mapa ruim.
CONFIANCA_MINIMA_LAYOUT = 60.0

# Nome do arquivo de controle que guarda o timestamp e o resumo do
# último processamento. Fica na pasta raiz, fora das subpastas de remessa.
ARQUIVO_CONTROLE = ".ultimo_processamento"

# Campos obrigatórios por tipo de documento. Se algum faltar, o documento
# vai para a fila de treinamento para você criar um layout específico.
CAMPOS_OBRIGATORIOS = {
    TipoDoc.GUIA: ["valor_guia", "data_vencimento"],
    TipoDoc.COMP_JADLOG: ["valor_pago_jadlog", "data_pagamento_jadlog"],
    TipoDoc.COMP_CLIENTE: ["valor_reembolso", "data_reembolso"],
}


# ---------------------------------------------------------------------------
# COLETA DOS ARQUIVOS
# ---------------------------------------------------------------------------
def _listar_arquivos(pasta: Path) -> list[Path]:
    """Lista arquivos suportados na pasta da remessa."""
    return sorted(
        p for p in pasta.iterdir() if p.is_file() and p.suffix.lower() in EXT_SUPORTADAS
    )


def _e_imagem_embutida(caminho: Path) -> bool:
    """Detecta imagens de assinatura/logo embutidas no e-mail.

    São arquivos pequenos, com nomes genéricos, que o Outlook extrai junto
    com os anexos reais. Nunca são comprovantes.
    """
    if caminho.suffix.lower() not in EXT_IMAGEM:
        return False

    nome = caminho.stem.lower()
    if any(nome.startswith(p) or nome == p for p in PREFIXOS_IMAGEM_EMBUTIDA):
        return True

    try:
        if caminho.stat().st_size < LIMITE_BYTES_IMAGEM_EMBUTIDA:
            return True
    except OSError:
        pass

    return False


def _calcular_dias_reembolso(
    data_pagamento: str | None, data_reembolso: str | None
) -> int | None:
    """Diferença em dias entre o pagamento da Jadlog e o reembolso do cliente.

    Aceita data em dd/mm/yyyy (formato do pdf_reader) e em YYYY-MM-DD.
    """
    if not data_pagamento or not data_reembolso:
        return None

    def _parse(valor: str) -> date | None:
        s = str(valor)[:10]
        # dd/mm/yyyy
        if "/" in s:
            try:
                d, m, a = s.split("/")
                return date(int(a), int(m), int(d))
            except (ValueError, TypeError):
                return None
        # YYYY-MM-DD
        try:
            return date.fromisoformat(s)
        except (ValueError, TypeError):
            return None

    dj = _parse(data_pagamento)
    dc = _parse(data_reembolso)
    if dj is None or dc is None:
        return None
    return (dc - dj).days


# ---------------------------------------------------------------------------
# APRENDIZADO DE LAYOUT
# ---------------------------------------------------------------------------
def _rotulos_do_campo(campos: dict) -> dict:
    """Extrai o mapa campo -> rótulo que o extrator devolveu.

    O pdf_reader devolve os rótulos usados na chave `_rotulos`. Se a
    chave não existir, devolve vazio e o layout não é gravado.
    """
    rotulos = campos.get("_rotulos")
    if isinstance(rotulos, dict):
        return rotulos
    return {}


def _aprender_layout(
    caminho: Path, tipo: str, confianca: float, campos: dict, texto: str | None
) -> bool:
    """Grava o layout aprendido quando a extração foi bem-sucedida.

    Condições para gravar:
    1. O tipo foi identificado com confiança >= CONFIANCA_MINIMA_LAYOUT
    2. A extração devolveu pelo menos um rótulo usado
    3. O texto do documento está disponível para o hash

    Retorna True quando gravou.
    """
    if confianca < CONFIANCA_MINIMA_LAYOUT:
        log.debug(
            "Layout não gravado: confiança %.1f abaixo do mínimo (%.0f)",
            confianca,
            CONFIANCA_MINIMA_LAYOUT,
        )
        return False

    rotulos = _rotulos_do_campo(campos)
    if not rotulos:
        log.debug("Layout não gravado: extrator não devolveu rótulos")
        return False

    # O texto vem do próprio extrator; se não veio, lê do arquivo
    if texto is None:
        try:
            from ocr_engine import extrair_texto

            texto = extrair_texto(caminho)
        except Exception as e:
            log.debug("Layout não gravado: texto indisponível (%s)", e)
            return False

    if not texto:
        return False

    try:
        layout_cache.gravar_layout(tipo, texto, campos, rotulos)
        log.info(
            "Layout aprendido: %s (%s, %d rótulos)",
            caminho.name,
            tipo,
            len(rotulos),
        )
        return True
    except Exception as e:
        # O aprendizado nunca deve derrubar o pipeline
        log.error("Erro ao gravar layout de %s: %s", caminho.name, e)
        return False


# ---------------------------------------------------------------------------
# FILA DE TREINAMENTO (Etapa 4)
# ---------------------------------------------------------------------------

def _campos_faltantes(tipo: str, campos: dict) -> list[str]:
    """Lista os campos obrigatórios que vieram vazios na extração."""
    obrigatorios = CAMPOS_OBRIGATORIOS.get(tipo, [])
    return [c for c in obrigatorios if not campos.get(c)]


def _registrar_falha(
    remessa: str, caminho: Path, tipo: str, campos: dict, faltando: list[str]
) -> None:
    """Enfileira o documento para treinamento quando campos obrigatórios faltam.

    Guarda o texto bruto (limitado a 8000 chars) para você inspecionar o
    formato e criar um layout específico depois. Evita duplicar a mesma
    entrada quando o pipeline roda várias vezes sobre a mesma remessa.
    """
    texto = (campos.get("texto") or "")[:8000]
    payload = {
        "faltando": faltando,
        "campos": {
            k: v for k, v in campos.items() if k != "texto" and not isinstance(v, Path)
        },
        "texto": texto,
    }

    try:
        with db.cursor() as cur:
            cur.execute(
                """SELECT id FROM fila_treinamento
                   WHERE numero_remessa=? AND caminho_arquivo=? AND resolvido=0""",
                (remessa, str(caminho)),
            )
            if cur.fetchone():
                log.debug("Falha já registrada: %s", caminho.name)
                return

            cur.execute(
                """INSERT INTO fila_treinamento
                   (tipo_documento, caminho_arquivo, numero_remessa,
                    confianca, campos_json, criado_em, resolvido)
                   VALUES (?,?,?,?,?,?,0)""",
                (
                    tipo,
                    str(caminho),
                    remessa,
                    campos.get("confianca", 0),
                    json.dumps(payload, ensure_ascii=False, default=str),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
        log.warning(
            "Documento enfileirado para treinamento: %s (faltando: %s)",
            caminho.name,
            ", ".join(faltando),
        )
    except Exception as e:
        # A fila de treinamento nunca deve derrubar o pipeline
        log.error("Erro ao registrar falha de %s: %s", caminho.name, e)


# ---------------------------------------------------------------------------
# PROCESSAMENTO DE UMA REMESSA
# ---------------------------------------------------------------------------
def processar_remessa(pasta: Path, remessa: str) -> dict:
    """Processa todos os arquivos de uma pasta de remessa."""
    # Garante que as tabelas existam (idempotente)
    db.init_db()
    ps.init_historico()

    arquivos = _listar_arquivos(pasta)
    if not arquivos:
        log.warning("Remessa %s: nenhum arquivo encontrado", remessa)
        return {"ok": False, "remessa": remessa, "motivo": "Sem arquivos"}

    guia = None
    comp_jadlog = None
    comp_cliente = None
    planilhas = []
    desconhecidos = []
    ignorados = []
    enfileirados = []
    layouts_aprendidos = []

    # Classifica e extrai cada arquivo
    for arq in arquivos:
        try:
            if _e_imagem_embutida(arq):
                log.info("Ignorando imagem embutida: %s", arq.name)
                ignorados.append(arq.name)
                continue

            tipo, conf = classificar(arq)
            log.info("%s -> %s (%.1f%%)", arq.name, tipo, conf)

            if tipo == TipoDoc.DESCONHECIDO:
                desconhecidos.append(arq.name)
                continue

            if tipo == TipoDoc.PLANILHA:
                planilhas.extend(extrair_linhas_planilha(arq))
                continue

            campos = extrair_por_tipo(arq, tipo)
            campos["numero_remessa"] = remessa  # o motor espera este campo
            campos["caminho"] = str(arq)
            campos["confianca"] = conf

            # Aprendizado de layout: grava o mapa de rótulos que funcionou.
            # Roda antes da checagem de campos faltantes, porque um layout
            # parcial ainda ajuda na próxima vez.
            if _aprender_layout(arq, tipo, conf, campos, campos.get("texto")):
                layouts_aprendidos.append(arq.name)

            # Etapa 4: registra falhas de extração para treinamento futuro
            faltando = _campos_faltantes(tipo, campos)
            if faltando:
                _registrar_falha(remessa, arq, tipo, campos, faltando)
                enfileirados.append(arq.name)

            if tipo == TipoDoc.GUIA:
                if guia is None:
                    guia = campos
                else:
                    log.info("Guia adicional ignorada: %s", arq.name)
            elif tipo == TipoDoc.COMP_JADLOG:
                if comp_jadlog is None:
                    comp_jadlog = campos
                else:
                    log.info("Comprovante Jadlog adicional ignorado: %s", arq.name)
            elif tipo == TipoDoc.COMP_CLIENTE:
                if comp_cliente is None:
                    comp_cliente = campos
                else:
                    log.info("Comprovante cliente adicional ignorado: %s", arq.name)

        except Exception as e:
            log.warning("Erro ao processar %s: %s", arq.name, e)
            desconhecidos.append(arq.name)

    if not guia:
        log.warning("Remessa %s: guia não encontrada", remessa)
        return {
            "ok": False,
            "remessa": remessa,
            "motivo": "Guia não encontrada",
            "desconhecidos": desconhecidos,
            "ignorados": ignorados,
            "enfileirados": enfileirados,
            "layouts_aprendidos": layouts_aprendidos,
        }

    # Monta o registro para o banco (só colunas que existem no schema)
    registro = {
        "numero_remessa": remessa,
        "numero_nf": guia.get("numero_nf") or None,
        "tomador": guia.get("tomador") or None,
        "numero_guia": guia.get("numero_guia") or None,
        "cliente": guia.get("cliente") or None,
        "cnpj_cliente": guia.get("cnpj") or None,
        "cnpj_guia": guia.get("cnpj") or None,
        "uf": guia.get("uf") or None,
        "tributo": guia.get("tributo") or None,
        "valor_guia": guia.get("valor_guia"),
        "data_emissao": guia.get("data_emissao"),
        "data_vencimento": guia.get("data_vencimento"),
        "caminho_guia": guia.get("caminho"),
        # Estado inicial a cada execução: o motor parte do zero.
        # Sem isso, uma remessa já conciliada não volta a aparecer como
        # pendente em listar_pendentes_por_cnpj (que filtra por status).
        "status": "AGUARDANDO_REEMBOLSO",
        # Valor que o motor usa para casar o comprovante do cliente com o
        # pendente. Sem este campo, o pendente vem com None e o motor não
        # consegue comparar valores.
        "valor_reembolso_esperado": guia.get("valor_guia"),
    }

    if comp_jadlog:
        registro["valor_pago_jadlog"] = comp_jadlog.get("valor_pago_jadlog")
        registro["data_pagamento_jadlog"] = comp_jadlog.get("data_pagamento_jadlog")
        registro["caminho_comp_jadlog"] = comp_jadlog.get("caminho")

    if comp_cliente:
        registro["valor_reembolso_cliente"] = comp_cliente.get("valor_reembolso")
        registro["data_reembolso_cliente"] = comp_cliente.get("data_reembolso")
        registro["caminho_comp_cliente"] = comp_cliente.get("caminho")
        registro["cnpj_comprovante"] = comp_cliente.get("cnpj_pagador") or None

    # Calcula o prazo de reembolso (usado pelo dashboard e pela exportação)
    registro["dias_reembolso"] = _calcular_dias_reembolso(
        registro.get("data_pagamento_jadlog"),
        registro.get("data_reembolso_cliente"),
    )

    db.upsert_reembolso(registro)

    # Pendentes do mesmo CNPJ — inclui a própria remessa.
    # O comprovante do cliente é o reembolso desta remessa, então ela precisa
    # estar na lista para o motor conseguir casar valor e data.
    cnpj = registro.get("cnpj_cliente")
    pendentes = db.listar_pendentes_por_cnpj(cnpj) if cnpj else []

    # Concilia
    resultado = conciliar_remessa(guia, comp_jadlog, comp_cliente, pendentes)
    resultado["numero_remessa"] = remessa

    # Persiste. Uma remessa com CNPJ divergente NUNCA entra no caminho
    # agrupado: o agrupamento quitaria vários reembolsos de uma vez, e a
    # divergência de identificação precisa de revisão humana antes disso.
    cnpj_divergente = bool(resultado.get("cnpj_divergente"))
    tem_quitacao_agrupada = bool((resultado.get("lado_cliente") or {}).get("quitados"))
    if tem_quitacao_agrupada and not cnpj_divergente:
        ps.salvar_quitacao_agrupada(resultado)
    else:
        ps.salvar_resultado(resultado)

    return {
        "ok": True,
        "remessa": remessa,
        "status": resultado.get("status_final"),
        "score": resultado.get("score_final"),
        "guia": bool(guia),
        "comp_jadlog": bool(comp_jadlog),
        "comp_cliente": bool(comp_cliente),
        "planilhas": len(planilhas),
        "desconhecidos": desconhecidos,
        "ignorados": ignorados,
        "enfileirados": enfileirados,
        "layouts_aprendidos": layouts_aprendidos,
        "resultado": resultado,
    }


# ---------------------------------------------------------------------------
# SELEÇÃO DE REMESSAS
# ---------------------------------------------------------------------------
def _mtime_mais_recente(pasta: Path) -> float:
    """Maior mtime entre os arquivos suportados da pasta. 0 se não houver."""
    maior = 0.0
    try:
        for arq in pasta.iterdir():
            if arq.is_file() and arq.suffix.lower() in EXT_SUPORTADAS:
                try:
                    m = arq.stat().st_mtime
                    if m > maior:
                        maior = m
                except OSError:
                    continue
    except OSError:
        return 0.0
    return maior


def _ler_controle(pasta_raiz: Path) -> dict:
    """Lê o arquivo de controle e devolve um dict normalizado.

    Sempre retorna as chaves timestamp, total, processadas, falhas e quando.
    Aceita o formato antigo (apenas um número) por compatibilidade.
    """
    vazio = {
        "timestamp": 0.0,
        "total": 0,
        "processadas": 0,
        "falhas": 0,
        "quando": None,
    }
    caminho = pasta_raiz / ARQUIVO_CONTROLE
    try:
        bruto = caminho.read_text().strip()
    except OSError:
        return vazio

    if not bruto:
        return vazio

    # Formato antigo: apenas o timestamp como número
    try:
        return {**vazio, "timestamp": float(bruto)}
    except ValueError:
        pass

    # Formato novo: JSON com o resumo
    try:
        dados = json.loads(bruto)
    except (json.JSONDecodeError, TypeError):
        log.warning("Arquivo de controle ilegível: %s", caminho)
        return vazio

    if not isinstance(dados, dict):
        return vazio

    return {
        "timestamp": float(dados.get("timestamp") or 0.0),
        "total": int(dados.get("total") or 0),
        "processadas": int(dados.get("processadas") or 0),
        "falhas": int(dados.get("falhas") or 0),
        "quando": dados.get("quando"),
    }


def _gravar_controle(
    pasta_raiz: Path,
    total: int = 0,
    processadas: int = 0,
    falhas: int = 0,
    quando: float | None = None,
) -> None:
    """Grava o timestamp e o resumo do processamento no arquivo de controle."""
    ts = quando or time.time()
    dados = {
        "timestamp": ts,
        "total": int(total),
        "processadas": int(processadas),
        "falhas": int(falhas),
        "quando": datetime.fromtimestamp(ts).isoformat(timespec="seconds"),
    }
    try:
        (pasta_raiz / ARQUIVO_CONTROLE).write_text(
            json.dumps(dados, ensure_ascii=False, indent=2)
        )
    except OSError as e:
        log.warning("Não foi possível gravar %s: %s", ARQUIVO_CONTROLE, e)


def _remessas_com_arquivo_novo(pasta_raiz: Path) -> list[str]:
    """Remessas cuja pasta tem arquivo mais novo que o último processamento.

    Usa o arquivo de controle .ultimo_processamento em vez da
    data_atualizacao do banco. O persistence.py só atualiza essa data
    quando o status ou o score mudam, então uma remessa reprocessada com
    resultado idêntico ficaria com data antiga e voltaria a entrar como
    "nova" toda vez. O controle local registra o instante real da execução.
    """
    ultimo = _ler_controle(pasta_raiz)["timestamp"]
    novas = []
    for sub in sorted(p for p in pasta_raiz.iterdir() if p.is_dir()):
        mtime = _mtime_mais_recente(sub)
        if mtime == 0:
            continue
        if mtime > ultimo:
            novas.append(sub.name)
    return novas


def _selecionar_subpastas(
    pasta_raiz: Path,
    remessas: list[str] | None = None,
    apenas_novas: bool = False,
) -> tuple[list[Path], list[str]]:
    """Resolve quais subpastas processar.

    Retorna (subpastas, ignoradas).
    - remessas: lista explícita de números de remessa. Vence sobre apenas_novas.
    - apenas_novas: processa só pastas com arquivo mais novo que o controle.
    - nenhum dos dois: processa todas.
    """
    todas = sorted(p for p in pasta_raiz.iterdir() if p.is_dir())

    if remessas:
        alvo = {str(r).strip() for r in remessas if str(r).strip()}
        selecionadas = [p for p in todas if p.name in alvo]
        encontradas = {p.name for p in selecionadas}
        faltando = sorted(alvo - encontradas)
        if faltando:
            log.warning("Remessas sem pasta em %s: %s", pasta_raiz, ", ".join(faltando))
        ignoradas = [p.name for p in todas if p.name not in alvo]
        return selecionadas, ignoradas

    if apenas_novas:
        novas = set(_remessas_com_arquivo_novo(pasta_raiz))
        selecionadas = [p for p in todas if p.name in novas]
        ignoradas = [p.name for p in todas if p.name not in novas]
        log.info(
            "Processamento seletivo: %d de %d remessas com arquivo novo",
            len(selecionadas),
            len(todas),
        )
        return selecionadas, ignoradas

    return todas, []


# ---------------------------------------------------------------------------
# PROCESSAMENTO EM LOTE
# ---------------------------------------------------------------------------
def processar_pasta_raiz(
    pasta_raiz: str | Path,
    remessas: list[str] | None = None,
    apenas_novas: bool = False,
) -> dict:
    """Processa as subpastas de remessa dentro da pasta raiz.

    Args:
        pasta_raiz: pasta que contém as subpastas de remessa.
        remessas: lista explícita de números de remessa a processar.
                  Quando informada, só essas são processadas.
        apenas_novas: quando True, processa apenas as pastas cujo arquivo
                  mais recente é mais novo que o último processamento
                  registrado no arquivo de controle. Ignorado se `remessas`
                  for informada.

    Retorna:
        {
            "ok": bool,
            "total": int,              # subpastas selecionadas
            "processadas": int,
            "falhas": int,
            "ignoradas": list[str],    # subpastas fora da seleção
            "layouts_aprendidos": int,
            "modo": str,               # "todas" | "lista" | "novas"
            "controle": dict,          # resumo gravado no arquivo de controle
            "resultados": list[dict],
        }
    """
    db.init_db()
    ps.init_historico()

    raiz = Path(pasta_raiz)
    if not raiz.is_dir():
        return {"ok": False, "motivo": f"Pasta inválida: {raiz}"}

    subpastas, ignoradas = _selecionar_subpastas(raiz, remessas, apenas_novas)

    if remessas:
        modo = "lista"
    elif apenas_novas:
        modo = "novas"
    else:
        modo = "todas"

    if not subpastas:
        log.info("Nenhuma remessa selecionada (modo=%s)", modo)
        return {
            "ok": True,
            "total": 0,
            "processadas": 0,
            "falhas": 0,
            "ignoradas": ignoradas,
            "layouts_aprendidos": 0,
            "modo": modo,
            "controle": _ler_controle(raiz),
            "resultados": [],
        }

    log.info(
        "Processando %d remessa(s) (modo=%s, %d ignorada(s))",
        len(subpastas),
        modo,
        len(ignoradas),
    )

    resultados = []
    for sub in subpastas:
        try:
            resultados.append(processar_remessa(sub, sub.name))
        except Exception as e:
            log.error("Erro na remessa %s: %s", sub.name, e)
            resultados.append({"ok": False, "remessa": sub.name, "motivo": str(e)})

    ok = sum(1 for r in resultados if r.get("ok"))
    falhas = len(resultados) - ok
    total_layouts = sum(len(r.get("layouts_aprendidos") or []) for r in resultados)

    # Registra o instante e o resumo do processamento para o filtro da
    # próxima execução. Só grava quando o modo é "novas": nos modos "todas"
    # e "lista" o usuário escolheu processar de propósito, e mover o
    # controle faria a próxima execução do modo "novas" ignorar arquivos
    # que ainda precisam ser vistos.
    controle = _ler_controle(raiz)
    if modo == "novas":
        _gravar_controle(
            raiz,
            total=len(resultados),
            processadas=ok,
            falhas=falhas,
        )
        controle = _ler_controle(raiz)

    return {
        "ok": True,
        "total": len(resultados),
        "processadas": ok,
        "falhas": falhas,
        "ignoradas": ignoradas,
        "layouts_aprendidos": total_layouts,
        "modo": modo,
        "controle": controle,
        "resultados": resultados,
    }
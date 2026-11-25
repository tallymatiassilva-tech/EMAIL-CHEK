"""Interface Streamlit para conciliacao de reembolsos Jadlog.

Camada de tela apenas. Toda a regra de negocio fica nos modulos existentes:
outlook_reader, pipeline, export_excel, backup, reprocessar.
"""

import io
import json
import sqlite3
from pathlib import Path

import streamlit as st

from config import DB_PATH, PASTA_ANEXOS_PADRAO

st.set_page_config(
    page_title="Conciliacao Jadlog",
    page_icon="📦",
    layout="wide",
)

# ---------------------------------------------------------------- dados


@st.cache_data(ttl=30)
def consultar(sql: str, params: tuple = ()) -> list[dict]:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()


def executar(sql: str, params: tuple = ()) -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(sql, params)
        con.commit()
    finally:
        con.close()


def tabelas_existentes() -> list[str]:
    return [
        r["name"]
        for r in consultar(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    ]


def mostrar_documento(caminho_str: str | None, altura: int = 420) -> None:
    """Renderiza PDF ou imagem no painel."""
    if not caminho_str:
        st.caption("Caminho nao informado.")
        return
    caminho = Path(caminho_str)
    if not caminho.exists():
        st.caption(f"Arquivo nao encontrado: {caminho.name}")
        return
    if caminho.suffix.lower() == ".pdf":
        st.pdf(caminho.read_bytes(), height=altura)
    else:
        st.image(str(caminho), width="stretch")


# ---------------------------------------------------------------- diagnostico

# Campos obrigatorios por tipo, com severidade.
# 0 = critico (valor), 1 = alto (data), 2 = medio (identificacao), 3 = baixo
CAMPOS_POR_TIPO = {
    "COMPROVANTE_REEMBOLSO": [
        ("valor_reembolso", 0, "valor"),
        ("data_reembolso", 1, "data"),
        ("cnpj_pagador", 2, "cnpj"),
        ("nome_pagador", 3, "nome"),
    ],
    "COMPROVANTE_JADLOG": [
        ("valor_pago_jadlog", 0, "valor"),
        ("data_pagamento_jadlog", 1, "data"),
        ("cnpj_pagador", 2, "cnpj"),
        ("beneficiario", 3, "nome"),
        ("autenticacao", 3, "autenticacao"),
    ],
    "GUIA_FISCAL": [
        ("valor_guia", 0, "valor"),
        ("data_emissao", 1, "data"),
        ("data_vencimento", 1, "data"),
        ("cnpj", 2, "cnpj"),
        ("numero_nf", 2, "nf"),
        ("numero_guia", 3, "guia"),
    ],
    "PLANILHA": [],
    "DESCONHECIDO": [],
}

SEVERIDADE_LABEL = {
    0: "🔴 CRITICO",
    1: "🟠 ALTO",
    2: "🟡 MEDIO",
    3: "🟢 BAIXO",
}


def _campos_do_json(campos_json: str | None) -> dict:
    """Le o campos_json do item e devolve o dicionario de campos."""
    try:
        dados = json.loads(campos_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return dados.get("campos", dados) or {}


def _ler_json_bruto(campos_json: str | None) -> dict:
    """Le o campos_json inteiro, preservando texto e _rotulos."""
    try:
        dados = json.loads(campos_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return dados if isinstance(dados, dict) else {}


def _diagnosticar_item(item: dict) -> dict:
    """Analisa o item e devolve o diagnostico completo de campos ausentes.

    Verifica TODOS os campos obrigatorios do tipo, nao so valor e data.
    Classifica cada campo ausente por severidade.

    Retorna:
        {
            "campos_ausentes": list[str],
            "severidade": int,          # menor severidade encontrada
            "severidade_label": str,
            "valor_ausente": bool,
            "data_ausente": bool,
            "motivo": str,
            "prioridade": int,          # menor = mais urgente
            "detalhes": list[dict],     # campo, severidade, categoria
        }
    """
    campos = _campos_do_json(item.get("campos_json"))
    tipo = item.get("tipo_documento", "")
    obrigatorios = CAMPOS_POR_TIPO.get(tipo, [])

    ausentes = []
    detalhes = []

    for chave, sev, categoria in obrigatorios:
        valor = campos.get(chave)
        if valor in (None, "", 0, 0.0):
            ausentes.append(chave)
            detalhes.append(
                {
                    "campo": chave,
                    "severidade": sev,
                    "categoria": categoria,
                }
            )

    valor_ausente = any(d["categoria"] == "valor" for d in detalhes)
    data_ausente = any(d["categoria"] == "data" for d in detalhes)

    # Severidade geral = a mais grave encontrada
    if detalhes:
        severidade = min(d["severidade"] for d in detalhes)
    else:
        severidade = 4  # nenhum campo ausente

    # Motivo provavel
    categorias = {d["categoria"] for d in detalhes}
    if not ausentes:
        motivo = ""
    elif "valor" in categorias and "data" in categorias:
        motivo = "OCR corrompido: valor e data ilegiveis"
    elif "valor" in categorias:
        motivo = "Valor nao encontrado (CNPJ pode ter sido confundido com valor)"
    elif "data" in categorias:
        motivo = "Data nao encontrada (formato nao reconhecido pelo OCR)"
    elif "cnpj" in categorias:
        motivo = "CNPJ nao encontrado (documento pode estar incompleto)"
    elif "nf" in categorias:
        motivo = "Numero da NF nao encontrado"
    else:
        motivo = "Campos secundarios ausentes"

    return {
        "campos_ausentes": ausentes,
        "severidade": severidade,
        "severidade_label": SEVERIDADE_LABEL.get(severidade, "⚪ OK"),
        "valor_ausente": valor_ausente,
        "data_ausente": data_ausente,
        "motivo": motivo,
        "prioridade": severidade,
        "detalhes": detalhes,
    }


def _preparar_itens(itens: list[dict]) -> list[dict]:
    """Aplica o diagnostico a cada item e adiciona os campos derivados."""
    for i in itens:
        i["arquivo"] = Path(i["caminho_arquivo"]).name
        diag = _diagnosticar_item(i)
        i["_diag"] = diag
        i["_valor_ausente"] = diag["valor_ausente"]
        i["_data_ausente"] = diag["data_ausente"]
        i["_campos_ausentes"] = diag["campos_ausentes"]
        i["_motivo"] = diag["motivo"]
        i["_severidade"] = diag["severidade"]
        i["_severidade_label"] = diag["severidade_label"]
        i["_prioridade"] = diag["prioridade"]
    return itens


def _filtrar_itens(
    itens: list[dict],
    severidades: list[int] | None = None,
    tipos: list[str] | None = None,
    remessas: list[str] | None = None,
) -> list[dict]:
    """Filtra a lista de itens pelos criterios escolhidos."""
    resultado = itens
    if severidades:
        resultado = [i for i in resultado if i["_severidade"] in severidades]
    if tipos:
        resultado = [i for i in resultado if i["tipo_documento"] in tipos]
    if remessas:
        resultado = [i for i in resultado if i["numero_remessa"] in remessas]
    return resultado


def _render_filtros(itens: list[dict], prefixo: str) -> tuple:
    """Renderiza os tres filtros e devolve (severidades, tipos, remessas).

    prefixo: 'fila' ou 'resolver' — garante keys unicas no Streamlit.
    """
    col_f1, col_f2, col_f3 = st.columns(3)

    sev_opcoes = {
        "🔴 Critico (valor)": 0,
        "🟠 Alto (data)": 1,
        "🟡 Medio (CNPJ/NF)": 2,
        "🟢 Baixo (outros)": 3,
    }
    sev_escolhidas = col_f1.multiselect(
        "Severidade",
        list(sev_opcoes.keys()),
        key=f"{prefixo}_sev",
    )
    sev_filtro = [sev_opcoes[s] for s in sev_escolhidas] if sev_escolhidas else None

    tipos_disponiveis = sorted({i["tipo_documento"] for i in itens})
    tipos_filtro = (
        col_f2.multiselect(
            "Tipo de documento",
            tipos_disponiveis,
            key=f"{prefixo}_tipo",
        )
        or None
    )

    remessas_disponiveis = sorted(
        {i["numero_remessa"] for i in itens if i["numero_remessa"]}
    )
    remessas_filtro = (
        col_f3.multiselect(
            "Remessa",
            remessas_disponiveis,
            key=f"{prefixo}_rem",
        )
        or None
    )

    return sev_filtro, tipos_filtro, remessas_filtro


def _gerar_excel_criticos(itens: list[dict]) -> bytes:
    """Gera um Excel com os itens criticos e devolve os bytes."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
    except ImportError:
        raise RuntimeError("openpyxl nao instalado. Rode: pip install openpyxl")

    wb = Workbook()
    ws = wb.active
    ws.title = "Itens criticos"

    cabecalhos = [
        "Severidade",
        "ID",
        "Arquivo",
        "Tipo",
        "Remessa",
        "Confianca",
        "Campos ausentes",
        "Motivo",
    ]
    ws.append(cabecalhos)

    # Estilo do cabecalho
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(horizontal="center")

    # Ordena por severidade
    itens_ord = sorted(itens, key=lambda x: (x["_severidade"], -x["id"]))
    for i in itens_ord:
        ws.append(
            [
                i["_severidade_label"],
                i["id"],
                i["arquivo"],
                i["tipo_documento"],
                i["numero_remessa"],
                i["confianca"],
                ", ".join(i["_campos_ausentes"]) or "-",
                i["_motivo"] or "-",
            ]
        )

    # Larguras
    larguras = [18, 8, 45, 24, 20, 12, 40, 55]
    for idx, largura in enumerate(larguras, start=1):
        ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = largura

    # Congela o cabecalho
    ws.freeze_panes = "A2"

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()


# ---------------------------------------------------------------- tela

st.title("Conciliacao de Reembolsos — Jadlog")

abas = st.tabs(
    [
        "1. Coletar Outlook",
        "2. Conciliar",
        "3. Dashboard",
        "4. Exportar Excel",
        "5. Estatisticas",
        "6. Fila",
        "7. Revisao manual",
        "8. Layouts",
        "9. Reprocessar",
        "10. Backup",
        "11. Reprocessar fila",
    ]
)

# ---- 1. coletar
with abas[0]:
    st.subheader("Coletar anexos do Outlook")
    st.caption(f"Pasta destino dos anexos: {PASTA_ANEXOS_PADRAO}")

    try:
        from outlook_reader import coletar_todos, listar_pastas

        col_a, col_b = st.columns([1, 3])
        with col_a:
            if st.button("Carregar pastas", key="btn_listar_pastas"):
                with st.spinner("Lendo pastas do Outlook..."):
                    try:
                        pastas = listar_pastas()
                        st.session_state["pastas"] = pastas
                        if not pastas:
                            st.warning(
                                "Nenhuma pasta retornada. "
                                "Verifique se o Outlook esta aberto."
                            )
                    except Exception as e:
                        st.session_state["pastas"] = []
                        st.error(f"Falha ao listar pastas: {e}")

        with col_b:
            if st.button("Limpar lista", key="btn_limpar_pastas"):
                st.session_state.pop("pastas", None)
                st.rerun()

        pastas = st.session_state.get("pastas", [])
        if pastas:
            mapa = {p["caminho"]: p for p in pastas}
            escolhidas = st.multiselect(
                "Pastas do Outlook (pode escolher varias)",
                list(mapa.keys()),
                key="sel_pastas_outlook",
                help="Selecione uma ou mais pastas. Cada pasta sera varrida "
                "separadamente e os resultados serao somados.",
            )

            if escolhidas:
                total_itens = sum(
                    mapa[c].get("total_itens", 0)
                    for c in escolhidas
                    if mapa[c].get("total_itens", -1) > 0
                )
                st.caption(
                    f"{len(escolhidas)} pasta(s) selecionada(s) | "
                    f"~{total_itens} itens no total"
                )

            col1, col2 = st.columns(2)
            dias = col1.number_input("Dias para tras (0 = todos)", min_value=0, value=7)
            recursivo = col2.checkbox("Incluir subpastas", value=False)

            if st.button(
                "Coletar agora",
                type="primary",
                key="btn_coletar",
                disabled=not escolhidas,
            ):
                progresso = st.progress(0.0, text="Iniciando coleta...")
                acumulado = {
                    "remessas": {},
                    "total_emails": 0,
                    "pastas": {},
                    "erros": [],
                }

                for i, caminho in enumerate(escolhidas, start=1):
                    progresso.progress(
                        (i - 1) / len(escolhidas),
                        text=f"[{i}/{len(escolhidas)}] {caminho}",
                    )
                    try:
                        r = coletar_todos(
                            caminho_pasta=caminho,
                            pasta_destino=PASTA_ANEXOS_PADRAO,
                            dias=int(dias) or None,
                            recursivo=recursivo,
                        )
                    except Exception as e:
                        acumulado["erros"].append(f"{caminho}: {e}")
                        continue

                    if not r.get("ok"):
                        acumulado["erros"].append(f"{caminho}: {r.get('erro')}")
                        continue

                    acumulado["total_emails"] += r.get("total_emails", 0)
                    for rem, dados in r.get("remessas", {}).items():
                        if rem not in acumulado["remessas"]:
                            acumulado["remessas"][rem] = {"anexos": []}
                        acumulado["remessas"][rem]["anexos"].extend(
                            dados.get("anexos", [])
                        )
                    acumulado["pastas"][caminho] = {
                        "emails": r.get("total_emails", 0),
                        "remessas": len(r.get("remessas", {})),
                    }

                progresso.progress(1.0, text="Coleta concluida.")

                for erro in acumulado["erros"]:
                    st.error(f"Falha na coleta: {erro}")

                if acumulado["pastas"]:
                    st.success(
                        f"Coleta concluida: {acumulado['total_emails']} "
                        f"e-mails, {len(acumulado['remessas'])} remessas "
                        f"em {len(acumulado['pastas'])} pasta(s)."
                    )
                    with st.expander("Detalhes por pasta"):
                        st.json(acumulado["pastas"])

                st.cache_data.clear()
        else:
            st.info("Clique em 'Carregar pastas' para listar as pastas do Outlook.")

    except Exception as e:
        st.error(f"Falha na coleta: {e}")

# ---- 2. conciliar
with abas[1]:
    st.subheader("Conciliar anexos baixados")
    st.caption(f"Processa a pasta raiz: {PASTA_ANEXOS_PADRAO}")

    if st.button("Conciliar", type="primary", key="btn_conciliar"):
        try:
            from pipeline import processar_pasta_raiz

            with st.spinner("Conciliando..."):
                retorno = processar_pasta_raiz(PASTA_ANEXOS_PADRAO)
            st.success(
                f"Processadas: {retorno.get('processadas', 0)} | "
                f"Falhas: {retorno.get('falhas', 0)}"
            )
            st.cache_data.clear()
        except Exception as e:
            st.error(f"Falha na conciliacao: {e}")

# ---- 3. dashboard
with abas[2]:
    st.subheader("Dashboard de acompanhamento")
    try:
        if "reembolsos" not in tabelas_existentes():
            st.warning("Tabela reembolsos nao encontrada no banco.")
        else:
            por_status = consultar(
                "SELECT status, COUNT(*) AS qtd FROM reembolsos GROUP BY status"
            )
            cols = st.columns(max(len(por_status), 1))
            for i, r in enumerate(por_status):
                cols[i].metric(r["status"], r["qtd"])
            st.caption(f"Total: {sum(r['qtd'] for r in por_status)}")

            st.divider()
            st.write("**Ultimas atualizacoes**")
            st.dataframe(
                consultar(
                    "SELECT numero_remessa, numero_nf, status, score, "
                    "valor_guia, valor_reembolso_cliente, data_atualizacao "
                    "FROM reembolsos ORDER BY data_atualizacao DESC LIMIT 50"
                ),
                width="stretch",
                hide_index=True,
            )
    except Exception as e:
        st.warning(f"Nao foi possivel carregar o dashboard: {e}")

# ---- 4. exportar
with abas[3]:
    st.subheader("Exportar para Excel")
    st.caption("Gera a planilha de saida com os reembolsos do banco.")

    if st.button("Gerar planilha", type="primary", key="btn_exportar"):
        try:
            from export_excel import exportar

            with st.spinner("Gerando planilha..."):
                caminho = exportar()
            st.success(f"Planilha gerada: {caminho}")
        except Exception as e:
            st.error(f"Falha na exportacao: {e}")

# ---- 5. estatisticas
with abas[4]:
    st.subheader("Estatisticas do banco")
    try:
        for nome in tabelas_existentes():
            qtd = consultar(f"SELECT COUNT(*) AS n FROM {nome}")[0]["n"]
            st.write(f"**{nome}**: {qtd} registros")
    except Exception as e:
        st.warning(f"Nao foi possivel ler as estatisticas: {e}")

# ---- 6. fila (com filtros)
with abas[5]:
    st.subheader("Fila de treinamento")
    try:
        itens = consultar(
            "SELECT id, tipo_documento, numero_remessa, confianca, "
            "caminho_arquivo, campos_json, criado_em FROM fila_treinamento "
            "WHERE resolvido = 0 ORDER BY id DESC"
        )
        itens = _preparar_itens(itens)

        total = len(itens)
        por_sev = {s: sum(1 for i in itens if i["_severidade"] == s) for s in range(4)}

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Pendentes", total)
        c2.metric("Criticos", por_sev[0])
        c3.metric("Alto", por_sev[1])
        c4.metric("Medio", por_sev[2])
        c5.metric("Baixo", por_sev[3])

        st.divider()
        st.write("**Filtros**")

        sev_filtro, tipos_filtro, remessas_filtro = _render_filtros(itens, "fila")

        filtrados = _filtrar_itens(itens, sev_filtro, tipos_filtro, remessas_filtro)

        st.caption(f"Mostrando {len(filtrados)} de {total} itens")

        if filtrados:
            filtrados.sort(key=lambda x: (x["_severidade"], -x["id"]))
            tabela = [
                {
                    "Severidade": i["_severidade_label"],
                    "ID": i["id"],
                    "Arquivo": i["arquivo"],
                    "Tipo": i["tipo_documento"],
                    "Remessa": i["numero_remessa"],
                    "Confianca": i["confianca"],
                    "Campos ausentes": ", ".join(i["_campos_ausentes"]) or "-",
                    "Motivo": i["_motivo"] or "-",
                }
                for i in filtrados
            ]
            st.dataframe(tabela, width="stretch", hide_index=True)
        else:
            st.info("Nenhum item corresponde aos filtros.")

    except Exception as e:
        st.warning(f"Nao foi possivel ler a fila: {e}")

# ---- 7. REVISAO MANUAL (fila + remessas em divergencia)
with abas[6]:
    st.subheader("Revisao manual")
    st.caption(
        "Tudo o que precisa de atencao humana em um lugar so. Escolha a visao abaixo."
    )

    visao = st.radio(
        "O que revisar",
        ["Campos faltando", "Remessas em divergencia"],
        horizontal=True,
        key="visao_revisao",
    )

    # ================================================================
    # VISAO 1 — CAMPOS FALTANDO (fila de treinamento)
    # ================================================================
    if visao == "Campos faltando":
        st.divider()
        try:
            pendentes = consultar(
                "SELECT id, tipo_documento, numero_remessa, confianca, "
                "caminho_arquivo, campos_json FROM fila_treinamento "
                "WHERE resolvido = 0 ORDER BY confianca ASC"
            )
            if not pendentes:
                st.info("Nenhum item pendente na fila.")
            else:
                pendentes = _preparar_itens(pendentes)
                total = len(pendentes)
                por_sev = {
                    s: sum(1 for p in pendentes if p["_severidade"] == s)
                    for s in range(4)
                }
                revisados = [
                    p
                    for p in pendentes
                    if _campos_do_json(p.get("campos_json")).get("editado_manualmente")
                ]
                pendentes_revisao = [
                    p
                    for p in pendentes
                    if not _campos_do_json(p.get("campos_json")).get(
                        "editado_manualmente"
                    )
                    and (p["_valor_ausente"] or p["_data_ausente"])
                ]

                c1, c2, c3, c4, c5 = st.columns(5)
                c1.metric("Pendentes", total)
                c2.metric("Criticos", por_sev[0], delta_color="inverse")
                c3.metric("Alto", por_sev[1], delta_color="inverse")
                c4.metric("Medio", por_sev[2], delta_color="inverse")
                c5.metric("Baixo", por_sev[3], delta_color="inverse")

                if por_sev[0] > 0:
                    st.warning(
                        f"**{por_sev[0]} item(ns) critico(s)** com valor "
                        "ausente. Aparecem primeiro na lista."
                    )

                st.divider()
                st.write("**Filtros**")
                sev_filtro, tipos_filtro, remessas_filtro = _render_filtros(
                    pendentes, "resolver"
                )

                col_btn1, col_btn2, col_btn3 = st.columns([1, 1, 1])
                if col_btn1.button(
                    "🔴 Mostrar so itens criticos",
                    key="btn_so_criticos",
                ):
                    st.session_state["resolver_sev"] = ["🔴 Critico (valor)"]
                    st.rerun()

                if col_btn2.button(
                    f"📝 So revisao manual ({len(pendentes_revisao)})",
                    key="btn_so_pendentes_revisao",
                    help="Mostra apenas itens sem valor ou sem data, "
                    "excluindo os ja revisados manualmente.",
                ):
                    st.session_state["resolver_so_revisao"] = True
                    st.rerun()

                if col_btn3.button(
                    "🔄 Limpar filtros",
                    key="btn_limpar_filtros",
                ):
                    st.session_state.pop("resolver_so_revisao", None)
                    st.session_state.pop("resolver_sev", None)
                    st.rerun()

                filtrados = _filtrar_itens(
                    pendentes, sev_filtro, tipos_filtro, remessas_filtro
                )

                if st.session_state.get("resolver_so_revisao"):
                    ids_revisao = {p["id"] for p in pendentes_revisao}
                    filtrados = [p for p in filtrados if p["id"] in ids_revisao]
                    st.info(
                        f"Filtro ativo: mostrando apenas os "
                        f"{len(filtrados)} itens pendentes de revisao."
                    )

                criticos = [p for p in pendentes if p["_severidade"] == 0]
                if criticos:
                    try:
                        excel_bytes = _gerar_excel_criticos(criticos)
                        st.download_button(
                            f"📥 Exportar {len(criticos)} criticos (Excel)",
                            data=excel_bytes,
                            file_name="itens_criticos.xlsx",
                            mime="application/vnd.openxmlformats-"
                            "officedocument.spreadsheetml.sheet",
                            key="btn_export_criticos",
                        )
                    except Exception as e:
                        st.caption(f"Export indisponivel: {e}")

                st.caption(f"Mostrando {len(filtrados)} de {total} itens")
                if not filtrados:
                    st.info("Nenhum item corresponde aos filtros.")
                else:
                    filtrados.sort(
                        key=lambda x: (
                            x["_severidade"],
                            x["confianca"] or 0,
                            -x["id"],
                        )
                    )
                    opcoes = {}
                    for p in filtrados:
                        campos_p = _campos_do_json(p.get("campos_json"))
                        marca_revisao = (
                            " ✅" if campos_p.get("editado_manualmente") else ""
                        )
                        label = (
                            f"{p['_severidade_label']} #{p['id']} — "
                            f"{Path(p['caminho_arquivo']).name} "
                            f"({p['tipo_documento']}, conf {p['confianca']})"
                            f"{marca_revisao}"
                        )
                        opcoes[label] = p

                    escolha = st.selectbox("Item", list(opcoes.keys()), key="sel_fila")
                    item = opcoes[escolha]
                    campos_atuais = _campos_do_json(item.get("campos_json"))
                    ja_revisado = bool(campos_atuais.get("editado_manualmente"))

                    if ja_revisado:
                        st.success(
                            "**Item ja revisado manualmente.** Os valores "
                            "abaixo foram digitados por uma pessoa e estao "
                            "protegidos contra sobrescrita do motor."
                        )
                    elif item["_severidade"] == 0:
                        st.error(
                            f"**Item critico — valor ausente.** "
                            f"{item['_motivo']}. Campos faltando: "
                            f"{', '.join(item['_campos_ausentes'])}."
                        )
                    elif item["_severidade"] == 1:
                        st.warning(
                            f"**Severidade alta — data ausente.** "
                            f"{item['_motivo']}. Campos faltando: "
                            f"{', '.join(item['_campos_ausentes'])}."
                        )
                    elif item["_severidade"] in (2, 3):
                        st.info(
                            f"**Campos secundarios ausentes.** "
                            f"{item['_motivo']}. Faltando: "
                            f"{', '.join(item['_campos_ausentes'])}."
                        )

                    col_doc, col_form = st.columns([1, 1])
                    with col_doc:
                        st.write("**Documento**")
                        mostrar_documento(item["caminho_arquivo"], altura=460)
                    with col_form:
                        st.write("**Edicao**")
                        tipos = [
                            "GUIA_FISCAL",
                            "COMPROVANTE_JADLOG",
                            "COMPROVANTE_REEMBOLSO",
                            "PLANILHA",
                            "DESCONHECIDO",
                        ]
                        tipo_atual = item["tipo_documento"]
                        idx_tipo = tipos.index(tipo_atual) if tipo_atual in tipos else 0
                        novo_tipo = st.selectbox(
                            "Tipo do documento",
                            tipos,
                            index=idx_tipo,
                            key=f"tipo_{item['id']}",
                        )

                        remessas = [
                            r["numero_remessa"]
                            for r in consultar(
                                "SELECT DISTINCT numero_remessa "
                                "FROM reembolsos "
                                "WHERE numero_remessa IS NOT NULL "
                                "ORDER BY numero_remessa DESC"
                            )
                        ]
                        rem_atual = item["numero_remessa"]
                        idx_rem = (
                            remessas.index(rem_atual) if rem_atual in remessas else 0
                        )
                        nova_remessa = st.selectbox(
                            "Remessa vinculada",
                            remessas or [rem_atual],
                            index=idx_rem if remessas else 0,
                            key=f"rem_{item['id']}",
                        )

                        st.divider()
                        st.write("**Campos extraidos**")

                        campos_do_tipo = CAMPOS_POR_TIPO.get(novo_tipo, [])
                        rotulos_campos = {
                            "valor_reembolso": "Valor do reembolso",
                            "data_reembolso": "Data do reembolso",
                            "cnpj_pagador": "CNPJ",
                            "nome_pagador": "Nome pagador",
                            "valor_pago_jadlog": "Valor pago Jadlog",
                            "data_pagamento_jadlog": "Data pagamento Jadlog",
                            "beneficiario": "Beneficiario",
                            "autenticacao": "Autenticacao",
                            "valor_guia": "Valor da guia",
                            "data_emissao": "Data de emissao",
                            "data_vencimento": "Data de vencimento",
                            "cnpj": "CNPJ contribuinte",
                            "numero_nf": "Numero da NF",
                            "numero_guia": "Numero da guia",
                        }
                        campos_numericos = {
                            "valor_reembolso",
                            "valor_pago_jadlog",
                            "valor_guia",
                        }

                        def _campo(
                            chave: str,
                            rotulo: str,
                            numerico: bool = False,
                        ):
                            valor = campos_atuais.get(chave)
                            vazio = valor in (None, "", 0, 0.0)
                            marca = "⚠️" if vazio else "✅"
                            label = f"{marca} {rotulo}"
                            if numerico:
                                return st.number_input(
                                    label,
                                    value=(float(valor) if not vazio else 0.0),
                                    step=0.01,
                                    format="%.2f",
                                    key=f"{chave}_{item['id']}",
                                )
                            return st.text_input(
                                label,
                                value=str(valor) if not vazio else "",
                                key=f"{chave}_{item['id']}",
                            )

                        editados = {}

                        if not campos_do_tipo:
                            st.info(
                                f"Tipo **{novo_tipo}** nao tem campos "
                                "obrigatorios definidos. Use o expander "
                                "abaixo para editar."
                            )
                        else:
                            faltando = [
                                c
                                for c, _sev, _cat in campos_do_tipo
                                if campos_atuais.get(c) in (None, "", 0, 0.0)
                            ]
                            if faltando:
                                st.warning(
                                    f"**{len(faltando)} campo(s) "
                                    f"faltando:** {', '.join(faltando)}"
                                )
                            else:
                                st.success("Todos os campos do tipo estao preenchidos.")

                            for chave, _sev, _cat in campos_do_tipo:
                                rotulo = rotulos_campos.get(chave, chave)
                                editados[chave] = _campo(
                                    chave,
                                    rotulo,
                                    numerico=chave in campos_numericos,
                                )

                        with st.expander(
                            "Ver todos os campos (inclusive fora do tipo)"
                        ):
                            st.caption(
                                "Campos que nao pertencem ao tipo "
                                "selecionado. Editar aqui so faz sentido "
                                "se o documento tiver informacao extra."
                            )
                            for chave, rotulo in rotulos_campos.items():
                                if chave in editados:
                                    continue
                                editados[chave] = _campo(
                                    chave,
                                    rotulo,
                                    numerico=chave in campos_numericos,
                                )

                        st.divider()
                        nova_confianca = st.slider(
                            "Confianca",
                            min_value=0,
                            max_value=100,
                            value=int(item["confianca"] or 0),
                            key=f"conf_{item['id']}",
                        )

                        marcar_resolvido = st.checkbox(
                            "Marcar como resolvido (sai da fila)",
                            value=True,
                            key=f"resolv_{item['id']}",
                            help="Marcado: o item sai da fila ao salvar. "
                            "Desmarque para manter na fila e conferir "
                            "depois.",
                        )

                        reprocessar_agora = st.checkbox(
                            "Reprocessar a remessa ao salvar",
                            value=True,
                            key=f"reproc_{item['id']}",
                        )

                        if st.button("Salvar e reprocessar", type="primary"):
                            novos_campos = dict(campos_atuais)
                            for k, v in editados.items():
                                if v not in (None, ""):
                                    novos_campos[k] = v

                            novos_campos["editado_manualmente"] = True

                            novo_json = _ler_json_bruto(item.get("campos_json"))
                            novo_json["campos"] = novos_campos
                            novo_json["confianca"] = nova_confianca
                            novo_json["editado_manualmente"] = True
                            novo_json["faltando"] = [
                                c
                                for c in (
                                    "valor_reembolso",
                                    "data_reembolso",
                                )
                                if not novos_campos.get(c)
                            ]

                            executar(
                                "UPDATE fila_treinamento "
                                "SET campos_json = ?, confianca = ?, "
                                "tipo_documento = ?, numero_remessa = ?, "
                                "resolvido = ? WHERE id = ?",
                                (
                                    json.dumps(novo_json, ensure_ascii=False),
                                    nova_confianca,
                                    novo_tipo,
                                    nova_remessa,
                                    1 if marcar_resolvido else 0,
                                    item["id"],
                                ),
                            )

                            if nova_remessa and (
                                novos_campos.get("valor_reembolso")
                                or novos_campos.get("data_reembolso")
                            ):
                                executar(
                                    "UPDATE reembolsos SET "
                                    "valor_reembolso_cliente = ?, "
                                    "data_reembolso_cliente = ?, "
                                    "data_atualizacao = datetime('now') "
                                    "WHERE numero_remessa = ?",
                                    (
                                        novos_campos.get("valor_reembolso"),
                                        novos_campos.get("data_reembolso"),
                                        nova_remessa,
                                    ),
                                )

                            status_msg = (
                                "marcado como resolvido"
                                if marcar_resolvido
                                else "mantido na fila"
                            )
                            st.success(
                                f"Item #{item['id']} atualizado: "
                                f"tipo={novo_tipo}, "
                                f"remessa={nova_remessa}, "
                                f"conf={nova_confianca}, {status_msg}."
                            )

                            if reprocessar_agora and nova_remessa:
                                try:
                                    from reprocessar import (
                                        reprocessar_lote,
                                    )

                                    with st.spinner(f"Reprocessando {nova_remessa}..."):
                                        ret = reprocessar_lote([nova_remessa])
                                    st.info(
                                        f"Reprocessamento: "
                                        f"{ret.get('processadas', 0)} "
                                        f"processadas, "
                                        f"{ret.get('falhas', 0)} falhas."
                                    )
                                    for r in ret.get("resultados", []):
                                        st.write(
                                            f"**{r['remessa']}** → "
                                            f"{r['status']} "
                                            f"(score {r.get('score')})"
                                        )
                                except Exception as e:
                                    st.warning(f"Reprocessamento falhou: {e}")

                            st.cache_data.clear()
                            st.rerun()

            # Lote
            st.divider()
            st.write("**Reprocessamento em lote**")
            st.caption(
                "Roda o OCR e a extracao em todos os itens pendentes. "
                "Use o dry-run para ver o resultado antes de gravar. "
                "Itens revisados manualmente sao preservados."
            )
            col_lim, col_dry = st.columns(2)
            limite_lote = col_lim.number_input(
                "Limite de itens (0 = todos)",
                min_value=0,
                value=0,
                key="reproc_limite",
            )
            dry_run = col_dry.checkbox(
                "Dry-run (nao grava)",
                value=True,
                key="reproc_dry",
            )
            if st.button("Reprocessar fila", type="primary", key="btn_reproc_fila"):
                try:
                    from reprocessar_fila import reprocessar

                    with st.spinner("Reprocessando a fila..."):
                        ret = reprocessar(
                            limite=int(limite_lote) or None,
                            aplicar=not dry_run,
                        )
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Processados", ret["total"])
                    c2.metric("Resolvidos", ret["resolvidos"])
                    c3.metric(
                        "Falhando",
                        ret["faltando"]
                        if "faltando" in ret
                        else ret.get("falhando", 0),
                    )
                    c4.metric("Erros", ret["erros"])
                    if ret["aplicado"]:
                        st.success("Campos gravados no banco.")
                    else:
                        st.info("Dry-run: nada foi gravado.")
                    if ret.get("detalhes"):
                        tabela = []
                        for d in ret["detalhes"]:
                            if d["status"] == "RESOLVIDO":
                                marca = "🟢"
                            elif d["status"] == "ERRO":
                                marca = "⚫"
                            else:
                                marca = "🔴"
                            tabela.append(
                                {
                                    "Status": f"{marca} {d['status']}",
                                    "ID": d["id"],
                                    "Arquivo": d["arquivo"],
                                    "Tipo": d["tipo"],
                                    "Confianca": d["confianca"],
                                    "Faltando": ", ".join(d["faltando"]) or "-",
                                }
                            )
                        st.dataframe(tabela, width="stretch", hide_index=True)
                        with st.expander("Ver campos extraidos por item"):
                            for d in ret["detalhes"]:
                                st.write(
                                    f"**#{d['id']} — {d['arquivo']}** ({d['status']})"
                                )
                                st.json(d["depois"])
                    st.cache_data.clear()
                    if ret["aplicado"]:
                        st.rerun()
                except Exception as e:
                    st.error(f"Falha no reprocessamento: {e}")
        except Exception as e:
            st.warning(f"Nao foi possivel carregar a fila: {e}")

    # ================================================================
    # VISAO 2 — REMESSAS EM DIVERGENCIA
    # ================================================================
    else:
        st.divider()
        try:
            if "reembolsos" not in tabelas_existentes():
                st.info("Tabela reembolsos nao encontrada.")
            else:
                divergentes = consultar(
                    "SELECT * FROM reembolsos "
                    "WHERE status LIKE 'DIVERGENCIA%' "
                    "OR status = 'REVISAO_MANUAL' "
                    "OR status = 'AGUARDANDO_REEMBOLSO' "
                    "ORDER BY data_atualizacao DESC"
                )
                col_m1, col_m2, col_m3 = st.columns(3)
                col_m1.metric("Em divergencia", len(divergentes))
                col_m2.metric(
                    "Divergencia de valor",
                    len([d for d in divergentes if "VALOR" in (d.get("status") or "")]),
                )
                col_m3.metric(
                    "Divergencia de CNPJ",
                    len([d for d in divergentes if "CNPJ" in (d.get("status") or "")]),
                )

                if not divergentes:
                    st.success("Nenhuma remessa pendente de validacao.")
                else:
                    st.divider()
                    status_disponiveis = sorted(
                        {d.get("status") for d in divergentes if d.get("status")}
                    )
                    status_filtro = st.multiselect(
                        "Filtrar por status",
                        status_disponiveis,
                        key="val_status",
                    )
                    if status_filtro:
                        divergentes = [
                            d for d in divergentes if d.get("status") in status_filtro
                        ]

                    if not divergentes:
                        st.info("Nenhuma remessa corresponde ao filtro.")
                    else:
                        opcoes = {
                            f"{d.get('numero_remessa')} — "
                            f"{d.get('status')} "
                            f"(score {d.get('score')}, "
                            f"NF {d.get('numero_nf')})": d
                            for d in divergentes
                        }
                        escolha = st.selectbox(
                            "Remessa", list(opcoes.keys()), key="sel_val"
                        )
                        d = opcoes[escolha]

                        c1, c2, c3, c4 = st.columns(4)
                        c1.metric(
                            "Valor da guia",
                            f"R$ {d.get('valor_guia') or 0:.2f}",
                        )
                        c2.metric(
                            "Reembolso cliente",
                            f"R$ {d.get('valor_reembolso_cliente') or 0:.2f}",
                        )
                        c3.metric(
                            "Esperado",
                            f"R$ {d.get('valor_reembolso_esperado') or 0:.2f}",
                        )
                        c4.metric("Score", d.get("score") or 0)

                        st.divider()
                        col1, col2, col3 = st.columns(3)
                        with col1:
                            st.write("**Guia fiscal**")
                            mostrar_documento(d.get("caminho_guia"), altura=380)
                        with col2:
                            st.write("**Comprovante Jadlog**")
                            mostrar_documento(d.get("caminho_comp_jadlog"), altura=380)
                        with col3:
                            st.write("**Comprovante do cliente**")
                            mostrar_documento(d.get("caminho_comp_cliente"), altura=380)

                        st.divider()
                        obs = st.text_area(
                            "Observacao da validacao",
                            key=f"obs_{d['id']}",
                            placeholder="Motivo da aprovacao ou da manutencao...",
                        )

                        b1, b2, b3 = st.columns(3)
                        if b1.button("Aprovar como conferido", type="primary"):
                            executar(
                                "UPDATE reembolsos SET "
                                "status = 'CONFERIDO', "
                                "data_atualizacao = datetime('now') "
                                "WHERE id = ?",
                                (d["id"],),
                            )
                            st.success(
                                f"Remessa {d['numero_remessa']} aprovada "
                                "como CONFERIDO."
                            )
                            if obs:
                                st.caption(f"Obs: {obs}")
                            st.cache_data.clear()
                            st.rerun()

                        if b2.button("Marcar para revisao manual"):
                            executar(
                                "UPDATE reembolsos SET "
                                "status = 'REVISAO_MANUAL', "
                                "data_atualizacao = datetime('now') "
                                "WHERE id = ?",
                                (d["id"],),
                            )
                            st.info(
                                f"Remessa {d['numero_remessa']} marcada para revisao."
                            )
                            st.cache_data.clear()
                            st.rerun()

                        if b3.button("Reprocessar esta remessa"):
                            try:
                                from reprocessar import reprocessar_lote

                                with st.spinner("Reprocessando..."):
                                    ret = reprocessar_lote([d["numero_remessa"]])
                                st.info(
                                    f"{ret.get('processadas', 0)} "
                                    f"processadas, "
                                    f"{ret.get('falhas', 0)} falhas."
                                )
                                for r in ret.get("resultados", []):
                                    st.write(
                                        f"**{r['remessa']}** → "
                                        f"{r['status']} "
                                        f"(score {r.get('score')})"
                                    )
                                st.cache_data.clear()
                            except Exception as e:
                                st.warning(f"Reprocessamento falhou: {e}")
        except Exception as e:
            st.warning(f"Nao foi possivel carregar a validacao: {e}")

# ---- 8. layouts
with abas[7]:
    st.subheader("Layouts treinados")
    try:
        if "layouts" in tabelas_existentes():
            layouts = consultar("SELECT * FROM layouts ORDER BY id DESC")
            st.metric("Layouts registrados", len(layouts))
            if layouts:
                st.dataframe(layouts, width="stretch", hide_index=True)
            else:
                st.info("Nenhum layout registrado ainda.")
        else:
            st.info("Tabela de layouts nao encontrada no banco.")
    except Exception as e:
        st.warning(f"Nao foi possivel ler os layouts: {e}")


# ---- 09. reprocessar
with abas[9]:
    st.subheader("Reprocessar remessas")
    try:
        remessas = [
            r["numero_remessa"]
            for r in consultar(
                "SELECT DISTINCT numero_remessa FROM reembolsos "
                "WHERE numero_remessa IS NOT NULL ORDER BY numero_remessa DESC"
            )
        ]
        selecionadas = st.multiselect("Remessas", remessas, key="sel_reproc")

        if st.button("Reprocessar", type="primary", disabled=not selecionadas):
            from reprocessar import reprocessar_lote

            with st.spinner("Processando..."):
                retorno = reprocessar_lote(selecionadas)
            st.success(
                f"Processadas: {retorno.get('processadas', 0)} | "
                f"Falhas: {retorno.get('falhas', 0)}"
            )
            for r in retorno.get("resultados", []):
                with st.expander(f"{r['remessa']} — {r['status']}"):
                    st.write(f"**Score:** {r.get('score')}")
                    st.write(f"**Guia:** {r.get('guia')}")
                    st.write(f"**Comp. Jadlog:** {r.get('comp_jadlog')}")
                    st.write(f"**Comp. cliente:** {r.get('comp_cliente')}")
            st.cache_data.clear()
    except Exception as e:
        st.warning(f"Nao foi possivel reprocessar: {e}")

# ---- 10. backup
with abas[10]:
    st.subheader("Backup do banco")
    motivo = st.text_input("Motivo", "manual")

    if st.button("Fazer backup", type="primary", key="btn_backup"):
        try:
            from backup import criar_backup

            with st.spinner("Copiando..."):
                retorno = criar_backup(motivo=motivo)
            st.success(f"Backup criado: {retorno}")
        except Exception as e:
            st.error(f"Falha no backup: {e}")

    st.divider()
    st.write("**Backups existentes**")
    try:
        from backup import listar_backups

        backups = listar_backups()
        if backups:
            st.dataframe(backups, width="stretch", hide_index=True)
        else:
            st.caption("Nenhum backup ainda.")
    except Exception as e:
        st.warning(f"Nao foi possivel listar os backups: {e}")

# ---- 11. reprocessar fila (aba nova)
with abas[10]:
    st.subheader("Reprocessar a fila de treinamento")
    st.caption(
        "Roda o OCR e a extracao de novo em todos os itens pendentes. "
        "Use o dry-run para ver o resultado antes de gravar."
    )

    try:
        pendentes_total = consultar(
            "SELECT COUNT(*) AS n FROM fila_treinamento WHERE resolvido = 0"
        )[0]["n"]
    except Exception:
        pendentes_total = 0

    st.metric("Itens pendentes na fila", pendentes_total)

    if pendentes_total == 0:
        st.success("Fila vazia. Nada a reprocessar.")
    else:
        col_lim, col_dry = st.columns(2)

        limite_lote = col_lim.number_input(
            "Limite de itens (0 = todos)",
            min_value=0,
            value=0,
            step=5,
            key="aba12_limite",
        )
        dry_run = col_dry.checkbox(
            "Dry-run (nao grava)",
            value=True,
            key="aba12_dry",
        )

        if st.button("Reprocessar fila", type="primary", key="aba12_btn"):
            try:
                from reprocessar_fila import reprocessar

                with st.spinner("Reprocessando a fila..."):
                    ret = reprocessar(
                        limite=int(limite_lote) or None,
                        aplicar=not dry_run,
                    )

                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Processados", ret["total"])
                c2.metric("Resolvidos", ret["resolvidos"])
                c3.metric("Falhando", ret["falhando"])
                c4.metric("Erros", ret["erros"])

                if ret["aplicado"]:
                    st.success("Campos gravados no banco.")
                else:
                    st.info("Dry-run: nada foi gravado.")

                detalhes = ret.get("detalhes") or []

                if detalhes:
                    st.divider()
                    st.write("**Antes e depois por item**")

                    tabela = []
                    for d in detalhes:
                        antes = d.get("antes") or {}
                        depois = d.get("depois") or {}

                        valor_antes = (
                            antes.get("valor_reembolso")
                            or antes.get("valor_pago_jadlog")
                            or antes.get("valor_guia")
                        )
                        valor_depois = (
                            depois.get("valor_reembolso")
                            or depois.get("valor_pago_jadlog")
                            or depois.get("valor_guia")
                        )

                        tabela.append(
                            {
                                "Status": d.get("status"),
                                "ID": d.get("id"),
                                "Arquivo": d.get("arquivo"),
                                "Tipo": d.get("tipo"),
                                "Valor antes": valor_antes,
                                "Valor depois": valor_depois,
                                "Faltando": ", ".join(d.get("faltando") or []) or "-",
                                "Conf.": d.get("confianca"),
                            }
                        )

                    st.dataframe(tabela, width="stretch", hide_index=True)

                    falhando = [d for d in detalhes if d.get("status") == "FALHANDO"]
                    if falhando:
                        st.warning(
                            f"{len(falhando)} item(ns) continuam sem todos os "
                            "campos. Veja a coluna 'Faltando' acima."
                        )

                st.cache_data.clear()

            except Exception as e:
                st.error(f"Falha no reprocessamento: {e}")


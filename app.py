"""Interface Streamlit para conciliacao de reembolsos Jadlog.

Camada de tela apenas. Toda a regra de negocio fica nos modulos existentes:
outlook_reader, pipeline, export_excel, backup, reprocessar.
"""

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
        "7. Resolver fila",
        "8. Layouts",
        "9. Validacao",
        "10. Reprocessar",
        "11. Backup",
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

# ---- 6. fila
with abas[5]:
    st.subheader("Fila de treinamento")
    try:
        itens = consultar(
            "SELECT id, tipo_documento, numero_remessa, confianca, "
            "caminho_arquivo, criado_em FROM fila_treinamento "
            "WHERE resolvido = 0 ORDER BY id DESC"
        )
        st.metric("Itens pendentes", len(itens))
        if itens:
            for i in itens:
                i["arquivo"] = Path(i["caminho_arquivo"]).name
            st.dataframe(itens, width="stretch", hide_index=True)
        else:
            st.info("Fila vazia.")
    except Exception as e:
        st.warning(f"Nao foi possivel ler a fila: {e}")

# ---- 7. resolver fila (EDICAO COMPLETA)
with abas[6]:
    st.subheader("Resolver itens da fila")
    st.caption(
        "Edite os campos extraidos, reclassifique o documento e vincule a "
        "remessa. Ao salvar, a remessa e reprocessada automaticamente."
    )

    try:
        pendentes = consultar(
            "SELECT id, tipo_documento, numero_remessa, confianca, "
            "caminho_arquivo, campos_json FROM fila_treinamento "
            "WHERE resolvido = 0 ORDER BY confianca ASC"
        )

        if not pendentes:
            st.info("Nenhum item pendente na fila.")
        else:
            st.metric("Itens pendentes", len(pendentes))

            opcoes = {
                f"#{p['id']} — {Path(p['caminho_arquivo']).name} "
                f"({p['tipo_documento']}, conf {p['confianca']})": p
                for p in pendentes
            }
            escolha = st.selectbox("Item", list(opcoes.keys()), key="sel_fila")
            item = opcoes[escolha]

            # campos atuais
            try:
                dados = json.loads(item["campos_json"] or "{}")
            except Exception:
                dados = {}
            campos_atuais = dados.get("campos", dados) or {}

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
                        "SELECT DISTINCT numero_remessa FROM reembolsos "
                        "WHERE numero_remessa IS NOT NULL "
                        "ORDER BY numero_remessa DESC"
                    )
                ]
                rem_atual = item["numero_remessa"]
                idx_rem = remessas.index(rem_atual) if rem_atual in remessas else 0
                nova_remessa = st.selectbox(
                    "Remessa vinculada",
                    remessas or [rem_atual],
                    index=idx_rem if remessas else 0,
                    key=f"rem_{item['id']}",
                )

                st.divider()
                st.write("**Campos extraidos**")

                def _campo(chave: str, rotulo: str, tipo_input: str = "text"):
                    valor = campos_atuais.get(chave)
                    if tipo_input == "number":
                        return st.number_input(
                            rotulo,
                            value=float(valor) if valor not in (None, "") else 0.0,
                            step=0.01,
                            format="%.2f",
                            key=f"{chave}_{item['id']}",
                        )
                    return st.text_input(
                        rotulo,
                        value=str(valor) if valor not in (None, "") else "",
                        key=f"{chave}_{item['id']}",
                    )

                editados = {}
                editados["valor_reembolso"] = _campo(
                    "valor_reembolso", "Valor do reembolso", "number"
                )
                editados["valor_guia"] = _campo("valor_guia", "Valor da guia", "number")
                editados["cnpj_pagador"] = _campo("cnpj_pagador", "CNPJ")
                editados["nome_pagador"] = _campo("nome_pagador", "Nome pagador")
                editados["data_reembolso"] = _campo(
                    "data_reembolso", "Data do reembolso"
                )
                editados["data_pagamento_jadlog"] = _campo(
                    "data_pagamento_jadlog", "Data pagamento Jadlog"
                )
                editados["numero_nf"] = _campo("numero_nf", "Numero da NF")
                editados["numero_guia"] = _campo("numero_guia", "Numero da guia")

                st.divider()
                nova_confianca = st.slider(
                    "Confianca",
                    min_value=0,
                    max_value=100,
                    value=int(item["confianca"] or 0),
                    key=f"conf_{item['id']}",
                )

                reprocessar_agora = st.checkbox(
                    "Reprocessar a remessa ao salvar",
                    value=True,
                    key=f"reproc_{item['id']}",
                )

                if st.button("Salvar e reprocessar", type="primary"):
                    # atualiza campos_json preservando o que existia
                    novos_campos = dict(campos_atuais)
                    for k, v in editados.items():
                        if v not in (None, ""):
                            novos_campos[k] = v

                    novo_json = dict(dados)
                    novo_json["campos"] = novos_campos
                    novo_json["confianca"] = nova_confianca
                    novo_json["editado_manualmente"] = True

                    executar(
                        "UPDATE fila_treinamento "
                        "SET campos_json = ?, confianca = ?, "
                        "tipo_documento = ?, numero_remessa = ?, "
                        "resolvido = 1 WHERE id = ?",
                        (
                            json.dumps(novo_json, ensure_ascii=False),
                            nova_confianca,
                            novo_tipo,
                            nova_remessa,
                            item["id"],
                        ),
                    )

                    st.success(
                        f"Item #{item['id']} atualizado: tipo={novo_tipo}, "
                        f"remessa={nova_remessa}, conf={nova_confianca}."
                    )

                    if reprocessar_agora and nova_remessa:
                        try:
                            from reprocessar import reprocessar_lote

                            with st.spinner(f"Reprocessando {nova_remessa}..."):
                                ret = reprocessar_lote([nova_remessa])
                            st.info(
                                f"Reprocessamento: "
                                f"{ret.get('processadas', 0)} processadas, "
                                f"{ret.get('falhas', 0)} falhas."
                            )
                            for r in ret.get("resultados", []):
                                st.write(
                                    f"**{r['remessa']}** → {r['status']} "
                                    f"(score {r.get('score')})"
                                )
                        except Exception as e:
                            st.warning(f"Reprocessamento falhou: {e}")

                    st.cache_data.clear()
                    st.rerun()
    except Exception as e:
        st.warning(f"Nao foi possivel carregar a fila: {e}")

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

# ---- 9. VALIDACAO (aba nova)
with abas[8]:
    st.subheader("Validacao de divergencias")
    st.caption(
        "Revise as remessas em divergencia, compare os documentos e decida: "
        "aprovar como conferido ou manter a divergencia."
    )

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

                opcoes = {
                    f"{d.get('numero_remessa')} — {d.get('status')} "
                    f"(score {d.get('score')}, NF {d.get('numero_nf')})": d
                    for d in divergentes
                }
                escolha = st.selectbox("Remessa", list(opcoes.keys()), key="sel_val")
                d = opcoes[escolha]

                # resumo
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Valor da guia", f"R$ {d.get('valor_guia') or 0:.2f}")
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
                        "UPDATE reembolsos SET status = 'CONFERIDO', "
                        "data_atualizacao = datetime('now') WHERE id = ?",
                        (d["id"],),
                    )
                    st.success(
                        f"Remessa {d['numero_remessa']} aprovada como CONFERIDO."
                    )
                    if obs:
                        st.caption(f"Obs: {obs}")
                    st.cache_data.clear()
                    st.rerun()

                if b2.button("Marcar para revisao manual"):
                    executar(
                        "UPDATE reembolsos SET status = 'REVISAO_MANUAL', "
                        "data_atualizacao = datetime('now') WHERE id = ?",
                        (d["id"],),
                    )
                    st.info(f"Remessa {d['numero_remessa']} marcada para revisao.")
                    st.cache_data.clear()
                    st.rerun()

                if b3.button("Reprocessar esta remessa"):
                    try:
                        from reprocessar import reprocessar_lote

                        with st.spinner("Reprocessando..."):
                            ret = reprocessar_lote([d["numero_remessa"]])
                        st.info(
                            f"{ret.get('processadas', 0)} processadas, "
                            f"{ret.get('falhas', 0)} falhas."
                        )
                        for r in ret.get("resultados", []):
                            st.write(
                                f"**{r['remessa']}** → {r['status']} "
                                f"(score {r.get('score')})"
                            )
                        st.cache_data.clear()
                    except Exception as e:
                        st.warning(f"Reprocessamento falhou: {e}")
    except Exception as e:
        st.warning(f"Nao foi possivel carregar a validacao: {e}")

# ---- 10. reprocessar
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

# ---- 11. backup
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

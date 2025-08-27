
# app_streamlit_hibrido.py
# Um único app que:
#  (a) Mostra tabelas editáveis ligadas ao Postgres
#  (b) Roda o seu script de e-mail para alimentar diretamente o banco (DB mode)
#
# Rodar:
#   pip install streamlit pandas sqlalchemy psycopg2-binary python-dotenv
#   python -m streamlit run app_streamlit_hibrido.py

import os
import sys
import importlib.util
from pathlib import Path

import streamlit as st
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from db import engine, SessionLocal, Andamento, Publicacao, Agenda

st.set_page_config(page_title="E-mails → Banco + Tabelas Editáveis", layout="wide")
st.title("Planilha de Prazos Geral")

# --- Sidebar: carregar script e acionar ingestão ---
st.sidebar.header("Ingestão por Script de E-mail")
script_path = st.sidebar.text_input("Caminho do script (.py)", value="scrap_email.py")
col_a, col_b = st.sidebar.columns(2)
with col_a:
    btn_load = st.button("📦 Carregar script")
with col_b:
    btn_run = st.button("▶️ Rodar ingestão (DB mode)")

state = st.session_state
if "mod" not in state: state.mod = None

def load_module(path: str):
    mod_name = Path(path).stem
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)  # type: ignore
        return mod
    raise RuntimeError("Falha ao carregar o script")

if btn_load:
    try:
        state.mod = load_module(script_path)
        # força DB mode e desliga Streamlit mode para o script gravar no banco
        if hasattr(state.mod, "set_streamlit_mode"):
            state.mod.set_streamlit_mode(False)
        if hasattr(state.mod, "USE_DB"):
            state.mod.USE_DB = True
        st.success(f"Script '{script_path}' carregado com sucesso (DB mode ativado).")
    except Exception as e:
        st.exception(e)

if btn_run:
    try:
        if state.mod is None:
            state.mod = load_module(script_path)
            if hasattr(state.mod, "set_streamlit_mode"):
                state.mod.set_streamlit_mode(False)
            if hasattr(state.mod, "USE_DB"):
                state.mod.USE_DB = True
        if hasattr(state.mod, "buscar_e_processar_emails"):
            with st.spinner("Ingerindo e-mails e gravando direto no banco..."):
                state.mod.buscar_e_processar_emails()
            st.success("Ingestão concluída! Atualize as tabelas abaixo com o botão 🔄.")
        else:
            st.error("Função 'buscar_e_processar_emails' não encontrada no script.")
    except Exception as e:
        st.exception(e)

st.divider()

# --- Carregar dados do banco ---
#st.subheader("📊 Tabelas do Banco (Editáveis)")
c1, c2 = st.columns([1,1])
with c1:
    refresh = st.button("🔄 Atualizar tabelas do banco", use_container_width=True)
with c2:
    st.caption("Edite as linhas e clique em Salvar para persistir no Postgres.")

if "df1" not in state or refresh:
    state.df1 = pd.read_sql("select * from andamentos order by created_at desc", engine)
    state.df2 = pd.read_sql("select * from publicacoes order by created_at desc", engine)
    state.df3 = pd.read_sql("select * from agenda order by created_at desc", engine)
    if "df1" not in state:
        st.success("Tabelas carregadas.")

tab1, tab2, tab3 = st.tabs(["ANDAMENTOS", "PUBLICAÇÕES", "ANOTAR NA AGENDA E AVISAR"])

with tab1:
    st.markdown("### ✏️ ANDAMENTOS")
    state.df1 = st.data_editor(state.df1, num_rows="dynamic", use_container_width=True, height=420)
    if st.button("💾 Salvar ANDAMENTOS"):
        with SessionLocal() as db:
            for _, row in state.df1.iterrows():
                values = row.to_dict()
                rec_id = values.pop("id", None)
                if pd.notna(rec_id):
                    db.query(Andamento).filter(Andamento.id==int(rec_id)).update(values)
                else:
                    db.add(Andamento(**values))
            db.commit()
        st.success("ANDAMENTOS salvos no banco.")

with tab2:
    st.markdown("### ✏️ PUBLICAÇÕES")
    state.df2 = st.data_editor(state.df2, num_rows="dynamic", use_container_width=True, height=420)
    if st.button("💾 Salvar PUBLICAÇÕES"):
        with SessionLocal() as db:
            for _, row in state.df2.iterrows():
                values = row.to_dict()
                rec_id = values.pop("id", None)
                if pd.notna(rec_id):
                    db.query(Publicacao).filter(Publicacao.id==int(rec_id)).update(values)
                else:
                    db.add(Publicacao(**values))
            db.commit()
        st.success("PUBLICAÇÕES salvas no banco.")

with tab3:
    st.markdown("### ✏️ AGENDA")
    state.df3 = st.data_editor(state.df3, num_rows="dynamic", use_container_width=True, height=420)
    if st.button("💾 Salvar AGENDA"):
        with SessionLocal() as db:
            for _, row in state.df3.iterrows():
                values = row.to_dict()
                rec_id = values.pop("id", None)
                if pd.notna(rec_id):
                    db.query(Agenda).filter(Agenda.id==int(rec_id)).update(values)
                else:
                    db.add(Agenda(**values))
            db.commit()
        st.success("AGENDA salva no banco.")

st.caption("Certifique-se de definir DATABASE_URL (env, st.secrets ou .env).")

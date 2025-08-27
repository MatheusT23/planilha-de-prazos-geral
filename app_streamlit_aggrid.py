# app_streamlit_aggrid.py
# Edição estável com st-aggrid (sem sumir a 1ª tentativa).
# Rodar:
#   pip install streamlit streamlit-aggrid pandas sqlalchemy psycopg2-binary python-dotenv beautifulsoup4
#   python -m streamlit run app_streamlit_aggrid.py

import sys
import importlib.util
from pathlib import Path
import datetime as _dt

import streamlit as st
import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import MetaData, Table, text

# st-aggrid
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, DataReturnMode

from db import engine, SessionLocal, Andamento, Publicacao, Agenda


st.set_page_config(page_title="E-mails → Banco (AGGrid estável)", layout="wide")
st.title("Planilha de Prazos Geral — modo AGGrid")

# ----- Sidebar: rodar ingestão (opcional) -----
st.sidebar.header("Ingestão por Script de E-mail (opcional)")
script_path = st.sidebar.text_input("Caminho do script (.py)", value="scrap_email.py")
col_a, col_b = st.sidebar.columns(2)
with col_a:
    btn_load = st.sidebar.button("📦 Carregar script", use_container_width=True)
with col_b:
    btn_run  = st.sidebar.button("▶️ Rodar ingestão (DB mode)", use_container_width=True)

def load_module(path: str):
    mod_name = Path(path).stem
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("spec inválida")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod

if btn_load or btn_run:
    try:
        mod = load_module(script_path)
        st.success(f"Módulo '{script_path}' carregado.")
        if btn_run:
            if hasattr(mod, "set_streamlit_mode"):
                try:
                    mod.set_streamlit_mode(True)  # type: ignore
                except Exception:
                    pass
            if hasattr(mod, "buscar_e_processar_emails"):
                with st.spinner("Executando ingestão de e-mails…"):
                    mod.buscar_e_processar_emails()  # type: ignore
                st.success("Ingestão concluída! Atualize as tabelas abaixo.")
            else:
                st.error("Função 'buscar_e_processar_emails' não encontrada.")
    except Exception as e:
        st.exception(e)

st.divider()

st.sidebar.markdown("---")
st.sidebar.subheader("Esquema: Adicionar coluna")
with st.sidebar.form("add_column_form", clear_on_submit=False):
    table_choice = st.selectbox("Tabela", ["andamentos","publicacoes","agenda"])
    col_name = st.text_input("Nome da coluna (snake_case)")
    col_type = st.selectbox("Tipo", ["TEXT","DATE","INTEGER"])
    allow_null = st.checkbox("Permitir NULL", value=True)
    btn_addcol = st.form_submit_button("➕ Adicionar coluna")

if btn_addcol:
    # validação simples
    valid = True
    if not col_name or not col_name.strip():
        st.sidebar.error("Informe um nome de coluna.")
        valid = False
    if not col_name.replace("_","").isalnum():
        st.sidebar.error("Use apenas letras, números e '_' no nome da coluna.")
        valid = False
    try:
        if valid:
            null_sql = "" if allow_null else " NOT NULL"
            sql = f'ALTER TABLE {table_choice} ADD COLUMN IF NOT EXISTS "{col_name}" {col_type}{null_sql};'
            with engine.begin() as conn:
                conn.execute(text(sql))
            st.sidebar.success(f"Coluna '{col_name}' adicionada em {table_choice}. Clique em 🔄 Atualizar.")
    except Exception as e:
        st.sidebar.error(f"Erro ao adicionar coluna: {e}")

st.subheader("📊 Tabelas (edição estável via AGGrid)")
refresh = st.button("🔄 Atualizar", use_container_width=True)

# ----- Carregar dataframes do DB -----
@st.cache_data(show_spinner=False)
def _load_tables():
    df1 = pd.read_sql("select * from andamentos order by created_at desc", engine)
    df2 = pd.read_sql("select * from publicacoes order by created_at desc", engine)
    df3 = pd.read_sql("select * from agenda order by created_at desc", engine)
    # normalizações: data como string YYYY-MM-DD para edição consistente no grid
    for _df in (df1, df2, df3):
        if "data" in _df.columns:
            _df["data"] = pd.to_datetime(_df["data"], errors="coerce").dt.date.astype("string")
    return df1, df2, df3

if refresh or "dfs" not in st.session_state:
    st.session_state["dfs"] = _load_tables()
df1, df2, df3 = st.session_state["dfs"]

tab1, tab2, tab3 = st.tabs(["ANDAMENTOS", "PUBLICAÇÕES", "ANOTAR NA AGENDA E AVISAR"])

def _grid(df: pd.DataFrame, editable_cols, key: str):
    """Renderiza um AgGrid estável com colunas editáveis declaradas."""
    if "id" in df.columns:
        df = df.sort_values(["created_at","id"], ascending=[False, False]).reset_index(drop=True)
    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_grid_options(ensureDomOrder=True, animateRows=True)
    gb.configure_default_column(resizable=True, filter=True, sortable=True, editable=False)
    # habilita edição apenas em editable_cols
    for c in df.columns:
        if c in ("id", "created_at"):
            gb.configure_column(c, editable=False)
        elif c == "data":
            # tratamos data como texto controlado YYYY-MM-DD
            gb.configure_column(c, header_name="data (YYYY-MM-DD)", editable=True)
        elif c in editable_cols:
            gb.configure_column(c, editable=True)
        else:
            gb.configure_column(c, editable=False)
    # permitir adicionar linhas pelo botão custom (abaixo); grid em modo VALUE_CHANGED
    go = gb.build()
    grid = AgGrid(
        df,
        gridOptions=go,
        update_mode=GridUpdateMode.VALUE_CHANGED,
        data_return_mode=DataReturnMode.FILTERED_AND_SORTED,
        allow_unsafe_jscode=False,
        fit_columns_on_grid_load=True,
        theme="streamlit",
        key=key,
        height=440,
    )
    return grid["data"]  # dataframe refletindo a visão atual (com edições)

def _parse_date_str(v):
    if v is None or (isinstance(v, float) and pd.isna(v)) or (isinstance(v, str) and v.strip()==""):
        return None
    if isinstance(v, _dt.date):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return pd.to_datetime(s, format=fmt, errors="raise").date()
        except Exception:
            pass
    try:
        return pd.to_datetime(s, errors="raise").date()
    except Exception:
        return None

def _diff_rows(original: pd.DataFrame, edited: pd.DataFrame, editable_cols):
    """Retorna (updates, inserts) como listas de dicts somente com colunas editáveis/necessárias."""
    # normalizar NaN -> None na comparação
    def _norm_df(d: pd.DataFrame):
        d2 = d.copy()
        for c in d2.columns:
            d2[c] = d2[c].where(~d2[c].isna(), None)
        return d2
    original = _norm_df(original)
    edited   = _norm_df(edited)

    updates, inserts = [], []
    cols = ["id"] + [c for c in edited.columns if c in editable_cols or c=="data"]
    o = original[cols].copy() if set(cols).issubset(original.columns) else original
    e = edited[cols].copy()

    # trata data -> date real
    if "data" in e.columns:
        e["data"] = e["data"].apply(_parse_date_str)

    # separa linhas com id e sem id
    e_has_id = e[e["id"].notna()] if "id" in e.columns else pd.DataFrame(columns=e.columns)
    e_new    = e[e["id"].isna()]  if "id" in e.columns else e.iloc[0:0]

    # updates: linhas cujo id existe e mudou em alguma coluna editável
    if not e_has_id.empty:
        merged = e_has_id.merge(o, on="id", how="left", suffixes=("", "_orig"))
        for _, row in merged.iterrows():
            changed = {}
            for c in cols:
                if c == "id": 
                    continue
                cur = row[c]
                orig = row.get(f"{c}_orig", None)
                if (pd.isna(cur) and pd.isna(orig)) or (cur == orig):
                    continue
                changed[c] = None if (isinstance(cur, float) and pd.isna(cur)) else cur
            if changed:
                rec = {"id": int(row["id"]), **changed}
                updates.append(rec)

    # inserts: linhas sem id e que tenham algum valor significativo em colunas editáveis
    for _, row in e_new.iterrows():
        rec = {}
        for c in (editable_cols + (["data"] if "data" in e.columns else [])):
            v = row.get(c, None)
            if c == "data":
                v = _parse_date_str(v)
            if isinstance(v, float) and pd.isna(v):
                v = None
            rec[c] = v
        # evita inserir linha 100% vazia
        any_val = any(v not in (None, "",) for k,v in rec.items() if k != "data") or (rec.get("data") is not None)
        if any_val:
            inserts.append(rec)

    return updates, inserts

def _save_updates_dynamic(table_name: str, updates, inserts):
    """Persiste updates/inserts usando reflexão de tabela (suporta colunas dinâmicas)."""
    md = MetaData()
    tbl = Table(table_name, md, autoload_with=engine)
    colnames = set([c.name for c in tbl.columns])
    count_u = count_i = 0
    with engine.begin() as conn:
        for u in (updates or []):
            if "id" not in u or u["id"] is None:
                continue
            pk = int(u["id"])
            vals = {k: v for k, v in u.items() if k in colnames and k != "id"}
            if not vals:
                continue
            conn.execute(tbl.update().where(tbl.c.id == pk).values(**vals))
            count_u += 1
        for ins in (inserts or []):
            vals = {k: v for k, v in ins.items() if k in colnames}
            if not vals:
                continue
            conn.execute(tbl.insert().values(**vals))
            count_i += 1
    return count_u, count_i

# -------------------- ANDAMENTOS --------------------
with tab1:
    st.markdown("### ✏️ ANDAMENTOS (AGGrid)")
    editable_cols = ["col_b","col_c","status_assunto","cliente",
                     "numero_processo","col_g","col_h","col_i","observacoes"]
    edited_df = _grid(df1, editable_cols, key="grid_andamentos")

    c1, c2 = st.columns([1,1])
    with c1:
        if st.button("➕ Adicionar linha em branco (ANDAMENTOS)", use_container_width=True):
            blank = {c: "" for c in edited_df.columns}
            edited_df = pd.concat([pd.DataFrame([blank]), edited_df], ignore_index=True)
            st.session_state["andamentos_tmp"] = edited_df  # manter enquanto não salva
    edited_df = st.session_state.get("andamentos_tmp", edited_df)

    if st.button("💾 Salvar ANDAMENTOS", use_container_width=True):
        updates, inserts = _diff_rows(df1, edited_df, ["data"] + editable_cols)
        u,i = _save_updates_dynamic('andamentos', updates, inserts)
        st.success(f"ANDAMENTOS: {u} atualizações, {i} inserções.")
        st.session_state.pop("andamentos_tmp", None)
        st.session_state["dfs"] = _load_tables()
        df1, df2, df3 = st.session_state["dfs"]

# -------------------- PUBLICAÇÕES --------------------
with tab2:
    st.markdown("### ✏️ PUBLICAÇÕES (AGGrid)")
    editable_cols = ["col_b","col_c","col_d","cliente",
                     "numero_processo","col_g","col_h","col_i","observacoes"]
    edited_df = _grid(df2, editable_cols, key="grid_publicacoes")

    c1, c2 = st.columns([1,1])
    with c1:
        if st.button("➕ Adicionar linha em branco (PUBLICAÇÕES)", use_container_width=True):
            blank = {c: "" for c in edited_df.columns}
            edited_df = pd.concat([pd.DataFrame([blank]), edited_df], ignore_index=True)
            st.session_state["publicacoes_tmp"] = edited_df
    edited_df = st.session_state.get("publicacoes_tmp", edited_df)

    if st.button("💾 Salvar PUBLICAÇÕES", use_container_width=True):
        updates, inserts = _diff_rows(df2, edited_df, ["data"] + editable_cols)
        u,i = _save_updates_dynamic('publicacoes', updates, inserts)
        st.success(f"PUBLICAÇÕES: {u} atualizações, {i} inserções.")
        st.session_state.pop("publicacoes_tmp", None)
        st.session_state["dfs"] = _load_tables()
        df1, df2, df3 = st.session_state["dfs"]

# -------------------- AGENDA --------------------
with tab3:
    st.markdown("### ✏️ AGENDA (AGGrid)")
    editable_cols = ["idx","horario","status","cliente","cliente_avisado",
                     "anotado_na_agenda","observacao","numero_processo",
                     "tipo_audiencia_pericia","materia","parte_adversa","sistema"]
    edited_df = _grid(df3, editable_cols, key="grid_agenda")

    c1, c2 = st.columns([1,1])
    with c1:
        if st.button("➕ Adicionar linha em branco (AGENDA)", use_container_width=True):
            blank = {c: "" for c in edited_df.columns}
            edited_df = pd.concat([pd.DataFrame([blank]), edited_df], ignore_index=True)
            st.session_state["agenda_tmp"] = edited_df
    edited_df = st.session_state.get("agenda_tmp", edited_df)

    if st.button("💾 Salvar AGENDA", use_container_width=True):
        updates, inserts = _diff_rows(df3, edited_df, ["data"] + editable_cols)
        # idx pode vir string -> tenta converter para int nas inserções/updates
        for L in (updates, inserts):
            for rec in L:
                if "idx" in rec and isinstance(rec["idx"], str) and rec["idx"].strip():
                    try:
                        rec["idx"] = int(rec["idx"])
                    except Exception:
                        pass
        u,i = _save_updates_dynamic('agenda', updates, inserts)
        st.success(f"AGENDA: {u} atualizações, {i} inserções.")
        st.session_state.pop("agenda_tmp", None)
        st.session_state["dfs"] = _load_tables()
        df1, df2, df3 = st.session_state["dfs"]

st.info("Se aparecer aviso de pacote ausente, rode:  pip install streamlit-aggrid")

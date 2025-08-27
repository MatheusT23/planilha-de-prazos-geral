# app_streamlit_db.py
# App Streamlit conectado ao Postgres (le/edita/salva). Rode:
#   pip install -r requirements.txt
#   python -m streamlit run app_streamlit_db.py

import streamlit as st
import pandas as pd
from db import engine, SessionLocal, Andamento, Publicacao, Agenda

st.set_page_config(page_title="Leitor de E-mails — DB", layout="wide")
st.title("📨 Leitor de E-mails — Banco de Dados (Editável)")

TAB1, TAB2, TAB3 = st.tabs(["ANDAMENTOS", "PUBLICAÇÕES", "AGENDA"])

with TAB1:
    df1 = pd.read_sql("select * from andamentos order by created_at desc", engine)
    st.write("✏️ Edite e clique em salvar:")
    edited1 = st.data_editor(df1, num_rows="dynamic", use_container_width=True, height=420)
    if st.button("💾 Salvar ANDAMENTOS"):
        with SessionLocal() as db:
            for _, row in edited1.iterrows():
                values = row.to_dict()
                rec_id = values.pop("id", None)
                if pd.notna(rec_id):
                    db.query(Andamento).filter(Andamento.id==int(rec_id)).update(values)
                else:
                    db.add(Andamento(**values))
            db.commit()
        st.success("ANDAMENTOS salvos!")

with TAB2:
    df2 = pd.read_sql("select * from publicacoes order by created_at desc", engine)
    st.write("✏️ Edite e clique em salvar:")
    edited2 = st.data_editor(df2, num_rows="dynamic", use_container_width=True, height=420)
    if st.button("💾 Salvar PUBLICAÇÕES"):
        with SessionLocal() as db:
            for _, row in edited2.iterrows():
                values = row.to_dict()
                rec_id = values.pop("id", None)
                if pd.notna(rec_id):
                    db.query(Publicacao).filter(Publicacao.id==int(rec_id)).update(values)
                else:
                    db.add(Publicacao(**values))
            db.commit()
        st.success("PUBLICAÇÕES salvas!")

with TAB3:
    df3 = pd.read_sql("select * from agenda order by created_at desc", engine)
    st.write("✏️ Edite e clique em salvar:")
    edited3 = st.data_editor(df3, num_rows="dynamic", use_container_width=True, height=420)
    if st.button("💾 Salvar AGENDA"):
        with SessionLocal() as db:
            for _, row in edited3.iterrows():
                values = row.to_dict()
                rec_id = values.pop("id", None)
                if pd.notna(rec_id):
                    db.query(Agenda).filter(Agenda.id==int(rec_id)).update(values)
                else:
                    db.add(Agenda(**values))
            db.commit()
        st.success("AGENDA salva!")

st.caption("Defina a variável de ambiente DATABASE_URL (Postgres).")

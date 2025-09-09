
# db.py
# Lê DATABASE_URL de env, st.secrets, ou .env e define modelos.
import os
from sqlalchemy import create_engine, Column, BigInteger, Integer, Text, Date, TIMESTAMP, text
from sqlalchemy.orm import declarative_base, sessionmaker

def _get_database_url():
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    try:
        import streamlit as st  # type: ignore
        url = st.secrets.get("DATABASE_URL")
        if url:
            return url
    except Exception:
        pass
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv()
        url = os.environ.get("DATABASE_URL")
        if url:
            return url
    except Exception:
        pass
    raise RuntimeError("DATABASE_URL não encontrado (env, st.secrets ou .env).")

DATABASE_URL = _get_database_url()

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

class Andamento(Base):
    __tablename__ = "andamentos"
    id = Column(BigInteger, primary_key=True)
    inicio_prazo = Column(Date)
    fim_prazo = Column(Date)
    dias_restantes = Column(Integer)
    setor = Column(Text)
    cliente = Column(Text)
    processo = Column(Text, index=True)
    para_ramon_e_adriana_despacharem = Column(Text)
    status = Column(Text)
    resposta_do_colaborador = Column(Text)
    observacoes = Column(Text)

class Publicacao(Base):
    __tablename__ = "publicacoes"
    id = Column(BigInteger, primary_key=True)
    inicio_prazo = Column(Date)
    fim_prazo = Column(Date)
    dias_restantes = Column(Integer)
    setor = Column(Text)
    cliente = Column(Text)
    processo = Column(Text, index=True)
    para_ramon_e_adriana_despacharem = Column(Text)
    status = Column(Text)
    resposta_do_colaborador = Column(Text)
    observacoes = Column(Text)


class Financeiro(Base):
    __tablename__ = "financeiro"
    id = Column(BigInteger, primary_key=True)
    inicio_prazo = Column(Date)
    fim_prazo = Column(Date)
    dias_restantes = Column(Integer)
    setor = Column(Text)
    cliente = Column(Text)
    processo = Column(Text, index=True)
    para_ramon_e_adriana_despacharem = Column(Text)
    status = Column(Text)
    resposta_do_colaborador = Column(Text)
    observacoes = Column(Text)

class Agenda(Base):
    __tablename__ = "agenda"
    id = Column(BigInteger, primary_key=True)
    idx = Column(Integer)
    data = Column(Date)
    horario = Column(Text)
    status = Column(Text)
    cliente = Column(Text)
    cliente_avisado = Column(Text)
    anotado_na_agenda = Column(Text)
    observacao = Column(Text)
    numero_processo = Column(Text, index=True)
    tipo_audiencia_pericia = Column(Text)
    materia = Column(Text)
    parte_adversa = Column(Text)
    sistema = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=text("now()"))


class Concluida(Base):
    __tablename__ = "concluidas"
    id = Column(BigInteger, primary_key=True)
    d = Column(Date)
    inicio_prazo = Column(Date)
    fim_prazo = Column(Date)
    dias_restantes = Column(Integer)
    setor = Column(Text)
    cliente = Column(Text)
    processo = Column(Text, index=True)
    para_ramon_e_adriana_despacharem = Column(Text)
    status = Column(Text)
    resposta_do_colaborador = Column(Text)
    observacoes = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=text("now()"))


class LastChecked(Base):
    __tablename__ = "last_checked"
    id = Column(Integer, primary_key=True)
    checked_at = Column(TIMESTAMP(timezone=True))

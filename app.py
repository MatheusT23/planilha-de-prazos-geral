# app_streamlit_forms.py
# Edição estável por formulários (sem grid): ideal quando o st.data_editor dá flicker.
# Rodar:
#   pip install streamlit pandas sqlalchemy psycopg2-binary python-dotenv beautifulsoup4
#   python -m streamlit run app_streamlit_forms.py

import sys
import importlib.util
from pathlib import Path
from typing import Optional, Dict, Any

import streamlit as st
import pandas as pd
from sqlalchemy import text, inspect
from sqlalchemy.orm import Session
from sqlalchemy.exc import ProgrammingError
from datetime import timezone, timedelta, datetime, date
import unicodedata

from db import (
    engine,
    SessionLocal,
    Andamento,
    Publicacao,
    Financeiro,
    Agenda,
    Concluida,
    LastChecked,
)
from calendar_integration import (
    delete_agenda_event,
    delete_agenda_events,
    sync_agenda_event,
)

TABLE_MODELS = {
    "andamentos": Andamento,
    "publicacoes": Publicacao,
    "financeiro": Financeiro,
    "agenda": Agenda,
    "concluidas": Concluida,
}

# Opções fixas para o campo "status" em todos os formulários
STATUS_OPTIONS = [
    "",
    "Feito",
    "Correção",
    "Assinatura",
    "Nada a Fazer",
    "Em Andamento",
    "Redistribuir",
    "Para Ana Clara",
    "Para Lívia",
    "Para Tainá",
]

STATUS_COLORS = {
    "em andamento": "#cce5ff",  # azul claro
    "para livia": "#e6ccff",  # roxo claro
    "para ana clara": "#fff3cd",  # amarelo claro
    "para taina": "#d2b48c",  # marrom claro
    "feito": "#d4edda",  # verde claro
    "nada a fazer": "#d4edda",
    "feito e nada a fazer": "#d4edda",
    "correcao": "#ffe5b4",  # laranja claro
    "assinatura": "#ffe5b4",
    "correcao e assinatura": "#ffe5b4",
    "avisado": "#d4edda",  # verde claro
    "ja ocorreu": "#cce5ff",  # azul claro
    "remarcada": "#e6ccff",  # roxo claro
    "cancelada": "#f8d7da",  # vermelho claro
}


def _normalize_status(val: Any) -> str:
    if val is None:
        return ""
    val = unicodedata.normalize("NFKD", str(val))
    val = val.encode("ascii", "ignore").decode("ascii")
    return val.strip().lower()


def _detect_audiencia_pericia(text: Any) -> Optional[str]:
    """Retorna "Audiência" ou "Perícia" se o texto contiver essas palavras."""
    if text is None:
        return None
    val = unicodedata.normalize("NFKD", str(text))
    val = val.encode("ascii", "ignore").decode("ascii").lower()
    if "audiencia" in val:
        return "Audiência"
    if "pericia" in val:
        return "Perícia"
    return None


def _show_calendar_warning(message: Optional[str]) -> None:
    if not message:
        return
    warnings = st.session_state.setdefault("__calendar_warnings", [])
    if message in warnings:
        return
    warnings.append(message)
    st.warning(message)


def style_by_status(df: pd.DataFrame):
    if "status" not in df.columns:
        return df

    def _style(row: pd.Series):
        color = STATUS_COLORS.get(_normalize_status(row.get("status")), "")
        if not color:
            return ["" for _ in row]
        style = f"background-color: {color};"
        if color.lower() in (
            "#cce5ff",
            "#d4edda",
            "#fff3cd",
            "#e6ccff",
            "#f8d7da",
        ):  # linhas com fundo azul, verde, amarelo, roxo ou vermelho claros
            style += " color: black;"
        return [style for _ in row]

    return (
        df.style.apply(_style, axis=1)
        .set_table_styles(
            [{"selector": "tbody tr", "props": [("border-top", "1px solid black")]}]
        )
    )


def _calc_dias_restantes_df(df: pd.DataFrame) -> pd.DataFrame:
    """Atualiza coluna ``dias_restantes`` com base em ``fim_prazo`` e o dia atual."""
    if {"inicio_prazo", "fim_prazo"}.issubset(df.columns):
        hoje = pd.Timestamp.today().normalize()
        mask = df["inicio_prazo"].notna() & df["fim_prazo"].notna()
        if mask.any():
            fim = pd.to_datetime(df.loc[mask, "fim_prazo"]).dt.normalize()
            df.loc[mask, "dias_restantes"] = (fim - hoje).dt.days
    return df

@st.cache_data(show_spinner=False)
def _load_tables():
    def _coerce_dias(df: pd.DataFrame) -> pd.DataFrame:
        if "dias_restantes" in df.columns:
            df["dias_restantes"] = df["dias_restantes"].astype("Int64")
        return df

    df1 = pd.read_sql("select * from andamentos order by id desc", engine)
    df1 = _calc_dias_restantes_df(df1)
    df1 = _coerce_dias(df1)

    df2 = pd.read_sql("select * from publicacoes order by id desc", engine)
    df2 = _calc_dias_restantes_df(df2)
    df2 = _coerce_dias(df2)

    df_fin = pd.read_sql("select * from financeiro order by id desc", engine)
    df_fin = _calc_dias_restantes_df(df_fin)
    df_fin = _coerce_dias(df_fin)

    df3 = pd.read_sql("select * from agenda order by created_at desc", engine)

    try:
        df4 = pd.read_sql("select * from concluidas order by created_at desc", engine)
        df4 = _calc_dias_restantes_df(df4)
        df4 = _coerce_dias(df4)
    except ProgrammingError:
        df4 = pd.read_sql("select * from concluidas order by id desc", engine)
        df4 = _calc_dias_restantes_df(df4)
        df4 = _coerce_dias(df4)
    return df1, df2, df_fin, df3, df4

st.set_page_config(page_title="E-mails → Banco (Form Editor estável)", layout="wide")
# Reduz espaço do cabeçalho para mostrar mais conteúdo principal
st.markdown(
    """
    <style>
        div.block-container {
            padding-top: 1rem;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

if "show_settings" not in st.session_state:
    st.session_state["show_settings"] = False
if "filter_date" not in st.session_state:
    st.session_state["filter_date"] = date(2025, 8, 1)

menu_col, title_col = st.columns([0.1, 0.9])
with menu_col:
    st.markdown("<div style='margin-top: 1.2rem'></div>", unsafe_allow_html=True)
    if st.button("☰ Menu", use_container_width=True):
        st.session_state["show_settings"] = True
with title_col:
    st.title("Planilha de Prazos Geral 📗")
if "__last_msg" in st.session_state:
    st.success(st.session_state.pop("__last_msg"))

if st.session_state.get("show_settings"):
    with st.sidebar:
        st.header("Configurações")
        filtro_data = st.date_input(
            "Mostrar dados a partir de",
            value=st.session_state.get("filter_date"),
            min_value=date(2025, 1, 1),
            max_value=date(2050, 12, 31),
            format="DD/MM/YYYY",
            key="filter_date_input",
        )
        st.session_state["filter_date"] = filtro_data
        if st.button("Fechar", key="close_sidebar"):
            st.session_state["show_settings"] = False

# ----- Sidebar: ingestão opcional -----
st.divider()

# Botões principais: Atualizar Tabela e Buscar Novos Emails


def _last_email_update() -> Optional[str]:
    """Retorna a data/hora da última atualização de e-mails em fuso -03:00."""
    with SessionLocal() as db:
        rec = db.query(LastChecked).first()
        if rec and rec.checked_at:
            try:
                dt = rec.checked_at.astimezone(timezone(timedelta(hours=-3)))
            except Exception:
                dt = rec.checked_at
            now = datetime.now(timezone(timedelta(hours=-3)))
            if dt > now:
                dt = now
            return dt.strftime("%d/%m/%Y %H:%M")
    return None


last_email_checked = _last_email_update() or "Nunca"

refresh = st.button("🔄 Atualizar Tabela")
col_btn, col_info = st.columns([1, 4])
with col_btn:
    buscar = st.button("🛰️ Buscar Novos Emails")
with col_info:
    last_email_display = st.empty()
last_email_display.text(f"Última Atualização de Emails: {last_email_checked}")

# Executa o scrap_email.py fixo quando clicar em Buscar Novos Emails
if 'buscar' in locals() and buscar:
    try:
        # caminho fixo do script
        script_path = 'scrap_email.py'
        def load_module(path: str):
            mod_name = Path(path).stem
            if mod_name in sys.modules:
                del sys.modules[mod_name]
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if spec is None or spec.loader is None:
                raise RuntimeError('spec inválida para o caminho informado.')
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)  # type: ignore[attr-defined]
            return mod
        mod = load_module(script_path)
        if hasattr(mod, 'set_streamlit_mode'):
            try:
                mod.set_streamlit_mode(True)  # type: ignore
            except Exception:
                pass
        if hasattr(mod, 'buscar_e_processar_emails'):
            with st.spinner('Executando ingestão de e-mails…'):
                mod.buscar_e_processar_emails()  # type: ignore
            st.success('Ingestão concluída!')
            _load_tables.clear()
            if 'dfs_forms' in st.session_state:
                st.session_state.pop('dfs_forms')
            last_email_checked = _last_email_update() or "Nunca"
            last_email_display.text(f"Última Atualização de Emails: {last_email_checked}")
            refresh = True
        else:
            st.error("Função 'buscar_e_processar_emails' não encontrada no script.")
    except Exception as e:
        st.exception(e)

if refresh or "dfs_forms" not in st.session_state:
    _load_tables.clear()
    st.session_state["dfs_forms"] = _load_tables()

df1, df2, df_fin, df3, df4 = st.session_state["dfs_forms"]

filtro_data = st.session_state.get("filter_date")
if filtro_data:
    def _filtrar_por_inicio(df: pd.DataFrame) -> pd.DataFrame:
        if "inicio_prazo" in df.columns:
            serie = pd.to_datetime(df["inicio_prazo"], errors="coerce")
            return df[serie >= pd.to_datetime(filtro_data)]
        return df

    df1 = _filtrar_por_inicio(df1)
    df2 = _filtrar_por_inicio(df2)
    df4 = _filtrar_por_inicio(df4)

df_pub = df2.copy()

tab1, tab2, tab_fin, tab3, tab4 = st.tabs([
    "ANDAMENTOS",
    "PUBLICAÇÕES",
    "FINANCEIRO E SOLICITAÇÃO DE DOCUMENTOS",
    "ANOTAR NA AGENDA E AVISAR",
    "CONCLUÍDAS",
])

# ---------- Helpers ----------
def _to_date(v):
    if v is None or (isinstance(v, float) and pd.isna(v)) or (isinstance(v, str) and v.strip()==""):
        return None
    try:
        return pd.to_datetime(v, dayfirst=True, errors="coerce").date()
    except Exception:
        return None

def _text(v):
    if v is None:
        return ""
    if isinstance(v, float) and pd.isna(v):
        return ""
    return str(v)

def _row_by_id(df: pd.DataFrame, rid: Optional[int]) -> Optional[pd.Series]:
    if rid is None or "id" not in df.columns:
        return None
    hit = df[df["id"] == rid]
    if hit.empty:
        return None
    return hit.iloc[0]

def _apply_prazo_logic(inicio_prazo, fim_prazo, dias_restantes):
    """Aplica regras de cálculo entre início, fim e dias restantes."""
    hoje = datetime.now().date()
    if inicio_prazo and fim_prazo:
        dias_restantes = (fim_prazo - hoje).days
    elif inicio_prazo and dias_restantes is not None:
        fim_prazo = inicio_prazo + timedelta(days=int(dias_restantes))
    return inicio_prazo, fim_prazo, dias_restantes

def _save_row(model, rec_id: Optional[int], values: Dict[str, Any]):
    if rec_id is None and "inicio_prazo" in values and values["inicio_prazo"] is None:
        values["inicio_prazo"] = date.today()
        values["inicio_prazo"], values["fim_prazo"], values["dias_restantes"] = _apply_prazo_logic(
            values["inicio_prazo"], values.get("fim_prazo"), values.get("dias_restantes")
        )
    calendar_payload: Optional[Dict[str, Any]] = None
    new_id: Optional[int] = rec_id
    with SessionLocal() as db:
        if rec_id is not None:
            db.query(model).filter(model.id == rec_id).update(values)
            db.commit()
            new_id = rec_id
            if model is Agenda:
                record = db.query(Agenda).filter(Agenda.id == rec_id).first()
                if record is not None:
                    calendar_payload = {
                        k: v for k, v in record.__dict__.items() if not k.startswith("_")
                    }
        else:
            record = model(**values)
            db.add(record)
            db.commit()
            try:
                db.refresh(record)
            except Exception:
                pass
            new_id = getattr(record, "id", None)
            if model is Agenda:
                calendar_payload = {
                    k: v for k, v in record.__dict__.items() if not k.startswith("_")
                }
    if model is Agenda and new_id is not None and calendar_payload:
        success, message = sync_agenda_event(int(new_id), calendar_payload)
        if not success:
            _show_calendar_warning(message)
    return new_id

def _delete_row(model, rec_id: Optional[int]):
    if rec_id is None:
        return
    with SessionLocal() as db:
        db.query(model).filter(model.id == rec_id).delete()
        db.commit()
    if model is Agenda:
        success, message = delete_agenda_event(int(rec_id))
        if not success:
            _show_calendar_warning(message)


def _move_to_concluidas(model, rec_id: Optional[int]):
    if rec_id is None:
        return
    with SessionLocal() as db:
        rec = db.query(model).filter(model.id == rec_id).first()
        if rec is None:
            return
        values = {
            "d": getattr(rec, "inicio_prazo", None) or getattr(rec, "data", None),
            "inicio_prazo": getattr(rec, "inicio_prazo", None),
            "fim_prazo": getattr(rec, "fim_prazo", None),
            "dias_restantes": getattr(rec, "dias_restantes", None),
            "setor": getattr(rec, "setor", None) or getattr(rec, "horario", None),
            "cliente": getattr(rec, "cliente", None),
            "processo": getattr(rec, "processo", None) or getattr(rec, "numero_processo", None),
            "para_ramon_e_adriana_despacharem": getattr(rec, "para_ramon_e_adriana_despacharem", None) or getattr(rec, "anotado_na_agenda", None),
            "status": getattr(rec, "status", None),
            "resposta_do_colaborador": getattr(rec, "resposta_do_colaborador", None) or getattr(rec, "cliente_avisado", None),
            "observacoes": getattr(rec, "observacoes", None) or getattr(rec, "observacao", None),
        }
        # detect available columns in 'concluidas' to support legacy schemas
        cols = {c["name"] for c in inspect(db.bind).get_columns("concluidas")}
        filtered = {k: v for k, v in values.items() if k in cols}
        columns = ", ".join(filtered.keys())
        placeholders = ", ".join(f":{k}" for k in filtered.keys())
        db.execute(text(f"INSERT INTO concluidas ({columns}) VALUES ({placeholders})"), filtered)
        db.query(model).filter(model.id == rec_id).delete()
        db.commit()
    if model is Agenda:
        success, message = delete_agenda_event(int(rec_id))
        if not success:
            _show_calendar_warning(message)

def _delete_rows(model, ids):
    if not ids:
        return
    with SessionLocal() as db:
        db.query(model).filter(model.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
    if model is Agenda:
        agenda_ids = [int(i) for i in ids]
        success, message = delete_agenda_events(agenda_ids)
        if not success:
            _show_calendar_warning(message)


def _move_rows(src_model, dst_model, ids):
    if not ids:
        return
    agenda_ids_to_remove = []
    with SessionLocal() as db:
        src_cols = [c.name for c in inspect(src_model).columns]
        dst_cols = [c.name for c in inspect(dst_model).columns]
        common_cols = [c for c in src_cols if c in dst_cols and c != "id"]
        rows = db.query(src_model).filter(src_model.id.in_(ids)).all()
        for row in rows:
            data = {col: getattr(row, col) for col in common_cols}
            _save_row(dst_model, None, data)
            if src_model is Agenda:
                try:
                    agenda_ids_to_remove.append(int(getattr(row, "id")))
                except (TypeError, ValueError):
                    pass
            db.delete(row)
        db.commit()
    if agenda_ids_to_remove:
        success, message = delete_agenda_events(agenda_ids_to_remove)
        if not success:
            _show_calendar_warning(message)


def _bulk_actions(df: pd.DataFrame, table_name: str):
    ids = df["id"].dropna().astype(int).tolist() if "id" in df.columns else []
    if "cliente" in df.columns:
        id_to_cliente = (
            df.dropna(subset=["id"])
            .assign(id=lambda x: x["id"].astype(int))
            .set_index("id")["cliente"]
            .astype(str)
            .to_dict()
        )
        selected = st.multiselect(
            "Selecionar IDs:",
            ids,
            key=f"bulk_select_{table_name}",
            format_func=lambda x: f"{x} - {id_to_cliente.get(x, '')}",
        )
    else:
        selected = st.multiselect(
            "Selecionar IDs:", ids, key=f"bulk_select_{table_name}"
        )
    if not selected:
        return
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🗑️ Excluir", key=f"bulk_delete_{table_name}"):
            _delete_rows(TABLE_MODELS[table_name], selected)
            _reload("Excluídos com sucesso.")
    with col2:
        options = [t for t in TABLE_MODELS.keys() if t != table_name]
        target = st.selectbox(
            "Mover para a tabela", options, key=f"bulk_move_sel_{table_name}"
        )
        if st.button("Mover", key=f"bulk_move_{table_name}"):
            _move_rows(TABLE_MODELS[table_name], TABLE_MODELS[target], selected)
            _reload(f"Movidos para {target}.")

def _reload(msg: Optional[str] = None):
    try:
        _load_tables.clear()
    except Exception:
        pass
    st.session_state["dfs_forms"] = _load_tables()
    if msg:
        st.session_state["__last_msg"] = msg
    try:
        st.rerun()
    except AttributeError:
        st.experimental_rerun()


# ---------- ANDAMENTOS ----------
with tab1:
    left, right = st.columns([1.7, 0.7])
    with left:
        st.markdown("#### 📄 Visualização")
        st.dataframe(style_by_status(df1), use_container_width=True, height=1000, hide_index=True)
        _bulk_actions(df1, "andamentos")
    with right:
        st.markdown("#### ✏️ Editar / Adicionar")
        ids = df1["id"].tolist() if "id" in df1.columns else []
        escolha = st.selectbox("Selecione um ID para editar (ou deixe vazio para novo):",
                               options=["<novo>"] + ids, index=0)

        target = None if escolha == "<novo>" else int(escolha)
        row = _row_by_id(df1, target)

        with st.form("form_andamentos", clear_on_submit=False):
            inicio_prazo = st.date_input("inicio_prazo", value=_to_date(row["inicio_prazo"]) if row is not None else None)
            fim_prazo = st.date_input("fim_prazo", value=_to_date(row["fim_prazo"]) if row is not None else None)
            dias_restantes = st.number_input(
                "dias_restantes",
                value=int(row["dias_restantes"]) if row is not None and not pd.isna(row["dias_restantes"]) else 0,
                step=1,
            )
            setor = st.text_input("setor", value=_text(row["setor"]) if row is not None else "")
            cliente = st.text_input("cliente", value=_text(row["cliente"]) if row is not None else "")
            processo = st.text_input("processo", value=_text(row["processo"]) if row is not None else "")
            para_ramon_e_adriana_despacharem = st.text_input(
                "para_ramon_e_adriana_despacharem",
                value=_text(row["para_ramon_e_adriana_despacharem"]) if row is not None else "",
            )
            status_options = STATUS_OPTIONS
            status_index = (
                status_options.index(row["status"])
                if row is not None and row["status"] in status_options
                else 0
            )
            status = st.selectbox("status", status_options, index=status_index)
            resposta_do_colaborador = st.text_input(
                "resposta_do_colaborador",
                value=_text(row["resposta_do_colaborador"]) if row is not None else "",
            )
            observacoes = st.text_area(
                "observacoes",
                value=_text(row["observacoes"]) if row is not None else "",
                height=120,
            )
            col1, col2 = st.columns(2)
            with col1:
                submitted = st.form_submit_button("💾 Salvar ANDAMENTO", use_container_width=True)
            with col2:
                deleted = st.form_submit_button(
                    "🗑️ Excluir", use_container_width=True, disabled=target is None
                )
            col3, _ = st.columns(2)
            with col3:
                concluded = st.form_submit_button(
                    "Marcar como Concluída",
                    use_container_width=True,
                    disabled=target is None,
                    key="concl_andamento",
                )
                st.markdown(
                    """
                    <style>
                    button[data-testid="baseButton-concl_andamento"] {
                        background-color: #90EE90;
                    }
                    </style>
                    """,
                    unsafe_allow_html=True,
                )
            if submitted:
                inicio_prazo, fim_prazo, dias_restantes = _apply_prazo_logic(
                    inicio_prazo, fim_prazo, dias_restantes
                )
                tipo_ap = _detect_audiencia_pericia(resposta_do_colaborador)
                if tipo_ap:
                    agenda_values = {
                        "idx": None,
                        "data": fim_prazo or inicio_prazo,
                        "horario": None,
                        "status": None,
                        "cliente": cliente or None,
                        "cliente_avisado": None,
                        "anotado_na_agenda": None,
                        "observacao": resposta_do_colaborador or observacoes or None,
                        "numero_processo": processo or None,
                        "tipo_audiencia_pericia": tipo_ap,
                        "materia": None,
                        "parte_adversa": None,
                        "sistema": "manual",
                    }
                    _save_row(Agenda, None, agenda_values)
                    if target is not None:
                        _delete_row(Andamento, target)
                    _reload("Movido para Agenda.")
                else:
                    values = {
                        "inicio_prazo": inicio_prazo or None,
                        "fim_prazo": fim_prazo or None,
                        "dias_restantes": int(dias_restantes) if dias_restantes is not None else None,
                        "setor": setor or None,
                        "cliente": cliente or None,
                        "processo": processo or None,
                        "para_ramon_e_adriana_despacharem": para_ramon_e_adriana_despacharem or None,
                        "status": status or None,
                        "resposta_do_colaborador": resposta_do_colaborador or None,
                        "observacoes": observacoes or None,
                    }
                    _save_row(Andamento, target, values)
                    _reload("Salvo com sucesso.")
            if concluded:
                inicio_prazo, fim_prazo, dias_restantes = _apply_prazo_logic(
                    inicio_prazo, fim_prazo, dias_restantes
                )
                values = {
                    "inicio_prazo": inicio_prazo or None,
                    "fim_prazo": fim_prazo or None,
                    "dias_restantes": int(dias_restantes) if dias_restantes is not None else None,
                    "setor": setor or None,
                    "cliente": cliente or None,
                    "processo": processo or None,
                    "para_ramon_e_adriana_despacharem": para_ramon_e_adriana_despacharem or None,
                    "status": status or None,
                    "resposta_do_colaborador": resposta_do_colaborador or None,
                    "observacoes": observacoes or None,
                }
                _save_row(Andamento, target, values)
                _move_to_concluidas(Andamento, target)
                _reload("Movido para Concluídas.")
            if deleted and target is not None:
                _delete_row(Andamento, target)
                _reload("Excluído com sucesso.")

# ---------- PUBLICAÇÕES ----------
with tab2:
    left, right = st.columns([1.7, 0.7])
    with left:
        st.markdown("#### 📄 Visualização")
        st.dataframe(style_by_status(df_pub), use_container_width=True, height=1000, hide_index=True)
        _bulk_actions(df_pub, "publicacoes")
    with right:
        st.markdown("#### ✏️ Editar / Adicionar")
        ids = df_pub["id"].tolist() if "id" in df_pub.columns else []
        escolha = st.selectbox("Selecione um ID para editar (ou deixe vazio para novo):",
                               options=["<novo>"] + ids, index=0, key="pub_select")

        target = None if escolha == "<novo>" else int(escolha)
        row = _row_by_id(df_pub, target)

        with st.form("form_publicacoes", clear_on_submit=False):
            inicio_prazo = st.date_input("inicio_prazo", value=_to_date(row["inicio_prazo"]) if row is not None else None)
            fim_prazo = st.date_input("fim_prazo", value=_to_date(row["fim_prazo"]) if row is not None else None)
            dias_restantes = st.number_input(
                "dias_restantes",
                value=int(row["dias_restantes"]) if row is not None and not pd.isna(row["dias_restantes"]) else 0,
                step=1,
            )
            setor = st.text_input("setor", value=_text(row["setor"]) if row is not None else "")
            cliente = st.text_input("cliente", value=_text(row["cliente"]) if row is not None else "")
            processo = st.text_input("processo", value=_text(row["processo"]) if row is not None else "")
            para_ramon_e_adriana_despacharem = st.text_input(
                "para_ramon_e_adriana_despacharem",
                value=_text(row["para_ramon_e_adriana_despacharem"]) if row is not None else "",
            )
            status_options = STATUS_OPTIONS
            status_index = (
                status_options.index(row["status"])
                if row is not None and row["status"] in status_options
                else 0
            )
            status = st.selectbox("status", status_options, index=status_index)
            resposta_do_colaborador = st.text_input(
                "resposta_do_colaborador",
                value=_text(row["resposta_do_colaborador"]) if row is not None else "",
            )
            observacoes = st.text_area(
                "observacoes",
                value=_text(row["observacoes"]) if row is not None else "",
                height=120,
            )
            col1, col2 = st.columns(2)
            with col1:
                submitted = st.form_submit_button(
                    "💾 Salvar PUBLICAÇÃO", use_container_width=True
                )
            with col2:
                deleted = st.form_submit_button(
                    "🗑️ Excluir", use_container_width=True, disabled=target is None
                )
            col3, _ = st.columns(2)
            with col3:
                concluded = st.form_submit_button(
                    "Marcar como Concluída",
                    use_container_width=True,
                    disabled=target is None,
                    key="concl_publicacao",
                )
                st.markdown(
                    """
                    <style>
                    button[data-testid=\"baseButton-concl_publicacao\"] {
                        background-color: #90EE90;
                    }
                    </style>
                    """,
                    unsafe_allow_html=True,
                )
            if submitted:
                inicio_prazo, fim_prazo, dias_restantes = _apply_prazo_logic(
                    inicio_prazo, fim_prazo, dias_restantes
                )
                tipo_ap = _detect_audiencia_pericia(resposta_do_colaborador)
                if tipo_ap:
                    agenda_values = {
                        "idx": None,
                        "data": fim_prazo or inicio_prazo,
                        "horario": None,
                        "status": None,
                        "cliente": cliente or None,
                        "cliente_avisado": None,
                        "anotado_na_agenda": None,
                        "observacao": resposta_do_colaborador or observacoes or None,
                        "numero_processo": processo or None,
                        "tipo_audiencia_pericia": tipo_ap,
                        "materia": None,
                        "parte_adversa": None,
                        "sistema": "manual",
                    }
                    _save_row(Agenda, None, agenda_values)
                    if target is not None:
                        _delete_row(Publicacao, target)
                    _reload("Movido para Agenda.")
                else:
                    values = {
                        "inicio_prazo": inicio_prazo or None,
                        "fim_prazo": fim_prazo or None,
                        "dias_restantes": int(dias_restantes) if dias_restantes is not None else None,
                        "setor": setor or None,
                        "cliente": cliente or None,
                        "processo": processo or None,
                        "para_ramon_e_adriana_despacharem": para_ramon_e_adriana_despacharem or None,
                        "status": status or None,
                        "resposta_do_colaborador": resposta_do_colaborador or None,
                        "observacoes": observacoes or None,
                    }
                    if setor.strip().lower() in {"livia", "financeiro", "setor financeiro"}:
                        _save_row(Financeiro, None, values)
                        if target is not None:
                            _delete_row(Publicacao, target)
                        _reload("Movido para Financeiro.")
                    else:
                        _save_row(Publicacao, target, values)
                        _reload("Salvo com sucesso.")
            if concluded:
                inicio_prazo, fim_prazo, dias_restantes = _apply_prazo_logic(
                    inicio_prazo, fim_prazo, dias_restantes
                )
                values = {
                    "inicio_prazo": inicio_prazo or None,
                    "fim_prazo": fim_prazo or None,
                    "dias_restantes": int(dias_restantes) if dias_restantes is not None else None,
                    "setor": setor or None,
                    "cliente": cliente or None,
                    "processo": processo or None,
                    "para_ramon_e_adriana_despacharem": para_ramon_e_adriana_despacharem or None,
                    "status": status or None,
                    "resposta_do_colaborador": resposta_do_colaborador or None,
                    "observacoes": observacoes or None,
                }
                _save_row(Publicacao, target, values)
                _move_to_concluidas(Publicacao, target)
                _reload("Movido para Concluídas.")
            if deleted and target is not None:
                _delete_row(Publicacao, target)
                _reload("Excluído com sucesso.")

# ---------- FINANCEIRO ----------
with tab_fin:
    left, right = st.columns([1.7, 0.7])
    with left:
        st.markdown("#### 📄 Visualização")
        st.dataframe(style_by_status(df_fin), use_container_width=True, height=1000, hide_index=True)
        _bulk_actions(df_fin, "financeiro")
    with right:
        st.markdown("#### ✏️ Editar / Solicitar")
        ids = df_fin["id"].tolist() if "id" in df_fin.columns else []
        escolha = st.selectbox(
            "Selecione um ID para editar (ou deixe vazio para novo):",
            options=["<novo>"] + ids,
            index=0,
            key="fin_select",
        )

        target = None if escolha == "<novo>" else int(escolha)
        row = _row_by_id(df_fin, target)

        with st.form("form_financeiro", clear_on_submit=False):
            inicio_prazo = st.date_input(
                "inicio_prazo", value=_to_date(row["inicio_prazo"]) if row is not None else None
            )
            fim_prazo = st.date_input(
                "fim_prazo", value=_to_date(row["fim_prazo"]) if row is not None else None
            )
            dias_restantes = st.number_input(
                "dias_restantes",
                value=int(row["dias_restantes"]) if row is not None and not pd.isna(row["dias_restantes"]) else 0,
                step=1,
            )
            cliente = st.text_input(
                "cliente", value=_text(row["cliente"]) if row is not None else ""
            )
            processo = st.text_input(
                "processo", value=_text(row["processo"]) if row is not None else ""
            )
            para_ramon_e_adriana_despacharem = st.text_input(
                "para_ramon_e_adriana_despacharem",
                value=_text(row["para_ramon_e_adriana_despacharem"]) if row is not None else "",
            )
            status_options = STATUS_OPTIONS
            status_index = (
                status_options.index(row["status"])
                if row is not None and row["status"] in status_options
                else 0
            )
            status = st.selectbox("status", status_options, index=status_index)
            resposta_do_colaborador = st.text_input(
                "resposta_do_colaborador",
                value=_text(row["resposta_do_colaborador"]) if row is not None else "",
            )
            observacoes = st.text_area(
                "observacoes",
                value=_text(row["observacoes"]) if row is not None else "",
                height=120,
            )
            col1, col2 = st.columns(2)
            with col1:
                submitted = st.form_submit_button("💾 Salvar", use_container_width=True)
            with col2:
                move_pub = st.form_submit_button(
                    "Mover para Publicações",
                    use_container_width=True,
                    disabled=target is None,
                    key="fin_to_pub",
                )
            col3, col4 = st.columns(2)
            with col3:
                move_and = st.form_submit_button(
                    "Mover para Andamentos",
                    use_container_width=True,
                    disabled=target is None,
                    key="fin_to_and",
                )
            with col4:
                deleted = st.form_submit_button(
                    "🗑️ Excluir", use_container_width=True, disabled=target is None
                )
            col5, _ = st.columns(2)
            with col5:
                concluded = st.form_submit_button(
                    "Marcar como Concluída",
                    use_container_width=True,
                    disabled=target is None,
                    key="concl_financeiro",
                )
                st.markdown(
                    """
                    <style>
                    button[data-testid=\"baseButton-concl_financeiro\"] {
                        background-color: #90EE90;
                    }
                    </style>
                    """,
                    unsafe_allow_html=True,
                )
            if submitted:
                inicio_prazo, fim_prazo, dias_restantes = _apply_prazo_logic(
                    inicio_prazo, fim_prazo, dias_restantes
                )
                values = {
                    "inicio_prazo": inicio_prazo or None,
                    "fim_prazo": fim_prazo or None,
                    "dias_restantes": int(dias_restantes) if dias_restantes is not None else None,
                    "setor": "Lívia",
                    "cliente": cliente or None,
                    "processo": processo or None,
                    "para_ramon_e_adriana_despacharem": para_ramon_e_adriana_despacharem or None,
                    "status": status or None,
                    "resposta_do_colaborador": resposta_do_colaborador or None,
                    "observacoes": observacoes or None,
                }
                _save_row(Financeiro, target, values)
                _reload("Salvo com sucesso.")
            if move_pub:
                inicio_prazo, fim_prazo, dias_restantes = _apply_prazo_logic(
                    inicio_prazo, fim_prazo, dias_restantes
                )
                values = {
                    "inicio_prazo": inicio_prazo or None,
                    "fim_prazo": fim_prazo or None,
                    "dias_restantes": int(dias_restantes) if dias_restantes is not None else None,
                    "setor": "Tainá",
                    "cliente": cliente or None,
                    "processo": processo or None,
                    "para_ramon_e_adriana_despacharem": para_ramon_e_adriana_despacharem or None,
                    "status": status or None,
                    "resposta_do_colaborador": resposta_do_colaborador or None,
                    "observacoes": observacoes or None,
                }
                _save_row(Publicacao, None, values)
                if target is not None:
                    _delete_row(Financeiro, target)
                _reload("Movido para Publicações.")
            if move_and:
                inicio_prazo, fim_prazo, dias_restantes = _apply_prazo_logic(
                    inicio_prazo, fim_prazo, dias_restantes
                )
                values = {
                    "inicio_prazo": inicio_prazo or None,
                    "fim_prazo": fim_prazo or None,
                    "dias_restantes": int(dias_restantes) if dias_restantes is not None else None,
                    "setor": "Tainá",
                    "cliente": cliente or None,
                    "processo": processo or None,
                    "para_ramon_e_adriana_despacharem": para_ramon_e_adriana_despacharem or None,
                    "status": status or None,
                    "resposta_do_colaborador": resposta_do_colaborador or None,
                    "observacoes": observacoes or None,
                }
                _save_row(Andamento, None, values)
                if target is not None:
                    _delete_row(Financeiro, target)
                _reload("Movido para Andamentos.")
            if concluded:
                inicio_prazo, fim_prazo, dias_restantes = _apply_prazo_logic(
                    inicio_prazo, fim_prazo, dias_restantes
                )
                values = {
                    "inicio_prazo": inicio_prazo or None,
                    "fim_prazo": fim_prazo or None,
                    "dias_restantes": int(dias_restantes) if dias_restantes is not None else None,
                    "setor": "Lívia",
                    "cliente": cliente or None,
                    "processo": processo or None,
                    "para_ramon_e_adriana_despacharem": para_ramon_e_adriana_despacharem or None,
                    "status": status or None,
                    "resposta_do_colaborador": resposta_do_colaborador or None,
                    "observacoes": observacoes or None,
                }
                _save_row(Financeiro, target, values)
                _move_to_concluidas(Financeiro, target)
                _reload("Movido para Concluídas.")
            if deleted and target is not None:
                _delete_row(Financeiro, target)
                _reload("Excluído com sucesso.")

# ---------- AGENDA ----------
with tab3:
    left, right = st.columns([1.7, 0.7])
    with left:
        st.markdown("#### 📄 Visualização")
        st.dataframe(style_by_status(df3), use_container_width=True, height=1000, hide_index=True)
        _bulk_actions(df3, "agenda")
    with right:
        st.markdown("#### ✏️ Editar / Adicionar")
        ids = df3["id"].tolist() if "id" in df3.columns else []
        escolha = st.selectbox("Selecione um ID para editar (ou deixe vazio para novo):",
                               options=["<novo>"] + ids, index=0, key="agenda_select")

        target = None if escolha == "<novo>" else int(escolha)
        row = _row_by_id(df3, target)

        with st.form("form_agenda", clear_on_submit=False):
            idx = st.text_input("idx", value=_text(row["idx"]) if row is not None else "")
            data = st.date_input("data", value=_to_date(row["data"]) if row is not None else None)
            horario = st.text_input("horario", value=_text(row["horario"]) if row is not None else "")
            status_options = ["AVISADO", "JÁ OCORREU", "REMARCADA", "CANCELADA"]
            status_index = (
                status_options.index(row["status"])
                if row is not None and row["status"] in status_options
                else 0
            )
            status = st.selectbox("status", status_options, index=status_index)
            cliente = st.text_input("cliente", value=_text(row["cliente"]) if row is not None else "")
            cliente_avisado = st.text_input("cliente_avisado", value=_text(row["cliente_avisado"]) if row is not None else "")
            anotado_na_agenda = st.text_input("anotado_na_agenda", value=_text(row["anotado_na_agenda"]) if row is not None else "")
            observacao = st.text_area("observacao", value=_text(row["observacao"]) if row is not None else "", height=90)
            numero_processo = st.text_input("numero_processo", value=_text(row["numero_processo"]) if row is not None else "")
            tipo_audiencia_pericia = st.text_input("tipo_audiencia_pericia", value=_text(row["tipo_audiencia_pericia"]) if row is not None else "")
            materia = st.text_input("materia", value=_text(row["materia"]) if row is not None else "")
            parte_adversa = st.text_input("parte_adversa", value=_text(row["parte_adversa"]) if row is not None else "")
            sistema = st.text_input("sistema", value=_text(row["sistema"]) if row is not None else "")
            col1, col2 = st.columns(2)
            with col1:
                submitted = st.form_submit_button("💾 Salvar AGENDA", use_container_width=True)
            with col2:
                deleted = st.form_submit_button(
                    "🗑️ Excluir", use_container_width=True, disabled=target is None
                )
            col3, _ = st.columns(2)
            with col3:
                concluded = st.form_submit_button(
                    "Marcar como Concluída",
                    use_container_width=True,
                    disabled=target is None,
                    key="concl_agenda",
                )
                st.markdown(
                    """
                    <style>
                    button[data-testid=\"baseButton-concl_agenda\"] {
                        background-color: #90EE90;
                    }
                    </style>
                    """,
                    unsafe_allow_html=True,
                )
            if submitted:
                values = {
                    "idx": int(idx) if idx and idx.strip().isdigit() else None,
                    "data": data,
                    "horario": horario or None,
                    "status": status or None,
                    "cliente": cliente or None,
                    "cliente_avisado": cliente_avisado or None,
                    "anotado_na_agenda": anotado_na_agenda or None,
                    "observacao": observacao or None,
                    "numero_processo": numero_processo or None,
                    "tipo_audiencia_pericia": tipo_audiencia_pericia or None,
                    "materia": materia or None,
                    "parte_adversa": parte_adversa or None,
                    "sistema": sistema or None,
                }
                _save_row(Agenda, target, values)
                _reload("Salvo com sucesso.")
            if concluded:
                values = {
                    "idx": int(idx) if idx and idx.strip().isdigit() else None,
                    "data": data,
                    "horario": horario or None,
                    "status": status or None,
                    "cliente": cliente or None,
                    "cliente_avisado": cliente_avisado or None,
                    "anotado_na_agenda": anotado_na_agenda or None,
                    "observacao": observacao or None,
                    "numero_processo": numero_processo or None,
                    "tipo_audiencia_pericia": tipo_audiencia_pericia or None,
                    "materia": materia or None,
                    "parte_adversa": parte_adversa or None,
                    "sistema": sistema or None,
                }
                _save_row(Agenda, target, values)
                _move_to_concluidas(Agenda, target)
                _reload("Movido para Concluídas.")
            if deleted and target is not None:
                _delete_row(Agenda, target)
                _reload("Excluído com sucesso.")

# ---------- CONCLUÍDAS ----------
with tab4:
    st.markdown("#### 📄 Visualização")
    st.dataframe(style_by_status(df4), use_container_width=True, height=1000, hide_index=True)
    _bulk_actions(df4, "concluidas")

st.caption("Dica: o modo formulário evita o rerun a cada tecla; os dados só mudam ao clicar Salvar.")
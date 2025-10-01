"""Integração com o Google Calendar baseada na Google Calendar Simple API.

Este módulo foi reescrito do zero para utilizar a biblioteca documentada em
https://google-calendar-simple-api.readthedocs.io. A autenticação é feita a
partir de um ``google.oauth2.credentials.Credentials`` pré-configurado com os
valores de token, *refresh token*, *client id* e *client secret*.

Principais responsabilidades:

* Construir credenciais OAuth a partir de variáveis de ambiente ou ``st.secrets``
  (caso o Streamlit esteja disponível).
* Instanciar ``GoogleCalendar`` utilizando essas credenciais.
* Criar, atualizar e remover eventos relacionados aos registros da agenda.
* Gerar resumo e descrição dos eventos com base nos dados recebidos.

As funções ``sync_agenda_event`` e ``delete_agenda_event`` continuam expostas
para uso pelo restante da aplicação.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable, Mapping, Optional, Tuple

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from gcsa.event import Event
from gcsa.google_calendar import GoogleCalendar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:  # Streamlit é opcional no contexto de testes
    import streamlit as st
except Exception:  # pragma: no cover - dependente do ambiente
    st = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]
DEFAULT_TIMEZONE = "America/Sao_Paulo"
DEFAULT_CALENDAR_ID = "primary"
DEFAULT_DURATION_MINUTES = 60

_TIME_PATTERN = re.compile(r"(\d{1,2})(?:[:hH](\d{2}))?")


@dataclass
class CalendarCredentialsConfig:
    """Agrupa os parâmetros mínimos necessários para autenticar no Calendar."""

    token: Optional[str]
    refresh_token: str
    client_id: str
    client_secret: str
    token_uri: str = "https://oauth2.googleapis.com/token"


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _coerce_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return str(value)


def _get_config_value(*keys: str) -> Optional[str]:
    """Recupera o primeiro valor disponível dentre *keys*.

    Procura nas variáveis de ambiente e, se disponível, em ``st.secrets``.
    """

    for key in keys:
        value = os.getenv(key)
        if value:
            return value.strip()
        if st is not None:
            try:
                value = st.secrets[key]  # type: ignore[index]
            except Exception:
                continue
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _ensure_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(text).date()
        except ValueError:
            return None
    return None


def _extract_times(horario: str) -> list[time]:
    matches = _TIME_PATTERN.findall(horario)
    times: list[time] = []
    for hour_str, minute_str in matches:
        hour = int(hour_str)
        minute = int(minute_str) if minute_str else 0
        if 0 <= hour < 24 and 0 <= minute < 60:
            times.append(time(hour=hour, minute=minute))
    return times


def _compute_event_times(event_date: date, horario: Optional[str]) -> Tuple[Any, Any, bool]:
    horarios = _extract_times(horario) if horario else []
    if not horarios:
        start_date = event_date
        end_date = event_date + timedelta(days=1)
        return start_date, end_date, True
    start_time = horarios[0]
    if len(horarios) > 1:
        end_time = horarios[1]
    else:
        tmp_start = datetime.combine(date.today(), start_time)
        tmp_end = tmp_start + timedelta(minutes=DEFAULT_DURATION_MINUTES)
        end_time = tmp_end.time()
    start_dt = datetime.combine(event_date, start_time)
    end_dt = datetime.combine(event_date, end_time)
    if end_dt <= start_dt:
        end_dt = start_dt + timedelta(minutes=DEFAULT_DURATION_MINUTES)
    return start_dt, end_dt, False


class CalendarService:
    """Camada de orquestração para sincronização com o Google Calendar."""

    def __init__(self) -> None:
        self._timezone_name = _get_config_value(
            "GOOGLE_CALENDAR_TIMEZONE", "CALENDAR_TIMEZONE"
        ) or DEFAULT_TIMEZONE
        self._calendar_id = _get_config_value(
            "GOOGLE_CALENDAR_ID", "CALENDAR_ID", "CALENDAR"
        ) or DEFAULT_CALENDAR_ID
        try:
            self._tzinfo = ZoneInfo(self._timezone_name)
        except ZoneInfoNotFoundError:
            logger.warning(
                "Timezone '%s' não encontrada. Eventos serão criados sem informação de timezone.",
                self._timezone_name,
            )
            self._tzinfo = None
        self._calendar: Optional[GoogleCalendar] = None
        self._credentials: Optional[Credentials] = None
        self._last_error: Optional[str] = None

    # ------------------------------------------------------------------
    # Configuração de credenciais
    # ------------------------------------------------------------------
    def _load_credentials_config(self) -> Optional[CalendarCredentialsConfig]:
        refresh_token = _get_config_value(
            "GOOGLE_CALENDAR_REFRESH_TOKEN",
            "GOOGLE_API_REFRESH_TOKEN",
            "CALENDAR_REFRESH_TOKEN",
        )
        client_id = _get_config_value(
            "GOOGLE_CALENDAR_CLIENT_ID",
            "GOOGLE_API_CLIENT_ID",
            "CALENDAR_CLIENT_ID",
        )
        client_secret = _get_config_value(
            "GOOGLE_CALENDAR_CLIENT_SECRET",
            "GOOGLE_API_CLIENT_SECRET",
            "CALENDAR_CLIENT_SECRET",
        )
        token = _get_config_value(
            "GOOGLE_CALENDAR_ACCESS_TOKEN",
            "GOOGLE_API_ACCESS_TOKEN",
            "CALENDAR_ACCESS_TOKEN",
        )
        token_uri = _get_config_value(
            "GOOGLE_CALENDAR_TOKEN_URI",
            "GOOGLE_API_TOKEN_URI",
            "CALENDAR_TOKEN_URI",
        ) or "https://oauth2.googleapis.com/token"

        if not refresh_token or not client_id or not client_secret:
            self._last_error = (
                "Credenciais OAuth do Google Calendar não configuradas. "
                "Defina refresh token, client id e client secret."
            )
            return None

        return CalendarCredentialsConfig(
            token=token,
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
            token_uri=token_uri,
        )

    def _build_credentials(self) -> Optional[Credentials]:
        if self._credentials is not None:
            return self._credentials

        config = self._load_credentials_config()
        if config is None:
            return None

        credentials = Credentials(
            token=config.token,
            refresh_token=config.refresh_token,
            client_id=config.client_id,
            client_secret=config.client_secret,
            scopes=SCOPES,
            token_uri=config.token_uri,
        )

        if credentials and credentials.refresh_token and not credentials.valid:
            try:
                credentials.refresh(Request())
            except Exception as exc:  # pragma: no cover - depende de acesso externo
                logger.warning(
                    "Não foi possível renovar o token do Google Calendar automaticamente.",
                    exc_info=exc,
                )
        self._credentials = credentials
        return credentials

    # ------------------------------------------------------------------
    # Instanciação do Calendar
    # ------------------------------------------------------------------
    def _ensure_calendar(self) -> Optional[GoogleCalendar]:
        if self._calendar is not None:
            return self._calendar

        credentials = self._build_credentials()
        if credentials is None:
            return None

        try:
            calendar = GoogleCalendar(
                default_calendar=self._calendar_id,
                credentials=credentials,
            )
        except Exception as exc:  # pragma: no cover - depende da API externa
            logger.exception(
                "Erro ao inicializar cliente do Google Calendar", exc_info=exc
            )
            self._last_error = (
                "Falha ao conectar ao Google Calendar com as credenciais informadas."
            )
            return None

        self._calendar = calendar
        return calendar

    # ------------------------------------------------------------------
    # Operações de sincronização
    # ------------------------------------------------------------------
    def sync_event(
        self, record_id: int, data: Mapping[str, Any]
    ) -> Tuple[bool, Optional[str]]:
        calendar = self._ensure_calendar()
        if calendar is None:
            return False, self._last_error

        event_date = _ensure_date(data.get("data"))
        if not event_date:
            return False, "Registro da agenda sem data. Evento não sincronizado."

        horario = _coerce_str(data.get("horario"))
        start, end, is_all_day = _compute_event_times(event_date, horario)

        if not is_all_day and self._tzinfo is not None:
            start = start.replace(tzinfo=self._tzinfo)
            end = end.replace(tzinfo=self._tzinfo)

        summary = self._build_summary(data)
        description = self._build_description(data, record_id)
        location = _coerce_str(data.get("materia"))

        event_kwargs: dict[str, Any] = {
            "summary": summary,
            "start": start,
            "end": end,
            "description": description,
            "extended_properties": {"private": {"agenda_record_id": str(record_id)}},
        }
        if location:
            event_kwargs["location"] = location
        if not is_all_day and self._tzinfo is None:
            event_kwargs["timezone"] = self._timezone_name

        try:
            event = Event(**event_kwargs)
        except Exception as exc:  # pragma: no cover - depende da lib externa
            logger.exception("Erro ao construir evento para o Google Calendar", exc_info=exc)
            return False, f"Não foi possível construir evento para o Google Calendar: {exc}"

        try:
            existing = self._find_existing_event(calendar, record_id)
            if existing:
                event.event_id = existing.event_id
                calendar.update_event(event)
            else:
                calendar.add_event(event)
            return True, None
        except Exception as exc:  # pragma: no cover - depende da API externa
            logger.warning("Falha ao sincronizar evento no Google Calendar", exc_info=exc)
            return False, (
                "Não foi possível sincronizar com o Google Calendar: "
                f"{exc}"
            )

    def delete_event(self, record_id: int) -> Tuple[bool, Optional[str]]:
        calendar = self._ensure_calendar()
        if calendar is None:
            return False, self._last_error

        try:
            existing = self._find_existing_event(calendar, record_id)
            if not existing:
                return True, None
            calendar.delete_event(existing.event_id)
            return True, None
        except Exception as exc:  # pragma: no cover - depende da API externa
            logger.warning("Falha ao remover evento do Google Calendar", exc_info=exc)
            return False, (
                "Não foi possível remover o evento do Google Calendar: "
                f"{exc}"
            )

    def delete_events(self, record_ids: Iterable[int]) -> Tuple[bool, Optional[str]]:
        any_failure = False
        last_message: Optional[str] = None
        for rid in record_ids:
            success, message = self.delete_event(int(rid))
            if not success:
                any_failure = True
                last_message = message
        if any_failure:
            return False, last_message
        return True, None

    # ------------------------------------------------------------------
    # Utilitários de evento
    # ------------------------------------------------------------------
    def _find_existing_event(
        self, calendar: GoogleCalendar, record_id: int
    ) -> Optional[Event]:
        try:
            events = calendar.get_events(
                private_extended_property=f"agenda_record_id={record_id}",
                max_results=1,
                single_events=True,
                show_deleted=True,
            )
            for event in events:
                return event
        except Exception:  # pragma: no cover - depende da API externa
            logger.debug(
                "Erro ao buscar evento existente no Google Calendar", exc_info=True
            )
        return None

    def _build_summary(self, data: Mapping[str, Any]) -> str:
        status = _clean_text(data.get("status")).upper()
        tipo = _clean_text(data.get("tipo_audiencia_pericia"))
        cliente = _clean_text(data.get("cliente"))
        materia = _clean_text(data.get("materia"))

        base = "Compromisso"
        if tipo and cliente:
            base = f"{tipo} - {cliente}"
        elif cliente:
            base = cliente
        elif tipo:
            base = tipo
        elif materia:
            base = materia

        if status:
            return f"[{status}] {base}"
        return base

    def _build_description(self, data: Mapping[str, Any], record_id: int) -> str:
        lines: list[str] = []
        cliente_avisado = _clean_text(data.get("cliente_avisado"))
        if cliente_avisado:
            lines.append(f"Cliente avisado: {cliente_avisado}")
        anotado = _clean_text(data.get("anotado_na_agenda"))
        if anotado:
            lines.append(f"Anotado na agenda: {anotado}")
        numero_processo = _clean_text(data.get("numero_processo"))
        if numero_processo:
            lines.append(f"Número do processo: {numero_processo}")
        tipo = _clean_text(data.get("tipo_audiencia_pericia"))
        if tipo:
            lines.append(f"Tipo: {tipo}")
        materia = _clean_text(data.get("materia"))
        if materia:
            lines.append(f"Matéria: {materia}")
        parte_adversa = _clean_text(data.get("parte_adversa"))
        if parte_adversa:
            lines.append(f"Parte adversa: {parte_adversa}")
        observacao = _clean_text(data.get("observacao"))
        if observacao:
            lines.append(f"Observações: {observacao}")
        sistema = _clean_text(data.get("sistema"))
        if sistema:
            lines.append(f"Origem: {sistema}")
        idx = data.get("idx")
        if idx not in (None, ""):
            lines.append(f"Índice interno: {idx}")
        lines.append(f"ID interno da agenda: {record_id}")
        lines.append("Sincronizado automaticamente pela Planilha de Prazos Geral.")
        return "\n".join(lines)


_service = CalendarService()


def sync_agenda_event(record_id: int, data: Mapping[str, Any]) -> Tuple[bool, Optional[str]]:
    return _service.sync_event(record_id, data)


def delete_agenda_event(record_id: int) -> Tuple[bool, Optional[str]]:
    return _service.delete_event(record_id)


def delete_agenda_events(record_ids: Iterable[int]) -> Tuple[bool, Optional[str]]:
    return _service.delete_events(record_ids)

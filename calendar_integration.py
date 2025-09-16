"""Integração com Google Calendar para sincronizar registros da tabela Agenda."""
from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, Optional, Tuple

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    _GOOGLE_LIBS_AVAILABLE = True
except Exception:  # pragma: no cover - módulo opcional
    service_account = None  # type: ignore[assignment]
    build = None  # type: ignore[assignment]
    HttpError = Exception  # type: ignore[assignment]
    _GOOGLE_LIBS_AVAILABLE = False

try:  # pragma: no cover - streamlit pode não estar disponível em testes
    import streamlit as st
except Exception:  # pragma: no cover - streamlit não é obrigatório aqui
    st = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]
DEFAULT_TIMEZONE = "America/Sao_Paulo"
DEFAULT_DURATION_MINUTES = 60

_SERVICE_ACCOUNT_KEYS = [
    "GOOGLE_CALENDAR_SERVICE_ACCOUNT",
    "GOOGLE_CALENDAR_SERVICE_ACCOUNT_JSON",
    "GOOGLE_SERVICE_ACCOUNT_JSON",
    "GOOGLE_CALENDAR_CREDENTIALS",
    "GOOGLE_CALENDAR_API_KEY",
]
_SERVICE_ACCOUNT_FILE_KEYS = [
    "GOOGLE_CALENDAR_SERVICE_ACCOUNT_FILE",
    "GOOGLE_APPLICATION_CREDENTIALS",
]
_SECTION_KEYS = ("google_calendar", "GOOGLE_CALENDAR")
_TIME_PATTERN = re.compile(r"(\d{1,2})(?:[:hH](\d{2}))?")


def _get_calendar_section() -> Mapping[str, Any]:
    if st is None:
        return {}
    for key in _SECTION_KEYS:
        try:
            section = st.secrets[key]  # type: ignore[index]
        except Exception:
            continue
        if isinstance(section, Mapping):
            return section
    return {}


def _get_config_value(keys: Iterable[str]) -> Any:
    for key in keys:
        value = os.environ.get(key)
        if value:
            return value
    if st is not None:
        for key in keys:
            try:
                value = st.secrets[key]  # type: ignore[index]
            except Exception:
                continue
            if value:
                return value
    section = _get_calendar_section()
    for key in keys:
        value = section.get(key)
        if value:
            return value
    return None


def _coerce_mapping(value: Any) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if os.path.isfile(stripped):
            with open(stripped, "r", encoding="utf-8") as fp:
                return json.load(fp)
        if stripped.startswith("{"):
            return json.loads(stripped)
    return None


def _load_service_account_info() -> Optional[Dict[str, Any]]:
    raw_info = _get_config_value(_SERVICE_ACCOUNT_KEYS)
    info = _coerce_mapping(raw_info)
    if info:
        return info
    file_candidate = _get_config_value(_SERVICE_ACCOUNT_FILE_KEYS)
    info = _coerce_mapping(file_candidate)
    if info:
        return info
    return None


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


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
    if horario:
        horarios = _extract_times(horario)
    else:
        horarios = []
    if not horarios:
        start_date = event_date
        end_date = event_date + timedelta(days=1)
        return start_date, end_date, True
    start_time = horarios[0]
    if len(horarios) > 1:
        end_time = horarios[1]
    else:
        temp_start = datetime.combine(date.today(), start_time)
        temp_end = temp_start + timedelta(minutes=DEFAULT_DURATION_MINUTES)
        end_time = temp_end.time()
    start_dt = datetime.combine(event_date, start_time)
    end_dt = datetime.combine(event_date, end_time)
    if end_dt <= start_dt:
        end_dt = start_dt + timedelta(minutes=DEFAULT_DURATION_MINUTES)
    return start_dt, end_dt, False


def _coerce_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return str(value)


class AgendaCalendarClient:
    def __init__(self) -> None:
        self._service = None
        self._last_error: Optional[str] = None
        self._timezone = (
            _coerce_str(
                _get_config_value(["GOOGLE_CALENDAR_TIMEZONE", "CALENDAR_TIMEZONE"])
            )
            or DEFAULT_TIMEZONE
        )
        self._calendar_id = (
            _coerce_str(
                _get_config_value(["GOOGLE_CALENDAR_ID", "CALENDAR_ID", "CALENDAR"])
            )
            or "primary"
        )

    def _ensure_service(self):
        if self._service is not None:
            return self._service
        if not _GOOGLE_LIBS_AVAILABLE:
            self._last_error = (
                "Bibliotecas do Google não instaladas. Adicione 'google-api-python-client'."
            )
            return None
        info = _load_service_account_info()
        if not info:
            self._last_error = (
                "Credenciais do Google Calendar não configuradas. Informe o JSON do service account."
            )
            return None
        try:
            creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)  # type: ignore[arg-type]
            delegate = _coerce_str(
                _get_config_value([
                    "GOOGLE_CALENDAR_DELEGATE",
                    "GOOGLE_CALENDAR_SUBJECT",
                    "CALENDAR_DELEGATE",
                ])
            )
            if delegate:
                creds = creds.with_subject(delegate)
            self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)
            return self._service
        except Exception as exc:  # pragma: no cover - dependente do ambiente
            logger.exception("Erro ao inicializar integração com Google Calendar", exc_info=exc)
            self._last_error = f"Falha ao iniciar integração com Google Calendar: {exc}"
            return None

    def sync_event(self, record_id: int, data: Mapping[str, Any]) -> Tuple[bool, Optional[str]]:
        service = self._ensure_service()
        if not service:
            return False, self._last_error
        event_date = _ensure_date(data.get("data"))
        if not event_date:
            return False, "Registro da agenda sem data. Evento não sincronizado."
        horario = _coerce_str(data.get("horario"))
        start, end, is_all_day = _compute_event_times(event_date, horario)
        summary = self._build_summary(data)
        description = self._build_description(data, record_id)
        event_body: Dict[str, Any] = {
            "summary": summary,
            "description": description,
            "extendedProperties": {
                "private": {"agenda_record_id": str(record_id)}
            },
        }
        if is_all_day:
            event_body["start"] = {"date": start.isoformat(), "timeZone": self._timezone}
            event_body["end"] = {"date": end.isoformat(), "timeZone": self._timezone}
        else:
            event_body["start"] = {"dateTime": start.isoformat(), "timeZone": self._timezone}
            event_body["end"] = {"dateTime": end.isoformat(), "timeZone": self._timezone}
        location = _coerce_str(data.get("materia"))
        if location:
            event_body["location"] = location
        try:
            existing = self._find_existing_event(service, record_id)
            if existing:
                service.events().update(
                    calendarId=self._calendar_id,
                    eventId=existing["id"],
                    body=event_body,
                ).execute()
            else:
                service.events().insert(
                    calendarId=self._calendar_id,
                    body=event_body,
                ).execute()
            return True, None
        except HttpError as exc:  # pragma: no cover - depende da API externa
            logger.warning("Falha ao sincronizar evento no Google Calendar", exc_info=exc)
            message = getattr(exc, "error_details", None) or getattr(exc, "message", str(exc))
            if hasattr(exc, "resp") and getattr(exc.resp, "status", None) == 404:
                return False, "Calendário informado não foi encontrado."
            return False, f"Não foi possível sincronizar com o Google Calendar: {message}"

    def delete_event(self, record_id: int) -> Tuple[bool, Optional[str]]:
        service = self._ensure_service()
        if not service:
            return False, self._last_error
        try:
            existing = self._find_existing_event(service, record_id)
            if not existing:
                return True, None
            service.events().delete(
                calendarId=self._calendar_id, eventId=existing["id"]
            ).execute()
            return True, None
        except HttpError as exc:  # pragma: no cover - depende da API externa
            if hasattr(exc, "resp") and getattr(exc.resp, "status", None) == 404:
                return True, None
            logger.warning("Falha ao remover evento do Google Calendar", exc_info=exc)
            message = getattr(exc, "error_details", None) or getattr(exc, "message", str(exc))
            return False, f"Não foi possível remover o evento do Google Calendar: {message}"

    def delete_events(self, record_ids: Iterable[int]) -> Tuple[bool, Optional[str]]:
        any_failure = False
        last_message = None
        for rid in record_ids:
            success, message = self.delete_event(int(rid))
            if not success:
                any_failure = True
                last_message = message
        if any_failure:
            return False, last_message
        return True, None

    def _find_existing_event(self, service, record_id: int) -> Optional[Dict[str, Any]]:
        try:
            response = (
                service.events()
                .list(
                    calendarId=self._calendar_id,
                    maxResults=1,
                    privateExtendedProperty=f"agenda_record_id={record_id}",
                    showDeleted=True,
                    singleEvents=True,
                )
                .execute()
            )
            items = response.get("items", [])
            if items:
                return items[0]
        except HttpError as exc:  # pragma: no cover - depende da API externa
            logger.debug("Erro ao buscar evento existente", exc_info=exc)
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
        lines = []
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


_client = AgendaCalendarClient()


def sync_agenda_event(record_id: int, data: Mapping[str, Any]) -> Tuple[bool, Optional[str]]:
    return _client.sync_event(record_id, data)


def delete_agenda_event(record_id: int) -> Tuple[bool, Optional[str]]:
    return _client.delete_event(record_id)


def delete_agenda_events(record_ids: Iterable[int]) -> Tuple[bool, Optional[str]]:
    return _client.delete_events(record_ids)

"""Integração com Google Calendar usando a biblioteca `gcsa`."""
from __future__ import annotations

import inspect
import json
import logging
import os
import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, Optional, Tuple

try:  # pragma: no cover - dependente das libs opcionais
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account
    from google.oauth2.credentials import Credentials as UserCredentials

    _GOOGLE_AUTH_AVAILABLE = True
except Exception:  # pragma: no cover - módulo opcional
    Request = None  # type: ignore[assignment]
    service_account = None  # type: ignore[assignment]
    UserCredentials = None  # type: ignore[assignment]
    _GOOGLE_AUTH_AVAILABLE = False

try:  # pragma: no cover - biblioteca opcional
    from gcsa.event import Event
    from gcsa.google_calendar import GoogleCalendar

    _GCSA_AVAILABLE = True
except Exception:  # pragma: no cover - biblioteca opcional
    Event = None  # type: ignore[assignment]
    GoogleCalendar = None  # type: ignore[assignment]
    _GCSA_AVAILABLE = False

try:  # pragma: no cover - streamlit pode não estar disponível em testes
    import streamlit as st
except Exception:  # pragma: no cover - streamlit não é obrigatório aqui
    st = None  # type: ignore[assignment]

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
_OAUTH_TOKEN_KEYS = [
    "GOOGLE_CALENDAR_OAUTH_TOKEN",
    "GOOGLE_CALENDAR_USER_TOKEN",
    "GOOGLE_CALENDAR_CREDENTIALS_JSON",
]
_OAUTH_TOKEN_FILE_KEYS = [
    "GOOGLE_CALENDAR_OAUTH_TOKEN_FILE",
    "GOOGLE_CALENDAR_USER_TOKEN_FILE",
]
_OAUTH_CLIENT_KEYS = [
    "GOOGLE_CALENDAR_OAUTH_CLIENT",
    "GOOGLE_CALENDAR_CLIENT",
    "GOOGLE_CALENDAR_CLIENT_JSON",
]
_OAUTH_CLIENT_FILE_KEYS = [
    "GOOGLE_CALENDAR_OAUTH_CLIENT_FILE",
    "GOOGLE_CALENDAR_CLIENT_FILE",
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


def _load_oauth_credentials() -> Optional[UserCredentials]:
    if not _GOOGLE_AUTH_AVAILABLE or UserCredentials is None:
        return None
    token_info = _coerce_mapping(_get_config_value(_OAUTH_TOKEN_KEYS))
    if not token_info:
        token_info = _coerce_mapping(_get_config_value(_OAUTH_TOKEN_FILE_KEYS))
    if not token_info:
        token_info = _coerce_mapping(_get_config_value(_OAUTH_CLIENT_KEYS))
    if not token_info:
        token_info = _coerce_mapping(_get_config_value(_OAUTH_CLIENT_FILE_KEYS))
    if not token_info:
        return None
    token = _coerce_str(token_info.get("token")) or _coerce_str(
        token_info.get("access_token")
    )
    refresh_token = _coerce_str(token_info.get("refresh_token"))
    client_id = _coerce_str(token_info.get("client_id"))
    client_secret = _coerce_str(token_info.get("client_secret"))
    token_uri = _coerce_str(token_info.get("token_uri")) or "https://oauth2.googleapis.com/token"
    if not client_id or not client_secret or not refresh_token:
        return None
    creds = UserCredentials(
        token=token,
        refresh_token=refresh_token,
        token_uri=token_uri,
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    expiry = _coerce_str(token_info.get("expiry")) or _coerce_str(
        token_info.get("expires_at")
    )
    if expiry:
        try:
            creds.expiry = datetime.fromisoformat(expiry)
        except ValueError:
            pass
    return creds


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
        self._calendar: Optional[GoogleCalendar] = None
        self._last_error: Optional[str] = None
        self._timezone = _coerce_str(
            _get_config_value(["GOOGLE_CALENDAR_TIMEZONE", "CALENDAR_TIMEZONE"])
        ) or DEFAULT_TIMEZONE
        self._calendar_id = _coerce_str(
            _get_config_value(["GOOGLE_CALENDAR_ID", "CALENDAR_ID", "CALENDAR"])
        ) or "primary"
        self._tzinfo: Optional[ZoneInfo]
        try:
            self._tzinfo = ZoneInfo(self._timezone)
        except ZoneInfoNotFoundError:
            logger.warning(
                "Timezone '%s' não encontrada. Eventos serão criados sem informação de timezone.",
                self._timezone,
            )
            self._tzinfo = None

    def _build_credentials(self):
        info = _load_service_account_info()
        if info and service_account is not None:
            try:
                creds = service_account.Credentials.from_service_account_info(
                    info, scopes=SCOPES  # type: ignore[arg-type]
                )
                delegate = _coerce_str(
                    _get_config_value(
                        [
                            "GOOGLE_CALENDAR_DELEGATE",
                            "GOOGLE_CALENDAR_SUBJECT",
                            "CALENDAR_DELEGATE",
                        ]
                    )
                )
                if delegate:
                    creds = creds.with_subject(delegate)
                return creds
            except Exception as exc:  # pragma: no cover - dependente do ambiente
                logger.exception(
                    "Erro ao carregar credenciais do service account para o Google Calendar",
                    exc_info=exc,
                )
                self._last_error = f"Falha ao carregar credenciais do service account: {exc}"
                return None
        oauth_creds = _load_oauth_credentials()
        if oauth_creds is None:
            self._last_error = (
                "Credenciais do Google Calendar não configuradas. Informe o JSON do service account ou as credenciais OAuth."
            )
            return None
        if (
            Request is not None
            and oauth_creds.refresh_token
            and not oauth_creds.valid
        ):
            try:  # pragma: no cover - dependente de refresh externo
                oauth_creds.refresh(Request())
            except Exception as exc:
                logger.warning(
                    "Falha ao renovar token OAuth do Google Calendar", exc_info=exc
                )
                self._last_error = (
                    "Não foi possível renovar o token OAuth do Google Calendar."
                )
                return None
        return oauth_creds

    def _ensure_calendar(self) -> Optional[GoogleCalendar]:
        if self._calendar is not None:
            return self._calendar
        if not _GCSA_AVAILABLE or GoogleCalendar is None:
            self._last_error = (
                "Biblioteca 'gcsa' não está instalada. Adicione 'gcsa' às dependências."
            )
            return None
        credentials = self._build_credentials()
        if credentials is None:
            return None
        try:
            init_args = []
            init_kwargs: Dict[str, Any] = {}
            try:
                signature = inspect.signature(GoogleCalendar.__init__)
                parameters = {
                    name: param
                    for name, param in signature.parameters.items()
                    if name != "self"
                }
            except (TypeError, ValueError):
                parameters = {}
            candidate_values: Dict[str, Any] = {
                "calendar": self._calendar_id,
                "calendar_id": self._calendar_id,
                "default_calendar": self._calendar_id,
                "credentials": credentials,
                "timezone": self._timezone,
            }
            provided_keys = set()
            for name, param in parameters.items():
                if name not in candidate_values:
                    continue
                value = candidate_values[name]
                provided_keys.add(name)
                if param.kind is inspect.Parameter.POSITIONAL_ONLY:
                    init_args.append(value)
                elif param.kind is inspect.Parameter.VAR_POSITIONAL:
                    init_args.append(value)
                else:
                    init_kwargs[name] = value

            if not provided_keys.intersection({"calendar", "calendar_id", "default_calendar"}):
                if parameters:
                    first_param = next(iter(parameters.values()))
                    if first_param.kind in (
                        inspect.Parameter.POSITIONAL_ONLY,
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    ):
                        init_args.insert(0, self._calendar_id)
                else:
                    init_args.append(self._calendar_id)

            try:
                calendar = GoogleCalendar(*init_args, **init_kwargs)
            except TypeError:
                if "timezone" in init_kwargs and "timezone" not in parameters:
                    init_kwargs.pop("timezone", None)
                    calendar = GoogleCalendar(*init_args, **init_kwargs)
                else:
                    raise
            self._calendar = calendar
            return calendar
        except Exception as exc:  # pragma: no cover - dependente do ambiente
            logger.exception(
                "Erro ao inicializar integração com Google Calendar (gcsa)",
                exc_info=exc,
            )
            self._last_error = (
                "Falha ao iniciar integração com Google Calendar usando gcsa: "
                f"{exc}"
            )
            return None

    def sync_event(self, record_id: int, data: Mapping[str, Any]) -> Tuple[bool, Optional[str]]:
        calendar = self._ensure_calendar()
        if not calendar:
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
        event_kwargs: Dict[str, Any] = {
            "summary": summary,
            "start": start,
            "end": end,
            "description": description,
            "extended_properties": {"private": {"agenda_record_id": str(record_id)}},
        }
        if location:
            event_kwargs["location"] = location
        if not is_all_day and self._tzinfo is None:
            event_kwargs["timezone"] = self._timezone
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
                "Não foi possível sincronizar com o Google Calendar usando gcsa: "
                f"{exc}"
            )

    def delete_event(self, record_id: int) -> Tuple[bool, Optional[str]]:
        calendar = self._ensure_calendar()
        if not calendar:
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
                "Não foi possível remover o evento do Google Calendar usando gcsa: "
                f"{exc}"
            )

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
            logger.debug("Erro ao buscar evento existente no Google Calendar", exc_info=True)
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

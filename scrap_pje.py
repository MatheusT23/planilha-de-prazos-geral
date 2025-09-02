from __future__ import annotations

"""Scraper para publicações do PJe Comunica.

Este script aproveita a infraestrutura existente do projeto (DB, controle de
`last_checked`, classificação de setores) para buscar publicações disponíveis em
https://comunica.pje.jus.br.

Principais características
-------------------------
* Busca por janela de datas e OAB fixo (198943).
* Usa Playwright (API síncrona) com seletores baseados em rótulos/texto.
* Evita atributos voláteis do Angular (como `_ngcontent-*`).
* Faz upsert idempotente na tabela ``publicacoes`` usando hash de dedupe.
* Atualiza a tabela ``last_checked`` com ``scope = 'pje_comunica'``.
"""

import argparse
import hashlib
import logging
import time
import re
from datetime import date, datetime, timedelta, time as dt_time
from typing import List, Dict

import pytz
from tenacity import retry, stop_after_attempt, wait_random
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from psycopg2.extras import execute_values

from db import engine  # type: ignore
from last_checked_utils import get_last_checked, set_last_checked  # type: ignore
from scrap_email import checar_palavra_chave  # type: ignore

# ---------------------------------------------------------------------------
# Configurações globais
# ---------------------------------------------------------------------------
OAB = "198943"
SCOPE = "pje_comunica"
TZ = pytz.timezone("America/Sao_Paulo")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
CNJ_REGEX = re.compile(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------
def parse_date_br(s: str) -> date | None:
    """Tenta converter uma string de data para ``date`` (formato brasileiro)."""
    s = (s or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def compute_hash(processo: str, inicio: date, observacoes: str) -> str:
    obs_md5 = hashlib.md5((observacoes or "").encode("utf-8")).hexdigest()[:16]
    base = f"{(processo or '').lower()}|{inicio:%Y-%m-%d}|{obs_md5}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------
@retry(wait=wait_random(1, 2), stop=stop_after_attempt(3))
def fetch_publicacoes_for_day(d: date) -> List[Dict]:
    """Obtém todas as publicações de um dia."""
    url = (
        "https://comunica.pje.jus.br/consulta?"
        f"dataDisponibilizacaoInicio={d:%Y-%m-%d}&"
        f"dataDisponibilizacaoFim={d:%Y-%m-%d}&numeroOab={OAB}"
    )
    logger.info("Navegando em %s", url)
    records: List[Dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()
        page.goto(url, wait_until="networkidle")

        try:
            page.wait_for_selector('text="Data da disponibilização"', timeout=10000)
        except PlaywrightTimeoutError:
            logger.info("Nenhum resultado para %s", d)
            context.close()
            browser.close()
            return records

        # Paginação / infinite scroll: rola até não surgirem novos cards
        last_count = -1
        while True:
            cards = page.locator(
                "xpath=//*[contains(., 'Data da disponibilização')]/ancestor::div[@role='article' or contains(@class,'card')]"
            )
            count = cards.count()
            if count == last_count:
                break
            last_count = count
            page.mouse.wheel(0, 10000)
            time.sleep(1)

        cards = page.locator(
            "xpath=//*[contains(., 'Data da disponibilização')]/ancestor::div[@role='article' or contains(@class,'card')]"
        )
        for i in range(cards.count()):
            card = cards.nth(i)
            html = card.inner_html()
            texto = card.inner_text()

            def _ext(label: str) -> str:
                """Extrai o texto do primeiro sibling após o rótulo."""
                try:
                    loc = card.locator(
                        f"xpath=.//*[contains(normalize-space(text()), '{label}')]/following-sibling::*[1]"
                    )
                    if loc.count():
                        return loc.inner_text().strip()
                except Exception:
                    pass
                return ""

            record = {
                "data_disponibilizacao": _ext("Data da disponibilização"),
                "partes": _ext("Partes"),
                "processo": _ext("Processo"),
                "texto": texto,
                "raw_html": html,
            }
            records.append(record)

        context.close()
        browser.close()
    return records


# ---------------------------------------------------------------------------
# Normalização e persistência
# ---------------------------------------------------------------------------
def normalize_record(raw: Dict) -> Dict:
    """Normaliza campos extraídos do PJe Comunica."""
    inicio = parse_date_br(raw.get("data_disponibilizacao", "")) or date.today()
    processo = raw.get("processo", "")
    m = CNJ_REGEX.search(processo)
    processo = m.group(0) if m else ""
    partes = raw.get("partes") or ""
    observacoes = re.sub(r"\s+", " ", raw.get("texto", "")).strip()
    setor = checar_palavra_chave(observacoes)
    return {
        "inicio_prazo": inicio,
        "setor": setor,
        "cliente": partes,
        "processo": processo,
        "observacoes": observacoes,
        "fonte": "PJe Comunica",
        "capturado_em": datetime.now(TZ),
        "oab": OAB,
        "raw_html": raw.get("raw_html", ""),
    }


def save_records(recs: List[Dict]) -> int:
    if not recs:
        return 0
    for r in recs:
        r["hash_dedup"] = compute_hash(r["processo"], r["inicio_prazo"], r["observacoes"])

    cols = [
        "hash_dedup",
        "inicio_prazo",
        "setor",
        "cliente",
        "processo",
        "observacoes",
        "fonte",
        "capturado_em",
        "oab",
        "raw_html",
    ]
    values = [[r.get(c) for c in cols] for r in recs]
    sql = f"""
        INSERT INTO publicacoes ({', '.join(cols)})
        VALUES %s
        ON CONFLICT (hash_dedup) DO UPDATE SET
            inicio_prazo = EXCLUDED.inicio_prazo,
            setor = EXCLUDED.setor,
            cliente = EXCLUDED.cliente,
            processo = EXCLUDED.processo,
            observacoes = EXCLUDED.observacoes,
            fonte = EXCLUDED.fonte,
            capturado_em = EXCLUDED.capturado_em,
            oab = EXCLUDED.oab,
            raw_html = EXCLUDED.raw_html
    """
    conn = engine.raw_connection()
    try:
        with conn.cursor() as cur:
            execute_values(cur, sql, values)
        conn.commit()
    finally:
        conn.close()
    return len(recs)


# ---------------------------------------------------------------------------
# Execução principal
# ---------------------------------------------------------------------------
def run(from_date: date | None = None, to_date: date | None = None, dry_run: bool = False) -> int:
    today = datetime.now(TZ).date()
    last_dt = get_last_checked(SCOPE)
    if from_date is None or to_date is None:
        if last_dt:
            start = last_dt.astimezone(TZ).date()
        else:
            start = today
        from_date = from_date or start
        to_date = to_date or today

    if to_date < from_date:
        raise ValueError("to_date deve ser >= from_date")

    total_inserted = 0
    cur = from_date
    while cur <= to_date:
        logger.info("Processando dia %s", cur)
        try:
            raw = fetch_publicacoes_for_day(cur)
            norm = [normalize_record(r) for r in raw]
            if dry_run:
                for sample in norm[:2]:
                    logger.info(
                        "Amostra: %s",
                        {
                            "inicio_prazo": sample["inicio_prazo"],
                            "setor": sample["setor"],
                            "cliente": sample["cliente"],
                            "processo": sample["processo"],
                            "observacoes": sample["observacoes"][:200],
                        },
                    )
                logger.info("Total de registros em %s: %d", cur, len(norm))
            else:
                inserted = save_records(norm)
                total_inserted += inserted
                logger.info("Inseridos %d registros para %s", inserted, cur)
            # Atualiza last_checked após sucesso do dia
            end_dt = datetime.combine(cur, dt_time(23, 59, 59)).replace(tzinfo=TZ)
            set_last_checked(SCOPE, end_dt)
        except Exception:
            logger.exception("Falha ao processar o dia %s", cur)
            break
        cur += timedelta(days=1)
        time.sleep(1)  # evita possíveis bloqueios
    return total_inserted


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main() -> None:
    parser = argparse.ArgumentParser(description="Scraper do PJe Comunica")
    parser.add_argument("--from", dest="from_date", type=_parse_date)
    parser.add_argument("--to", dest="to_date", type=_parse_date)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    inserted = run(args.from_date, args.to_date, args.dry_run)
    logger.info("Total inserido: %d", inserted)


if __name__ == "__main__":
    main()

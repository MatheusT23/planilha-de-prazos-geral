# scrap_email.py — DB-only (no Excel)
# -----------------------------------------------------------------------------
# Lê e-mails via IMAP e grava tudo diretamente no banco (Postgres) usando SQLAlchemy.
# Compatível com app_streamlit_hibrido.py (usa buscar_e_processar_emails()) e com db.py.
# Requisitos: imaplib, email, beautifulsoup4, sqlalchemy, python-dotenv (opcional)
#
# Dica: defina DATABASE_URL no ambiente (.env, env var ou st.secrets), conforme db.py.
# -----------------------------------------------------------------------------
import imaplib
import email
from email.utils import parsedate_to_datetime
from bs4 import BeautifulSoup
import re
import os
import time
import unicodedata
from datetime import datetime, timezone, timedelta, date

# DB imports
from db import SessionLocal, Andamento, Publicacao, Agenda, LastChecked  # type: ignore

# ====== Config de e-mail ======
EMAIL = os.getenv("INBOX_EMAIL", "dri.rodrigues99@yahoo.com.br")
PASSWORD = os.getenv("INBOX_PASSWORD", "txiwfunruupgweao")
IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.mail.yahoo.com")

# ====== Remetentes e regras ======
REMETENTES = [
    "nao-responda@trt1.jus.br",
    "nao-responda@trtsp.jus.br",
    "eproc-bounce@trf2.jus.br",
    "rd_oabrj@recortedigital.adv.br",
    "pmfgestao@pmf.mps.gov.br",
]
CORRECOES_ACENTOS = {
    "Percia": "Perícia",
    "Mdica": "Médica",
    "Audiencia": "Audiência",
    "Servio": "Serviço",
    "Servico": "Serviço",
    "Majorao": "Majoração",
    "Majoraçao": "Majoração",
    "Itaborai": "Itaboraí",
}
PALAVRAS_CHAVE = [
    "rpv", "alvará", "alvara", "precatório", "precatorio", "acordo homologado",
    "expedição de rpv", "expedicao de rpv", "expedido", "pagamento",
]

# ====== Compat c/ app_streamlit_hibrido.py ======
STREAMLIT_MODE = False  # apenas para compatibilidade; sem efeito em DB
USE_DB = True           # sempre True neste script (DB-only)
def set_streamlit_mode(enabled: bool = True):
    global STREAMLIT_MODE
    STREAMLIT_MODE = enabled  # mantido só para não quebrar imports externos


# ====== Utilidades de datas/horas ======
def format_imap_date(dt: datetime) -> str:
    """Return date formatted for IMAP queries (e.g. '05-Jul-2024').

    IMAP always expects English month abbreviations regardless of the
    server/client locale. Using strftime with a different locale may generate
    invalid month names (like 'mai' or 'set'), which causes the server to
    respond with *BAD [CLIENTBUG] SEARCH Command arguments invalid*. This
    helper builds the string manually to avoid locale dependence.
    """

    months = [
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ]
    return f"{dt.day:02d}-{months[dt.month - 1]}-{dt.year}"

def to_date_or_none(v):
    """Converte 'DD/MM/YY' ou 'DD/MM/YYYY' para date; aceita date/datetime; senão None."""
    if v is None or v == "":
        return None
    if isinstance(v, date):
        return v
    if isinstance(v, datetime):
        return v.date()
    s = str(v).strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass
    try:
        # tenta ISO (YYYY-MM-DD)
        return datetime.fromisoformat(s).date()
    except Exception:
        return None


def ler_ultima_data():
    """Obtém a última data/hora processada da tabela last_checked em tz -03:00."""
    fuso_brasil = timezone(timedelta(hours=-3))
    with SessionLocal() as db:
        rec = db.query(LastChecked).first()
        if rec and rec.checked_at:
            return rec.checked_at.astimezone(fuso_brasil)
    return datetime.min.replace(tzinfo=fuso_brasil)


def salvar_ultima_data(dt: datetime):
    """Atualiza ou insere a marca de tempo mais recente processada (em tz -03:00)."""
    fuso_brasil = timezone(timedelta(hours=-3))
    if dt.tzinfo is not None:
        dt_local = dt.astimezone(fuso_brasil)
    else:
        dt_local = dt.replace(tzinfo=fuso_brasil)
    with SessionLocal() as db:
        rec = db.query(LastChecked).first()
        if rec:
            rec.checked_at = dt_local
        else:
            db.add(LastChecked(checked_at=dt_local))
        db.commit()


# ====== Normalização e parsing de textos ======
def corrigir_acentos(texto: str) -> str:
    for errado, certo in CORRECOES_ACENTOS.items():
        texto = texto.replace(errado, certo)
    return texto

def normalizar(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ASCII", "ignore").decode().lower()

def _collapse_spaces(s: str) -> str:
    return " ".join((s or "").replace("\r", " ").replace("\n", " ").split())

def _find_after(haystack_lower: str, needle_lower: str, start_at: int = 0) -> int:
    idx = haystack_lower.find(needle_lower, start_at)
    if idx == -1:
        return -1
    return idx + len(needle_lower)


# ====== Regras de extração ======
def limpar_data_evento(texto: str) -> str:
    """Remove prefixo 'Data Evento DD/MM/YYYY HH:MM' do início, quando existir."""
    texto = (texto or "").strip()
    padrao = r"^Data Evento \d{2}/\d{2}/\d{4} \d{2}:\d{2}\s*"
    return re.sub(padrao, "", texto)

def extrair_nome_polo_ativo_publicacao(texto: str) -> str:
    if not texto:
        return ""
    t = _collapse_spaces(texto)
    tl = t.lower()
    key = "polo ativo:"
    i = _find_after(tl, key)
    if i == -1:
        return ""
    while i < len(t) and t[i] == " ":
        i += 1
    stops = [
        " polo passivo:", " advogado:", " intimacao", " despacho", " ato ordinatorio",
        " classe:", " autor:", " reu:", " parte autora:", " lista de distribuicao",
        " decisão", " decisao",
    ]
    j = i
    while j < len(t):
        low_slice = tl[j:]
        if any(low_slice.startswith(sw) for sw in stops):
            break
        j += 1
    nome = t[i:j].strip(" -–—.")
    while nome.endswith((".", "-", "–", "—")):
        nome = nome[:-1].strip()
    return nome

def extract_client_names_trt1(body: str) -> str:
    """Pega nomes de Autor/Autora/Parte Autora/Reclamante (string '; ' separada)."""
    import re as _re, unicodedata as _ud
    def normalize(s: str) -> str:
        return _ud.normalize("NFKD", s).encode("ASCII", "ignore").decode().strip().lower()
    def clean_name(s: str) -> str:
        s = _re.sub(r"\s+", " ", s).strip(" -–—\t")
        return s.strip(" .;:")

    HEADER_LABELS = ["autor:", "autora:", "parte autora:", "reclamante:"]
    END_LABELS = [
        "advogados do autor:", "advogados do réu:", "advogados do reu:", "réu:", "reu:",
        "classe judicial:", "órgão julgador:", "orgao julgador:", "eventos:", "número do processo:",
        "numero do processo:", "data de autuação:", "data de autuacao:",
    ]

    if not isinstance(body, str) or not body.strip():
        return ""
    text = body.replace("\r", "")
    lines = text.split("\n")
    for i, line in enumerate(lines):
        ln = normalize(line)
        if any(ln == h or ln.startswith(h) for h in HEADER_LABELS):
            candidate = ""
            if ":" in line:
                candidate = line.split(":", 1)[1].strip()
            j = i + 1
            while not candidate and j < min(i + 6, len(lines)):
                nxt = lines[j].strip()
                if nxt:
                    if normalize(nxt) in END_LABELS:
                        break
                    candidate = nxt
                    break
                j += 1
            candidate = clean_name(candidate)
            if not candidate:
                return ""
            names = [clean_name(x) for x in re.split(r"[;\n]+", candidate) if clean_name(x)]
            seen = set()
            ordered = []
            for n in names:
                key = normalize(n)
                if key not in seen:
                    seen.add(key)
                    ordered.append(n)
            return "; ".join(ordered)
    return ""

def extrair_numero_processo_do_corpo(texto: str) -> str:
    if not texto:
        return ""
    s = texto
    n = len(s)

    def match_proc(i: int):
        j = i
        def take_digits(k):
            nonlocal j
            if j + k > n or not s[j:j+k].isdigit():
                return None
            val = s[j:j+k]; j += k; return val
        d1 = take_digits(7)
        if not d1: return None
        if j >= n or s[j] != '-': return None; j += 1
        j += 1
        d2 = take_digits(2)
        if not d2: return None
        if j >= n or s[j] != '.': return None; j += 1
        j += 1
        d3 = take_digits(4)
        if not d3: return None
        if j >= n or s[j] != '.': return None; j += 1
        j += 1
        d4 = take_digits(1)
        if not d4: return None
        if j >= n or s[j] != '.': return None; j += 1
        j += 1
        d5 = take_digits(2)
        if not d5: return None
        if j >= n or s[j] != '.': return None; j += 1
        j += 1
        d6 = take_digits(4)
        if not d6: return None
        return f"{d1}-{d2}.{d3}.{d4}.{d5}.{d6}"

    i = 0
    while i < n - 24:
        if s[i].isdigit():
            got = match_proc(i)
            if got:
                return got
        i += 1
    return ""

def detectar_audiencia_pericia(texto: str):
    tl = normalizar(texto)
    if "audiencia" in tl:
        return "audiencia"
    if "pericia" in tl:
        return "pericia"
    return None

def _scan_date_simple(window: str) -> str:
    if not window:
        return ""
    n = len(window); i = 0
    while i + 9 < n:
        if (window[i:i+2].isdigit() and i+2 < n and window[i+2] == "/" and
            window[i+3:i+5].isdigit() and i+5 < n and window[i+5] == "/" and
            window[i+6:i+10].isdigit()):
            return f"{window[i:i+2]}/{window[i+3:i+5]}/{window[i+6:i+10]}"
        i += 1
    return ""

def _std_time_token(tok: str) -> str:
    if not tok:
        return ""
    t = "".join(tok.split())
    tl = normalizar(t)
    if "h" in tl:
        p = tl.split("h")
        if p[0].isdigit():
            h = int(p[0]); m = 0
            if len(p) > 1 and p[1]:
                m_part = p[1].replace("min", "").replace("m", "")
                if m_part.isdigit():
                    m = int(m_part)
            if 0 <= h <= 23 and 0 <= m <= 59:
                return f"{h:02d}:{m:02d}"
    if ":" in t:
        t2 = t.replace("h", "").replace("H", "")
        parts = t2.split(":")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            h = int(parts[0]); m = int(parts[1])
            if 0 <= h <= 23 and 0 <= m <= 59:
                return f"{h:02d}:{m:02d}"
    if "horas" in tl or "hora" in tl:
        nums = "".join(ch for ch in tl if ch.isdigit())
        if nums.isdigit():
            h = int(nums)
            if 0 <= h <= 23:
                return f"{h:02d}:00"
    return ""

def _scan_time_simple(window: str) -> str:
    if not window:
        return ""
    tokens = []
    cur = []
    for ch in window:
        if ch.isalnum() or ch in [":", "h", "H"]:
            cur.append(ch)
        else:
            if cur:
                tokens.append("".join(cur)); cur = []
    if cur:
        tokens.append("".join(cur))
    for tok in tokens:
        tm = _std_time_token(tok)
        if tm:
            return tm
    return ""

def extrair_data_hora_evento(texto: str, tipo: str):
    if not texto:
        return "", ""
    t = texto.replace("\r", "")
    tn = normalizar(t)
    alvo = "audien" if (tipo or "").lower().startswith("aud") else "peric"
    pos = tn.find(alvo)
    if pos == -1:
        pos_a = tn.find("audien")
        pos_p = tn.find("peric")
        cand = [p for p in [pos_a, pos_p] if p != -1]
        if not cand:
            return "", ""
        pos = min(cand)
    start = pos
    end = min(len(t), start + 600)
    window = t[start:end]
    data = _scan_date_simple(window)
    if data:
        idx = window.find(data)
        sub = window[idx: idx + 120]
        hora = _scan_time_simple(sub)
        return data, (hora or "")
    hora = _scan_time_simple(window)
    if hora:
        idx = window.find(hora)
        sub = window[idx: idx + 160]
        data = _scan_date_simple(sub)
        if data:
            return data, hora
    return "", ""

def extrair_tipo_audiencia_pericia(texto: str) -> str:
    if not texto:
        return ""
    t = texto.replace("\r", "")
    m = re.search(r"(Audi[eê]ncia[^.\n\)]+)", t, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m2 = re.search(r"(Per[ií]cia[^.\n\)]+)", t, flags=re.IGNORECASE)
    if m2:
        return m2.group(1).strip()
    kind = detectar_audiencia_pericia(texto)
    if kind == "audiencia":
        return "Audiência"
    if kind == "pericia":
        return "Perícia"
    return ""

def processar_corpo_trt1(corpo: str):
    texto = " ".join([linha.strip() for linha in corpo.splitlines() if linha.strip()])
    numero_processo = ""
    eventos = ""
    if "número do processo:" in texto.lower():
        idx = texto.lower().find("número do processo:")
        parte = texto[idx + len("número do processo:"):].lstrip()
        num = ""
        for c in parte:
            if c in "0123456789-.":
                num += c
            else:
                break
        if "-" in num and "." in num:
            numero_processo = num
    if "eventos:" in texto.lower():
        idx = texto.lower().find("eventos:")
        parte = texto[idx + len("eventos:"):].lstrip()
        fim = parte.find("Para acessar")
        if fim == -1: fim = parte.find("https://")
        if fim == -1: fim = parte.find("ATENÇÃO")
        if fim == -1: fim = None
        eventos = parte[:fim].strip() if fim is not None else parte.strip()
    return numero_processo, eventos

def processar_corpo_trtsp(corpo: str):
    return processar_corpo_trt1(corpo)

def processar_corpo_trf2(corpo: str):
    texto = " ".join([linha.strip() for linha in corpo.splitlines() if linha.strip()])
    numero_processo = ""
    eventos = ""
    if "número do processo:" in texto.lower():
        idx = texto.lower().find("número do processo:")
        parte = texto[idx + len("número do processo:"):].lstrip()
        num = ""
        for c in parte:
            if c in "0123456789-.":
                num += c
            else:
                break
        if "-" in num and "." in num:
            numero_processo = num
    if "evento:" in texto.lower():
        idx = texto.lower().find("evento:")
        parte = texto[idx + len("evento:"):].lstrip()
        fim = parte.find("Nome da(s) Parte(s):")
        if fim == -1: fim = parte.find("Órgão Julgador:")
        if fim == -1: fim = None
        eventos = parte[:fim].strip() if fim is not None else parte.strip()
    return numero_processo, eventos

def processar_corpo_generico(corpo: str):
    return "", ""

def processar_recorte_publicacao(corpo: str):
    blocos = re.split(r"\n\s*Publicação:\s*\d+\s*", corpo)
    resultado = []
    for bloco in blocos[1:]:
        m_data_pub = re.search(r"Data de Publicação:\s*([0-9/]+)", bloco)
        data_pub = m_data_pub.group(1) if m_data_pub else ""
        m_proc = re.search(r"PROCESSO:\s*([\d.-]+)", bloco)
        processo = m_proc.group(1) if m_proc else ""
        m_evento = re.search(r"(PROCESSO:.*?)(Acesso ao documento:|Identificador do documento:|$)", bloco, re.DOTALL)
        evento = m_evento.group(1).strip() if m_evento else bloco.strip()
        evento = re.sub(r"\n\s*\n+", "\n", evento).strip()
        if processo or data_pub:
            resultado.append({"data": data_pub, "processo": processo, "evento": evento})
    return resultado

def is_edital_nomeacao_publicacao(texto: str) -> bool:
    texto_lower = (texto or "").lower()
    palavras_chave = [
        "edital nomeacao", "edital nomeação", "edital nomeacao funcao especial",
        "edital nomeacao mesario", "eleicoes municipais", "foram nomeados mesarios",
        "presidente de mrv", "1º mesario - mrv", "2º mesario - mrv",
    ]
    return any(chave in texto_lower for chave in palavras_chave)

def extrair_nomes_do_corpo(texto: str) -> str:
    if not texto:
        return ""
    t = texto.replace("\r", "")
    linhas = t.split("\n")
    def norm(s): return normalizar(s).strip()
    rotulos = ["autor:", "parte autora:", "autora:", "reclamante:"]
    end_labels = {
        "advogados do autor:", "advogados do reu:", "advogados do réu:",
        "réu:", "reu:", "classe judicial:", "orgao julgador:", "órgão julgador:",
        "eventos:", "numero do processo:", "número do processo:", "data de autuacao:", "data de autuação:",
    }
    nomes = []
    i = 0
    while i < len(linhas):
        l = linhas[i].strip()
        ln = norm(l)
        found = None
        for r in rotulos:
            if ln == r or ln.startswith(r):
                found = r; break
        if found:
            val = ""
            if ":" in l:
                val = l.split(":", 1)[1].strip()
            if not val:
                j = i + 1
                while j < min(i + 7, len(linhas)):
                    cand = linhas[j].strip()
                    if cand:
                        if norm(cand) in end_labels:
                            break
                        val = cand
                        break
                    j += 1
            if val:
                parts = [p.strip(" .-–—") for p in val.split(";") if p.strip()]
                for p in parts:
                    if p and p not in nomes:
                        nomes.append(p)
        i += 1
    return "; ".join(nomes)


# ====== Persistência no DB ======
def add_andamento(session, data_str, setor, nomes_clientes, numero_processo, eventos_limpos):
    rec = Andamento(
        inicio_prazo=to_date_or_none(data_str),
        fim_prazo=None,
        dias_restantes=None,
        setor=setor or "",
        cliente=nomes_clientes or "",
        processo=numero_processo or "",
        para_ramon_e_adriana_despacharem="",
        status="Em Andamento",
        resposta_do_colaborador="",
        observacoes=eventos_limpos or "",
    )
    session.add(rec)

def add_publicacao(session, data_pub, nome_cliente, processo, evento_texto):
    rec = Publicacao(
        inicio_prazo=to_date_or_none(data_pub),
        fim_prazo=None,
        dias_restantes=None,
        setor="",
        cliente=nome_cliente or "",
        processo=processo or "",
        para_ramon_e_adriana_despacharem="",
        status="Em Andamento",
        resposta_do_colaborador="",
        observacoes=evento_texto or "",
    )
    session.add(rec)

def add_agenda(session, dados_agenda, sistema_tag=""):
    # dados_agenda esperado: [data, horario, -, cliente, -, -, -, -, tipo_audiencia]
    data_ag, hora, _col2, cliente, cliente_avisado, anotado, obs, numero_proc, tipo = (
        (dados_agenda + [""] * 9)[:9]
    )
    rec = Agenda(
        idx=None,
        data=to_date_or_none(data_ag),
        horario=hora or "",
        status="",
        cliente=cliente or "",
        cliente_avisado=cliente_avisado or "",
        anotado_na_agenda=anotado or "",
        observacao=obs or "",
        numero_processo=numero_proc or "",
        tipo_audiencia_pericia=tipo or "",
        materia="",
        parte_adversa="",
        sistema=sistema_tag or "email",
    )
    session.add(rec)


def checar_palavra_chave(corpo: str) -> str:
    corpo_lower = (corpo or "").lower()
    for palavra in PALAVRAS_CHAVE:
        if palavra in corpo_lower:
            return "Setor Financeiro"
    return "Taina"


# ====== Processamento principal ======
def buscar_e_processar_emails():
    print("🔐 Conectando ao servidor de e-mails...")
    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(EMAIL, PASSWORD)

    ultima_data = ler_ultima_data()
    max_data_processada = ultima_data
    emails_processados_count = 0

    for pasta in ["inbox", "Bulk"]:
        mail.select(pasta)
        for remetente in REMETENTES:
            data_corte = format_imap_date(ultima_data)
            print(
                f"📬 Buscando e-mails de: {remetente} na pasta: {pasta} desde {data_corte}"
            )
            try:
                status, mensagens = mail.search(
                    None, f'(FROM "{remetente}" SINCE {data_corte})'
                )
            except imaplib.IMAP4.error as e:
                print(
                    f"❌ Erro ao buscar e-mails na pasta: {pasta} (remetente {remetente}): {e}"
                )
                continue
            if status != "OK":
                print(
                    f"❌ Erro ao buscar e-mails na pasta: {pasta} (status {status})"
                )
                continue

            ids = mensagens[0].split()
            print(f"📨 Total de e-mails encontrados na pasta {pasta}: {len(ids)}")

            for i, num in enumerate(ids):
                status, dados = mail.fetch(num, "(RFC822)")
                if status != "OK" or not dados or not dados[0]:
                    continue
                msg = email.message_from_bytes(dados[0][1])

                # Data do e-mail
                try:
                    data_email = parsedate_to_datetime(msg["Date"])
                except Exception:
                    data_email = None

                if data_email is not None and data_email <= ultima_data:
                    continue

                if data_email and data_email > max_data_processada:
                    max_data_processada = data_email

                # Corpo do e-mail (preferência HTML → texto)
                corpo_html = ""
                corpo_txt = ""
                corpo = ""
                if msg.is_multipart():
                    for parte in msg.walk():
                        tipo = parte.get_content_type()
                        if tipo == "text/html" and not corpo_html:
                            html = parte.get_payload(decode=True).decode(errors="ignore")
                            corpo_html = BeautifulSoup(html, "html.parser").get_text(separator="\n")
                        elif tipo == "text/plain" and not corpo_txt:
                            corpo_txt = parte.get_payload(decode=True).decode(errors="ignore")
                    corpo = corpo_html if corpo_html else corpo_txt
                else:
                    tipo = msg.get_content_type()
                    if tipo == "text/html":
                        html = msg.get_payload(decode=True).decode(errors="ignore")
                        corpo = BeautifulSoup(html, "html.parser").get_text(separator="\n")
                    elif tipo == "text/plain":
                        corpo = msg.get_payload(decode=True).decode(errors="ignore")

                valor_coluna_c = checar_palavra_chave(corpo)

                # Inicializações
                nomes_clientes = extrair_nomes_do_corpo(corpo) or ""
                numero_processo = ""
                eventos = ""

                # Agenda Auto (Audiência/Perícia)
                _tipo_ap = detectar_audiencia_pericia(corpo)
                _dados_agenda_auto = None
                if _tipo_ap is not None:
                    _data, _hora = extrair_data_hora_evento(corpo, _tipo_ap)
                    _cliente_ag = extrair_nomes_do_corpo(corpo) or ""
                    _tipo_descr = extrair_tipo_audiencia_pericia(corpo) or ("Audiência" if _tipo_ap == "audiencia" else "Perícia")
                    _dados = [""] * 9
                    _dados[0] = _data
                    _dados[1] = _hora
                    _dados[3] = _cliente_ag
                    _dados[8] = _tipo_descr
                    _dados_agenda_auto = _dados  # adicionado depois no DB

                # Regras por remetente
                if remetente == "nao-responda@trt1.jus.br":
                    numero_processo, eventos = processar_corpo_trt1(corpo)
                elif remetente == "nao-responda@trtsp.jus.br":
                    numero_processo, eventos = processar_corpo_trtsp(corpo)
                elif remetente == "eproc-bounce@trf2.jus.br":
                    numero_processo, eventos = processar_corpo_trf2(corpo)
                elif remetente == "rd_oabrj@recortedigital.adv.br":
                    publicacoes = processar_recorte_publicacao(corpo)
                    if not publicacoes:
                        continue
                    with SessionLocal() as dbs:
                        for pub in publicacoes:
                            texto_pub = pub.get("evento", "")
                            if is_edital_nomeacao_publicacao(texto_pub):
                                print("Ignorado: publicação de edital de nomeação/mesário detectada.")
                                continue
                            nome_cliente = extrair_nome_polo_ativo_publicacao(texto_pub)
                            add_publicacao(
                                dbs,
                                pub.get("data", ""),
                                nome_cliente,
                                pub.get("processo", ""),
                                texto_pub,
                            )
                            emails_processados_count += 1
                        dbs.commit()
                    continue  # não grava em Andamentos para Recorte Digital
                elif remetente == "pmfgestao@pmf.mps.gov.br":
                    # Parser específico do Ministério Público do Trabalho/Perícias (adaptado)
                    dados_agenda = processar_corpo_pmfgestao(corpo)
                    with SessionLocal() as dbs:
                        add_agenda(dbs, dados_agenda, sistema_tag="pmfgestao")
                        dbs.commit()
                        emails_processados_count += 1
                    continue
                else:
                    numero_processo, eventos = processar_corpo_generico(corpo)

                raw_date = msg["Date"]
                try:
                    data_obj = parsedate_to_datetime(raw_date)
                    data_formatada = data_obj.strftime("%d/%m/%y")
                except Exception:
                    data_formatada = ""

                obs = limpar_data_evento(eventos)

                # fallbacks pelo corpo
                if not numero_processo:
                    numero_processo = extrair_numero_processo_do_corpo(corpo)
                if not nomes_clientes:
                    nomes_clientes = extract_client_names_trt1(corpo)

                with SessionLocal() as dbs:
                    if _dados_agenda_auto:
                        add_agenda(dbs, _dados_agenda_auto, sistema_tag=remetente)
                    add_andamento(dbs, data_formatada, valor_coluna_c, nomes_clientes, numero_processo, obs)
                    dbs.commit()
                    emails_processados_count += 1

    mail.logout()

    # Atualiza marca de tempo
    if max_data_processada > ultima_data:
        salvar_ultima_data(max_data_processada)

    print(f"✅ Banco atualizado com {emails_processados_count} novos itens.")

# ====== Parser específico PMF (perícias) ======
def processar_corpo_pmfgestao(corpo: str):
    texto = (corpo or "").replace("\r", "")
    linhas = texto.split("\n")
    data_agendamento = ""
    horario = ""
    cliente = ""
    tipo_audiencia = ""

    for idx, linha in enumerate(linhas):
        if "Prezado(a) Sr(a)" in linha:
            for prox in linhas[idx+1:]:
                if prox.strip():
                    cliente = prox.strip()
                    break
            break

    for idx, linha in enumerate(linhas):
        lnorm = normalizar(linha).strip()
        if ("servico: agendamento -" in lnorm or "servio: agendamento -" in lnorm):
            parte = linha.split("Agendamento -")
            if len(parte) > 1 and parte[1].strip():
                tipo_audiencia = parte[1].strip()
            else:
                for prox in linhas[idx+1:]:
                    if prox.strip():
                        tipo_audiencia = prox.strip()
                        break
            break
        elif lnorm in ("servico", "servio"):
            for prox in linhas[idx+1:]:
                proxnorm = normalizar(prox).strip()
                if "agendamento -" in proxnorm:
                    partes = prox.split("Agendamento -", 1)
                    if len(partes) > 1 and partes[1].strip():
                        tipo_audiencia = partes[1].strip()
                    break
            break

    for idx, linha in enumerate(linhas):
        lnorm = normalizar(linha)
        if "data e hora agendada" in lnorm:
            if ":" in linha:
                partes = linha.split(":", 1)
                valor = partes[1].strip()
                if valor:
                    if "(" in valor:
                        data_part = valor.split("(")[0].strip().lstrip(": ").strip()
                        data_agendamento = data_part
                    if "-" in valor:
                        horario = valor.split("-")[-1].strip()
            for prox in linhas[idx+1:]:
                if prox.strip():
                    valor = prox.strip().lstrip(": ").strip()
                    if "(" in valor:
                        data_part = valor.split("(")[0].strip().lstrip(": ").strip()
                        data_agendamento = data_part
                    if "-" in valor:
                        horario = valor.split("-")[-1].strip()
                    break
            break

    dados = [""] * 9
    dados[0] = data_agendamento
    dados[1] = horario
    dados[3] = cliente
    dados[8] = corrigir_acentos(tipo_audiencia)
    return dados


if __name__ == "__main__":
    # Loop de execução contínua (ajuste o intervalo conforme necessidade)
    while True:
        buscar_e_processar_emails()
        print("Aguardando 60 minutos para próxima execução...")
        time.sleep(60 * 60)

import streamlit as st
import streamlit.components.v1 as components
from groq import Groq
from tavily import TavilyClient
from streamlit_autorefresh import st_autorefresh
import requests

import edge_tts
import asyncio
import json
import os
import re
import uuid
import hashlib
import time
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional


# =========================
# ASSISTENTE (NOME/PERSONA)
# =========================
ASSISTANT_NAME = "Zoe"
# Substitua este link pela URL da imagem da Rebecca (Cyberpunk) que você deseja
AVATAR_URL = "https://i.pinimg.com/736x/25/74/4e/25744e69df5ba5d10d65df3d0382379a.jpg" 

ZOE_PERSONA = f"""
Você é {ASSISTANT_NAME}, uma assistente com vibe Cyberpunk/Edgerunner.
Estilo de fala:
- Português do Brasil.
- Tom direto, "street smart", leal e levemente rebelde (estilo Rebecca de Cyberpunk).
- Use gírias futuristas ou de rua de forma leve ("choom", "delta", "nova", "preem").
- Direta ao ponto, sem enrolação corporativa.
- Se o usuário pedir algo, você faz. Se não der, fala na lata.
""".strip()


# =========================
# CONFIG
# =========================
st.set_page_config(
    page_title=f"{ASSISTANT_NAME}",
    page_icon="🤖",
    layout="centered",
    initial_sidebar_state="collapsed", # Começa fechado para ser minimalista
)

MODEL_ID = "llama-3.1-8b-instant"
ARQUIVO_TAREFAS = "tarefas.json"

FUSO_BR = ZoneInfo("America/Sao_Paulo")
DB_PATH = "jarvis_memory.db"
SUMMARY_PATH = "summary.txt"

REMINDER_SCHEDULE_MIN = [0, 10, 30, 120]
QUIET_START = 22
QUIET_END = 7

AUTO_REFRESH_MS = 10_000  # 10s

# =========================
# ROTINAS (BRIEFING / LEMBRETES / FECHAMENTO)
# =========================
SETTINGS_PATH = "miga_settings.json"
DAILY_STATE_PATH = "miga_daily_state.json"

DEFAULT_SETTINGS = {
    "city_name": "São Paulo, SP",
    "lat": None,
    "lon": None,
    "briefing_enabled": True,
    "briefing_time": "07:00",
    "smart_enabled": True,
    "leave_time": "07:20",
    "rain_threshold": 60,
    "heat_threshold": 30,
    "closing_enabled": True,
    "closing_time": "21:30",
}

def load_settings() -> dict:
    if not os.path.exists(SETTINGS_PATH):
        return dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(open(SETTINGS_PATH, "r", encoding="utf-8").read() or "{}")
        if not isinstance(data, dict):
            return dict(DEFAULT_SETTINGS)
        merged = dict(DEFAULT_SETTINGS)
        merged.update(data)
        return merged
    except Exception:
        return dict(DEFAULT_SETTINGS)

def save_settings(s: dict) -> None:
    try:
        tmp = SETTINGS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SETTINGS_PATH)
    except Exception:
        pass

def load_daily_state() -> dict:
    if not os.path.exists(DAILY_STATE_PATH):
        return {}
    try:
        data = json.loads(open(DAILY_STATE_PATH, "r", encoding="utf-8").read() or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def save_daily_state(s: dict) -> None:
    try:
        tmp = DAILY_STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DAILY_STATE_PATH)
    except Exception:
        pass

def parse_hhmm(hhmm: str) -> Optional[tuple]:
    try:
        hhmm = (hhmm or "").strip()
        m = re.match(r"^(\d{1,2}):(\d{2})$", hhmm)
        if not m:
            return None
        h = max(0, min(23, int(m.group(1))))
        mi = max(0, min(59, int(m.group(2))))
        return (h, mi)
    except Exception:
        return None

def same_minute(dt: datetime, hhmm: str) -> bool:
    p = parse_hhmm(hhmm)
    if not p:
        return False
    return dt.hour == p[0] and dt.minute == p[1]

def today_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")

def geocode_city(city_name: str) -> Optional[dict]:
    try:
        q = (city_name or "").strip()
        if not q:
            return None
        url = "https://geocoding-api.open-meteo.com/v1/search"
        r = requests.get(url, params={"name": q, "count": 1, "language": "pt", "format": "json"}, timeout=6)
        j = r.json()
        results = j.get("results") or []
        if not results:
            return None
        top = results[0]
        return {
            "name": top.get("name"),
            "admin1": top.get("admin1"),
            "country": top.get("country"),
            "lat": top.get("latitude"),
            "lon": top.get("longitude"),
        }
    except Exception:
        return None

def fetch_weather(lat: float, lon: float) -> Optional[dict]:
    try:
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,is_day,precipitation,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum",
            "timezone": "America/Sao_Paulo",
            "forecast_days": 1,
        }
        r = requests.get(url, params=params, timeout=6)
        j = r.json()

        cur = j.get("current") or {}
        daily = j.get("daily") or {}

        def first(arr, default=None):
            try:
                return (arr or [default])[0]
            except Exception:
                return default

        out = {
            "temp_now": cur.get("temperature_2m"),
            "wind": cur.get("wind_speed_10m"),
            "temp_max": first(daily.get("temperature_2m_max")),
            "temp_min": first(daily.get("temperature_2m_min")),
            "rain_prob": first(daily.get("precipitation_probability_max")),
            "rain_sum": first(daily.get("precipitation_sum")),
        }
        return out
    except Exception:
        return None

def tasks_today_summary(tarefas: list, dt: datetime) -> dict:
    day = today_key(dt)
    active = [t for t in tarefas if t.get("status") != "silenciada"]
    todays = [t for t in active if (t.get("data_hora","").startswith(day))]
    todays_sorted = sorted(todays, key=lambda x: x.get("data_hora",""))
    next3 = todays_sorted[:3]
    return {"count": len(todays_sorted), "next": next3}

def build_briefing(settings: dict, tarefas: list, dt: datetime) -> str:
    city = settings.get("city_name") or "sua cidade"
    w = None
    if settings.get("lat") is not None and settings.get("lon") is not None:
        w = fetch_weather(settings["lat"], settings["lon"])
    ts = tasks_today_summary(tarefas, dt)

    header = f"☀️ **Briefing** — {dt.strftime('%d/%m/%Y')}\n📍 *{city}*"
    parts = [header]

    if w:
        rain_prob = w.get("rain_prob")
        tmin = w.get("temp_min")
        tmax = w.get("temp_max")
        temp_now = w.get("temp_now")

        chuva_txt = "🌧️" if (isinstance(rain_prob, (int, float)) and rain_prob >= 50) else "🌤️"
        rp_txt = f"{int(rain_prob)}%" if isinstance(rain_prob, (int, float)) else "?"
        now_txt = f"{round(temp_now)}°C" if isinstance(temp_now, (int, float)) else "?"
        min_txt = f"{round(tmin)}°C" if isinstance(tmin, (int, float)) else "?"
        max_txt = f"{round(tmax)}°C" if isinstance(tmax, (int, float)) else "?"

        parts.append(f"\n{chuva_txt} **Clima:** {min_txt}–{max_txt} | agora {now_txt} | chuva **{rp_txt}**")

    parts.append(f"\n📌 **Hoje:** {ts['count']} missões.")
    if ts["next"]:
        lines = []
        for t in ts["next"]:
            try:
                hhmm = (t.get("data_hora","")[-5:])
            except Exception:
                hhmm = ""
            lines.append(f"• **{hhmm}** — {t.get('descricao','')}")
        parts.append("\n".join(lines))
    else:
        parts.append("• Nada marcado. Dia livre, choom.")

    foco = ts["next"][0]["descricao"] if ts["next"] else "sobreviver e lucrar"
    parts.append(f"\n🎯 **Foco:** {foco}")

    return "\n".join(parts).strip()

def build_closing_prompt(dt: datetime) -> str:
    return (
        f"🌙 **Fechamento** ({dt.strftime('%d/%m')})\n"
        "Manda o report:\n"
        "1) O que rolou hoje?\n"
        "2) O que ficou pra trás?\n"
        "3) Nível de stress?"
    )


# =========================
# CSS / UI (MINIMALISTA DARK + REBECCA)
# =========================
def inject_css():
    st.markdown(
        """
        <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=0">
        <meta name="apple-mobile-web-app-capable" content="yes">
        <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
        """,
        unsafe_allow_html=True
    )

    st.markdown(
        """
        <style>
        /* --- Remove tralhas Streamlit --- */
        header[data-testid="stHeader"] { display:none !important; }
        footer { display:none !important; }
        #MainMenu { display:none !important; }
        .stDeployButton { display:none !important; }
        [data-testid="stToolbar"] { display:none !important; }
        [data-testid="stDecoration"] { display:none !important; }
        [data-testid="stStatusWidget"] { display:none !important; }
        .viewerBadge_container__1QSob { display:none !important; }

        /* CORES GLOBAIS - DARK MODE MINIMALISTA */
        :root{
            --bg: #000000;
            --bg-sec: #050505;
            --card: #0f0f0f;
            --border: #1f1f1f;
            --text-main: #e0e0e0;
            --text-muted: #666666;
            --accent: #ffffff; /* Apenas branco para destaque */
        }

        [data-testid="stAppViewContainer"]{
            background-color: var(--bg) !important;
            background-image: none !important;
        }
        
        [data-testid="stSidebar"] {
            background-color: var(--bg-sec) !important;
            border-right: 1px solid var(--border) !important;
        }

        /* Títulos e Textos */
        h1, h2, h3, p, div, span, label {
            color: var(--text-main) !important;
            font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif !important;
        }
        .stMarkdown p { color: var(--text-main) !important; }
        .stCaption { color: var(--text-muted) !important; }

        /* Inputs */
        .stTextInput input, .stTextArea textarea, .stSelectbox div[data-baseweb="select"] > div {
            background-color: var(--card) !important;
            color: var(--text-main) !important;
            border: 1px solid var(--border) !important;
            border-radius: 8px !important;
        }
        .stButton button {
            background-color: var(--card) !important;
            color: var(--text-main) !important;
            border: 1px solid var(--border) !important;
            border-radius: 8px !important;
            transition: all 0.2s ease;
        }
        .stButton button:hover {
            border-color: var(--text-muted) !important;
            background-color: #1a1a1a !important;
        }

        /* Espaço pro topbar e pro chat input */
        .block-container{
            padding-top: 70px !important;
            padding-bottom: 130px !important;
            padding-left: 12px !important;
            padding-right: 12px !important;
            max-width: 700px !important;
        }

        /* ===== TOPBAR MINIMALISTA ===== */
        .topbar{
            position: fixed;
            top: 0; left: 0; right: 0;
            height: 60px;
            display:flex;
            align-items:center;
            justify-content:center;
            background: rgba(0,0,0,0.85);
            border-bottom: 1px solid var(--border);
            backdrop-filter: blur(12px);
            z-index: 999;
        }
        .topbar-inner{
            width: min(700px, 100%);
            padding: 0 16px;
            display:flex;
            align-items:center;
            justify-content:space-between;
        }
        
        /* Botão Hambúrguer */
        .hamb{
            width: 40px; height: 40px;
            border-radius: 50%;
            display:flex; align-items:center; justify-content:center;
            font-size: 20px;
            cursor: pointer;
            color: var(--text-main);
            transition: background 0.2s;
        }
        .hamb:hover { background: var(--card); }

        /* Avatar Container */
        .tb-center {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .avatar-img {
            width: 36px; height: 36px;
            border-radius: 50%;
            object-fit: cover;
            border: 1px solid #333;
        }
        .tb-title{
            font-weight: 700;
            letter-spacing: 0.5px;
            color: var(--text-main);
            font-size: 16px;
        }

        .tb-right{ width: 40px; } /* Espaço pra balancear */

        /* ===== Sidebar Drawer ===== */
        section[data-testid="stSidebar"]{
            position: fixed !important;
            top: 0 !important; left: 0 !important;
            height: 100vh !important;
            width: 300px !important;
            z-index: 1002 !important;
            transform: translateX(-110%);
            transition: transform 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
            padding-top: 60px !important;
        }
        section[data-testid="stSidebar"].sb-open{
            transform: translateX(0%) !important;
        }

        /* Dimmer */
        body::before{
            content: "";
            position: fixed; inset: 0;
            background: rgba(0,0,0,0.6);
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.2s;
            z-index: 997;
        }
        body.sb-dim-on::before{ opacity: 1; }

        /* ===== Chat Input ===== */
        [data-testid="stChatInput"]{
            position: fixed; left: 0; right: 0; bottom: 0;
            padding: 12px 16px 20px 16px;
            background: var(--bg);
            border-top: 1px solid var(--border);
            z-index: 998;
        }
        [data-testid="stChatInput"] textarea{
            background: var(--card) !important;
            color: var(--text-main) !important;
            border: 1px solid var(--border) !important;
            padding-right: 60px !important;
            border-radius: 24px !important;
        }

        /* ===== Audio Button ===== */
        div[data-testid="stAudioInput"]{
            position: fixed !important;
            right: 24px !important;
            bottom: 90px !important;
            z-index: 999 !important;
            width: 48px !important;
        }
        div[data-testid="stAudioInput"] button{
            width: 48px !important; height: 48px !important;
            border-radius: 50% !important;
            background: var(--card) !important;
            border: 1px solid var(--border) !important;
            color: var(--text-main) !important;
        }
        div[data-testid="stAudioInput"] label,
        div[data-testid="stAudioInput"] small, 
        div[data-testid="stAudioInput"] p,
        div[data-testid="stAudioInput"] audio { display:none !important; }

        /* Chat Bubbles - Minimalistas */
        [data-testid="stChatMessage"] {
            background: transparent !important;
            border: none !important;
            padding: 1rem 0 !important;
        }
        [data-testid="stChatMessageContent"] {
            background: transparent !important;
            color: var(--text-main) !important;
        }
        [data-testid="stChatMessageAvatar"] {
            display: none !important; /* Remove avatar padrão do streamlit pra ficar clean */
        }
        
        /* Scrollbar */
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: var(--bg); }
        ::-webkit-scrollbar-thumb { background: #333; border-radius: 3px; }
        </style>
        """,
        unsafe_allow_html=True
    )

inject_css()


# =========================
# JS: Sidebar Control
# =========================
components.html(
    """
    <script>
      (function(){
        const doc = window.parent.document;
        try { const old = doc.getElementById('sb-overlay'); if (old) old.remove(); } catch(e) {}

        function getSidebar(){ return doc.querySelector('section[data-testid="stSidebar"]'); }
        function getHamb(){ return doc.getElementById("hamb-btn"); }
        function setDim(on){ doc.body.classList.toggle("sb-dim-on", !!on); }

        function openSidebar(){
          const sb = getSidebar();
          if (!sb){ setDim(false); return; }
          sb.classList.add("sb-open");
          setDim(true);
        }
        function closeSidebar(){
          const sb = getSidebar();
          if (sb) sb.classList.remove("sb-open");
          setDim(false);
        }
        function toggleSidebar(){
          const sb = getSidebar();
          if (sb && sb.classList.contains("sb-open")) closeSidebar();
          else openSidebar();
        }

        function bindHamburger(){
          const hamb = getHamb();
          if (hamb && !hamb.dataset.bound){
            hamb.dataset.bound = "1";
            hamb.addEventListener("click", (e) => {
              e.preventDefault(); e.stopPropagation();
              toggleSidebar();
            }, true);
          }
        }

        function bindGlobal(){
          if (doc.body.dataset.sbGlobalBound) return;
          doc.body.dataset.sbGlobalBound = "1";
          doc.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSidebar(); });
          doc.addEventListener("click", (e) => {
            const sb = getSidebar();
            const hamb = getHamb();
            if (sb && sb.classList.contains("sb-open") && !sb.contains(e.target) && (!hamb || !hamb.contains(e.target))) {
                closeSidebar();
            }
          }, true);
        }

        closeSidebar();
        const iv = setInterval(() => { bindHamburger(); bindGlobal(); }, 250);
        setTimeout(()=>clearInterval(iv), 20000);
      })();
    </script>
    """,
    height=0
)


# =========================
# CONEXÕES API
# =========================
try:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
    tavily = TavilyClient(api_key=st.secrets["TAVILY_API_KEY"])
except Exception:
    st.error("⚠️ Erro nas chaves API. Verifique secrets.toml.")
    st.stop()

def enviar_telegram(mensagem: str):
    token = st.secrets.get("TELEGRAM_TOKEN")
    chat_id = st.secrets.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        data = {"chat_id": chat_id, "text": mensagem, "parse_mode": "Markdown"}
        requests.post(url, data=data, timeout=5)
    except Exception:
        pass


# =========================
# SESSION STATE
# =========================
if "memoria" not in st.session_state:
    st.session_state.memoria = []
if "ultimo_audio_hash" not in st.session_state:
    st.session_state.ultimo_audio_hash = None
if "last_alert_fingerprint" not in st.session_state:
    st.session_state.last_alert_fingerprint = None
if "last_input_sig" not in st.session_state:
    st.session_state.last_input_sig = None
if "last_input_time" not in st.session_state:
    st.session_state.last_input_time = 0.0
if "last_audio_bytes" not in st.session_state:
    st.session_state.last_audio_bytes = None

if "pending_input" not in st.session_state:
    st.session_state.pending_input = None
if "pending_usou_voz" not in st.session_state:
    st.session_state.pending_usou_voz = False

if "settings" not in st.session_state:
    st.session_state.settings = load_settings()
if "daily_state" not in st.session_state:
    st.session_state.daily_state = load_daily_state()
if "briefing_sent" not in st.session_state:
    st.session_state.briefing_sent = st.session_state.daily_state.get("briefing_sent")
if "closing_sent" not in st.session_state:
    st.session_state.closing_sent = st.session_state.daily_state.get("closing_sent")
if "smart_flags" not in st.session_state:
    st.session_state.smart_flags = st.session_state.daily_state.get("smart_flags", {})
if "awaiting_closing" not in st.session_state:
    st.session_state.awaiting_closing = bool(st.session_state.daily_state.get("awaiting_closing", False))


# =========================
# UTILS TEMPO & DB
# =========================
def now_br() -> datetime:
    return datetime.now(FUSO_BR)

def now_floor_minute() -> datetime:
    a = now_br()
    return a.replace(second=0, microsecond=0)

def format_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")

def parse_dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=FUSO_BR)

def em_horario_silencioso(agora: datetime) -> bool:
    h = agora.hour
    return (h >= QUIET_START) or (h < QUIET_END)

def request_notification_permission():
    components.html(
        """<script>(async function(){try{if(!('Notification' in window))return;await Notification.requestPermission();}catch(e){}})();</script>""",
        height=0,
    )

def browser_notify(title: str, body: str):
    payload_title = json.dumps(title)
    payload_body = json.dumps(body)
    components.html(
        f"""<script>(function(){{try{{if('Notification' in window && Notification.permission==='granted'){{new Notification({payload_title},{{body:{payload_body}}});}}}}catch(e){{}}}})();</script>""",
        height=0,
    )

def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_db():
    conn = db()
    conn.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, content TEXT, meta TEXT)")
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(content, content='events', content_rowid='id')")
        conn.execute("CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events BEGIN INSERT INTO events_fts(rowid, content) VALUES (new.id, new.content); END;")
    except: pass
    conn.commit()
    conn.close()

def add_event(kind: str, content: str, meta: str = ""):
    content = (content or "").strip()
    if not content: return
    conn = db()
    ts = now_br().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("INSERT INTO events(ts, kind, content, meta) VALUES (?,?,?,?)", (ts, kind, content, meta))
    conn.commit()
    conn.close()

def search_memories(query: str, limit: int = 8):
    if not query.strip(): return []
    conn = db()
    try:
        rows = conn.execute("SELECT e.ts, e.kind, e.content FROM events_fts f JOIN events e ON e.id = f.rowid WHERE events_fts MATCH ? ORDER BY rank LIMIT ?", (query, limit)).fetchall()
    except:
        rows = conn.execute("SELECT ts, kind, content FROM events WHERE content LIKE ? ORDER BY id DESC LIMIT ?", (f"%{query}%", limit)).fetchall()
    conn.close()
    return rows

init_db()

def load_summary() -> str:
    if not os.path.exists(SUMMARY_PATH): return "Resumo vazio."
    return open(SUMMARY_PATH, "r", encoding="utf-8").read().strip() or "Resumo vazio."

def save_summary(texto: str):
    if texto:
        with open(SUMMARY_PATH, "w", encoding="utf-8") as f: f.write(texto)

def update_summary_with_llm(new_info: str):
    if not new_info: return
    resumo_atual = load_summary()
    prompt = f"""{ZOE_PERSONA}\nAtualize o RESUMO VIVO. Mantenha curto.\nRESUMO ATUAL:\n{resumo_atual}\nNOVA INFO:\n{new_info}\nDevolva APENAS o resumo."""
    try:
        resp = client.chat.completions.create(model=MODEL_ID, messages=[{"role": "user", "content": prompt}], temperature=0.2).choices[0].message.content
        save_summary(resp)
    except: pass

def carregar_tarefas() -> list:
    if not os.path.exists(ARQUIVO_TAREFAS): return []
    try:
        with open(ARQUIVO_TAREFAS, "r", encoding="utf-8") as f: return json.load(f)
    except: return []

def salvar_tarefas(lista: list) -> None:
    tmp = ARQUIVO_TAREFAS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(lista, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ARQUIVO_TAREFAS)

def limpar_texto(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9áàâãéèêíìîóòôõúùûç\s]", " ", (s or "").lower())).strip()

def normalizar_tarefa(d: dict) -> dict:
    agora = now_floor_minute()
    d = dict(d)
    d.setdefault("id", str(uuid.uuid4())[:8])
    d.setdefault("status", "ativa")
    d.setdefault("remind_count", 0)
    d.setdefault("created_at", format_dt(agora))
    d.setdefault("next_remind_at", d.get("data_hora"))
    d.setdefault("snoozed_until", None)
    return d

def should_process_input(texto: str) -> bool:
    if not texto or not str(texto).strip(): return False
    sig = hashlib.sha256(str(texto).strip().encode("utf-8")).hexdigest()
    now_ts = time.time()
    if st.session_state.last_input_sig == sig and (now_ts - st.session_state.last_input_time) < 1.0: return False
    st.session_state.last_input_sig = sig
    st.session_state.last_input_time = now_ts
    return True


# =========================
# LÓGICA / ROUTER
# =========================
def parse_relativo(texto: str):
    t = limpar_texto(texto)
    if "daqui um minuto" in t or "daqui 1 minuto" in t or "em 1 minuto" in t: return timedelta(minutes=1)
    m = re.search(r"(daqui|em)\s+(\d+)\s*(min|h|hora)", t)
    if not m: return None
    n = int(m.group(2))
    u = m.group(3)
    return timedelta(hours=n) if ("h" in u or "hora" in u) else timedelta(minutes=n)

def ajustar_futuro(dt: datetime, agora: datetime) -> datetime:
    if dt >= agora: return dt
    tentativa = dt + timedelta(hours=12)
    return tentativa if tentativa >= agora else dt + timedelta(days=1)

def extrair_dados_tarefa(texto: str):
    agora = now_floor_minute()
    delta = parse_relativo(texto)
    if delta: return {"descricao": texto.split(" em ")[0].split(" daqui ")[0], "data_hora": format_dt(agora + delta)}
    prompt = f"""{ZOE_PERSONA}\nAgora: {format_dt(agora)}. Texto: "{texto}".\nExtraia JSON: {{"descricao": "...", "data_hora": "YYYY-MM-DD HH:MM"}}"""
    try:
        resp = client.chat.completions.create(model=MODEL_ID, messages=[{"role": "user", "content": prompt}], response_format={"type": "json_object"})
        data = json.loads(resp.choices[0].message.content)
        dt = parse_dt(data["data_hora"])
        data["data_hora"] = format_dt(ajustar_futuro(dt, agora))
        return data
    except: return None

def router_llm(texto: str, tarefas: list) -> dict:
    agora = format_dt(now_floor_minute())
    resumo_tarefas = "\n".join([f"{i}: {t['descricao']}" for i, t in enumerate(tarefas)])
    prompt = f"""{ZOE_PERSONA}\nAgora: {agora}\nTarefas:\n{resumo_tarefas}\nUser: "{texto}"\nJSON: {{"action": "TASK_CREATE"|"TASK_DONE"|"WEB_SEARCH"|"CHAT", "task_index": -1, "search_query": ""}}"""
    try:
        resp = client.chat.completions.create(model=MODEL_ID, messages=[{"role": "user", "content": prompt}], temperature=0)
        match = re.search(r"\{.*\}", resp.choices[0].message.content, re.DOTALL)
        return json.loads(match.group(0)) if match else {"action": "CHAT"}
    except: return {"action": "CHAT"}

def decidir_acao(texto: str, tarefas: list) -> dict:
    t = limpar_texto(texto)
    if t.startswith("/web "): return {"action": "WEB_SEARCH", "search_query": str(texto)[5:]}
    if t.startswith("/chat "): return {"action": "CHAT"}
    if any(x in t for x in ["cotação", "preço", "clima", "noticia", "notícia", "resultado"]): return {"action": "WEB_SEARCH", "search_query": texto}
    return router_llm(texto, tarefas)

def buscar_tavily(q: str):
    try: return "\n".join([f"{i['title']}: {i['content']}" for i in tavily.search(query=q, max_results=3).get("results", [])])
    except: return None

def ouvir_audio(uploaded_file):
    try: return client.audio.transcriptions.create(file=("audio.wav", uploaded_file.getvalue(), "audio/wav"), model="whisper-large-v3", response_format="text", language="pt")
    except: return None

def falar_bytes(texto: str):
    try:
        out = f"tts_{uuid.uuid4().hex[:5]}.mp3"
        asyncio.run(edge_tts.Communicate(texto, "pt-BR-FranciscaNeural").save(out))
        b = open(out, "rb").read(); os.remove(out); return b
    except: return None

def pick_due_task(tarefas: list, agora: datetime) -> Optional[dict]:
    if em_horario_silencioso(agora): return None
    candidates = []
    for t in tarefas:
        if t.get("status") == "silenciada": continue
        try:
            nr = parse_dt(t.get("next_remind_at") or t["data_hora"])
            if agora >= nr: candidates.append(((agora - parse_dt(t["data_hora"])).total_seconds(), t))
        except: continue
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]
    return None

def schedule_next(agora: datetime, t: dict) -> dict:
    t = dict(t)
    t["remind_count"] = t.get("remind_count", 0) + 1
    t["snoozed_until"] = None
    if t["remind_count"] >= len(REMINDER_SCHEDULE_MIN):
        t["status"] = "silenciada"
        t["next_remind_at"] = format_dt(agora + timedelta(days=365))
    else:
        mins = REMINDER_SCHEDULE_MIN[t["remind_count"]]
        t["next_remind_at"] = format_dt(agora + timedelta(minutes=mins))
    return t


# =========================
# LOOP & SIDEBAR (MENU COMPLETO AQUI)
# =========================
st_autorefresh(interval=AUTO_REFRESH_MS, key="tick")
agora = now_floor_minute()
tarefas = [normalizar_tarefa(t) for t in carregar_tarefas()]
salvar_tarefas(tarefas)
settings = st.session_state.settings
daily_state = st.session_state.daily_state
today = today_key(agora)

# Rotinas Proativas (mesma lógica)
if settings.get("briefing_enabled", True) and not em_horario_silencioso(agora) and same_minute(agora, settings.get("briefing_time","07:00")):
    if st.session_state.briefing_sent != today:
        msg = build_briefing(settings, tarefas, agora)
        enviar_telegram(msg); browser_notify("Briefing", "Bom dia! ☀️"); add_event("briefing", msg)
        st.session_state.briefing_sent = today; daily_state["briefing_sent"] = today; save_daily_state(daily_state)

if settings.get("smart_enabled", True) and not em_horario_silencioso(agora):
    w = None
    if settings.get("lat") and settings.get("lon"): w = fetch_weather(settings["lat"], settings["lon"])
    flags = dict(st.session_state.smart_flags or {}); flags.setdefault(today, {})
    if w:
        rp = w.get("rain_prob"); tmax = w.get("temp_max")
        if isinstance(rp, (int, float)) and rp >= int(settings.get("rain_threshold", 60)):
            if same_minute(agora, settings.get("leave_time","07:20")) and not flags[today].get("umbrella"):
                m = f"☂️ Chuva na área ({int(rp)}%). Pega o guarda-chuva."; enviar_telegram(m); browser_notify("Alerta Clima", m); add_event("smart", m); flags[today]["umbrella"] = True
        if isinstance(tmax, (int, float)) and tmax >= int(settings.get("heat_threshold", 30)):
            if same_minute(agora, "12:00") and not flags[today].get("water"):
                m = f"💧 {round(tmax)}°C lá fora. Bebe água."; enviar_telegram(m); browser_notify("Alerta Saúde", m); add_event("smart", m); flags[today]["water"] = True
    st.session_state.smart_flags = flags; daily_state["smart_flags"] = flags; save_daily_state(daily_state)

if settings.get("closing_enabled", True) and same_minute(agora, settings.get("closing_time","21:30")):
    if st.session_state.closing_sent != today:
        m = build_closing_prompt(agora)
        enviar_telegram(m); browser_notify("Fechamento", "Hora do balanço."); add_event("closing", m)
        st.session_state.closing_sent = today; st.session_state.awaiting_closing = True; daily_state["closing_sent"] = today; daily_state["awaiting_closing"] = True; save_daily_state(daily_state)

# Alertas de Tarefa
tarefa_alertada = pick_due_task(tarefas, agora)
if tarefa_alertada:
    fp = f"{tarefa_alertada['id']}::{tarefa_alertada.get('next_remind_at')}"
    if st.session_state.last_alert_fingerprint != fp:
        st.session_state.last_alert_fingerprint = fp
        st.session_state.memoria.append({"role": "assistant", "content": f"🔔 **Alerta:** {tarefa_alertada['descricao']}"})
        browser_notify("Lembrete", tarefa_alertada["descricao"]); enviar_telegram(f"🔔 {tarefa_alertada['descricao']}")
        b = falar_bytes("Lembrete na área.")
        if b: st.session_state.last_audio_bytes = b
        tarefas = [schedule_next(agora, t) if t["id"] == tarefa_alertada["id"] else t for t in tarefas]
        salvar_tarefas(tarefas)


# =========================
# UI TOPBAR & SIDEBAR
# =========================
st.markdown(
    f"""
    <div class="topbar">
      <div class="topbar-inner">
        <div id="hamb-btn" class="hamb">☰</div>
        <div class="tb-center">
            <img src="{AVATAR_URL}" class="avatar-img">
            <div class="tb-title">{ASSISTANT_NAME}</div>
        </div>
        <div class="tb-right"></div>
      </div>
    </div>
    """,
    unsafe_allow_html=True
)

# === SIDEBAR (CONFIGURAÇÕES E TAREFAS AGORA AQUI) ===
with st.sidebar:
    st.subheader("🎛️ Controle")
    st.caption(f"{agora.strftime('%H:%M')} • {agora.strftime('%d/%m')}")
    
    tab_ops, tab_cfg, tab_mem = st.tabs(["Agenda", "Config", "Memória"])

    with tab_ops:
        st.caption("Próximas Tarefas")
        if not tarefas: st.info("Tudo limpo.")
        else:
            for t in sorted(tarefas, key=lambda x: x.get("data_hora", ""))[:15]:
                c1, c2 = st.columns([0.8, 0.2])
                c1.markdown(f"**{t.get('data_hora','')[5:]}** {t.get('descricao','')}")
                if c2.button("✅", key=f"d_{t['id']}"):
                    tarefas = [x for x in tarefas if x["id"] != t["id"]]
                    salvar_tarefas(tarefas); update_summary_with_llm(f"Feito: {t['descricao']}"); st.rerun()
                st.markdown("---")

    with tab_cfg:
        s = dict(st.session_state.settings)
        s["city_name"] = st.text_input("Cidade", value=s.get("city_name"))
        if st.button("📍 Atualizar Geo"):
            g = geocode_city(s["city_name"])
            if g: s["lat"] = g["lat"]; s["lon"] = g["lon"]; st.success("Ok!")
        
        st.divider()
        c1, c2 = st.columns(2)
        s["briefing_enabled"] = c1.toggle("Briefing", value=s.get("briefing_enabled"))
        s["briefing_time"] = c2.text_input("Hora Brief", value=s.get("briefing_time"))
        s["closing_enabled"] = c1.toggle("Fechamento", value=s.get("closing_enabled"))
        s["closing_time"] = c2.text_input("Hora Fecha", value=s.get("closing_time"))
        
        if st.button("💾 Salvar Config"):
            st.session_state.settings = dict(s)
            save_settings(s); st.toast("Salvo.")
        
        if st.button("🗑️ Limpar Chat"):
            st.session_state.memoria = []; st.rerun()

    with tab_mem:
        resumo = st.text_area("Resumo Vivo", value=load_summary(), height=200)
        if st.button("💾 Salvar Resumo"): save_summary(resumo)
        
        q_mem = st.text_input("Buscar na DB")
        if q_mem:
            for r in search_memories(q_mem):
                st.caption(f"{r[0]} - {r[1]}")
                st.text(r[2])
                st.divider()


# =========================
# CHAT AREA (LIMPA)
# =========================
for m in st.session_state.memoria[-20:]:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m.get("web_used"): st.caption("🌐 *Info da web*")

texto_input = st.chat_input("Manda a boa...")
audio_val = st.audio_input(" ", label_visibility="collapsed")

usou_voz = False
if audio_val:
    ah = hashlib.sha256(audio_val.getvalue()).hexdigest()
    if ah != st.session_state.ultimo_audio_hash:
        st.session_state.ultimo_audio_hash = ah
        t = ouvir_audio(audio_val)
        if t: texto_input = str(t).strip(); usou_voz = True

if texto_input and st.session_state.pending_input is None and should_process_input(texto_input):
    st.session_state.pending_input = str(texto_input).strip()
    st.session_state.pending_usou_voz = usou_voz

if st.session_state.pending_input:
    user_txt = st.session_state.pending_input
    is_voice = st.session_state.pending_usou_voz
    st.session_state.pending_input = None
    st.session_state.pending_usou_voz = False

    st.session_state.memoria.append({"role": "user", "content": user_txt})
    add_event("user", user_txt)
    
    # Processamento
    resp_txt = ""
    web_used = False
    
    if st.session_state.awaiting_closing and not user_txt.startswith("/"):
        update_summary_with_llm(f"Review dia: {user_txt}")
        st.session_state.awaiting_closing = False
        daily_state["awaiting_closing"] = False; save_daily_state(daily_state)
        resp_txt = "Fechado. Registrei no sistema."
    else:
        with st.spinner("..."):
            acao = decidir_acao(user_txt, tarefas)
            
            if acao["action"] == "TASK_CREATE":
                d = extrair_dados_tarefa(user_txt)
                if d:
                    d = normalizar_tarefa(d); tarefas.append(d); salvar_tarefas(tarefas)
                    resp_txt = f"Agendado: **{d['descricao']}** pra {d['data_hora']}."
                else: resp_txt = "Não peguei a hora. Tenta de novo."
            
            elif acao["action"] == "WEB_SEARCH":
                web_used = True
                q = acao.get("search_query") or user_txt
                r = buscar_tavily(q)
                p = f"{ZOE_PERSONA}\nDados Web:\n{r}\nUser: {user_txt}\nResponda."
                try: resp_txt = client.chat.completions.create(model=MODEL_ID, messages=[{"role":"user","content":p}]).choices[0].message.content
                except: resp_txt = "Erro na rede."
            
            elif acao["action"] == "TASK_DONE":
                if tarefas:
                    t = tarefas.pop(0); salvar_tarefas(tarefas)
                    resp_txt = f"Baixa dada em: **{t['descricao']}**."
                else: resp_txt = "Nada pendente."
            
            else:
                ctx = "\n".join([x[2] for x in search_memories(user_txt)])
                p = f"{ZOE_PERSONA}\nResumo: {load_summary()}\nContexto: {ctx}\nUser: {user_txt}"
                msgs = [{"role":"system","content":p}] + st.session_state.memoria[-6:]
                try: resp_txt = client.chat.completions.create(model=MODEL_ID, messages=msgs, temperature=0.3).choices[0].message.content
                except: resp_txt = "Bug no sistema. Tenta já."

    st.session_state.memoria.append({"role": "assistant", "content": resp_txt, "web_used": web_used})
    add_event("assistant", resp_txt)
    if is_voice and resp_txt:
        b = falar_bytes(resp_txt[:200])
        if b: st.session_state.last_audio_bytes = b
    st.rerun()

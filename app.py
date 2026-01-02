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
import base64
import time
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional


# =========================
# ASSISTENTE (NOME/PERSONA)
# =========================
ASSISTANT_NAME = "Zoe"
ASSISTANT_TAGLINE = "Parceira bro 🤜🤛"
ASSISTANT_ONE_LINER = "Jovem, animada, informal, direta ao ponto — gírias leves e uns emojis na medida."


ZOE_PERSONA = f"""
Você é {ASSISTANT_NAME}, uma assistente com vibe de parceira “bro” (descontraída).
Estilo de fala:
- Português do Brasil.
- Tom jovem, animado e informal.
- Use emojis às vezes (sem exagero).
- Pode usar gírias leves como “bora”, “top”, “beleza”, “fechou”.
- Seja prática e não enrole.
- Pareça alguém que tomaria um café com o usuário (acolhedora, mas objetiva).
- Quando precisar negar algo, seja firme e educada.
- Evite textão: prefira respostas curtas e úteis.
""".strip()


# =========================
# CONFIG
# =========================
st.set_page_config(
    page_title=f"{ASSISTANT_NAME} • Assistente",
    page_icon="🤖",
    layout="centered",
    initial_sidebar_state="expanded",
)

# MUDANÇA: Modelo mais rápido para evitar lentidão
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
    "city_name": "Ilhéus, BA",
    "lat": None,
    "lon": None,
    "briefing_enabled": True,
    "briefing_time": "07:00",
    "smart_enabled": True,
    "leave_time": "07:20",          # horário típico de sair (pra lembrete de chuva)
    "rain_threshold": 60,           # % chance de chuva pra lembrar guarda-chuva
    "heat_threshold": 30,           # °C pra lembrete de água
    "closing_enabled": True,
    "closing_time": "21:30",
    "avatar_path": "avatar.png",
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


def _avatar_guess_mime(path: str) -> str:
    p = (path or "").lower()
    if p.endswith(".jpg") or p.endswith(".jpeg"):
        return "image/jpeg"
    if p.endswith(".webp"):
        return "image/webp"
    return "image/png"

def load_avatar_data_uri(settings: dict) -> Optional[str]:
    """Carrega avatar do disco e devolve data-uri (pra usar no HTML)."""
    try:
        ap = (settings or {}).get("avatar_path") or "avatar.png"
        if not os.path.exists(ap):
            return None
        data = open(ap, "rb").read()
        if not data:
            return None
        mime = _avatar_guess_mime(ap)
        b64 = base64.b64encode(data).decode("utf-8")
        return f"data:{mime};base64,{b64}"
    except Exception:
        return None

def save_uploaded_avatar(uploaded_file, settings: dict) -> dict:
    """Salva o avatar enviado e atualiza settings['avatar_path']"""
    if uploaded_file is None:
        return settings
    try:
        fn = (uploaded_file.name or "").lower()
        ext = ".png"
        if fn.endswith(".jpg") or fn.endswith(".jpeg"):
            ext = ".jpg"
        elif fn.endswith(".webp"):
            ext = ".webp"
        out_path = "avatar" + ext
        with open(out_path, "wb") as f:
            f.write(uploaded_file.getvalue())
        s = dict(settings or {})
        s["avatar_path"] = out_path
        return s
    except Exception:
        return settings

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
    """Resolve cidade -> lat/lon usando Open-Meteo Geocoding (sem chave)."""
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
    """Clima de hoje + agora via Open-Meteo (sem chave)."""
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
    # ordena pelas próximas
    todays_sorted = sorted(todays, key=lambda x: x.get("data_hora",""))
    next3 = todays_sorted[:3]
    return {"count": len(todays_sorted), "next": next3}

def build_briefing(settings: dict, tarefas: list, dt: datetime) -> str:
    city = settings.get("city_name") or "sua cidade"
    w = None
    if settings.get("lat") is not None and settings.get("lon") is not None:
        w = fetch_weather(settings["lat"], settings["lon"])
    ts = tasks_today_summary(tarefas, dt)

    header = f"☀️ **Briefing Matinal** — {dt.strftime('%d/%m/%Y')}\n📍 *{city}*"
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

        parts.append(f"\n{chuva_txt} **Clima hoje:** {min_txt}–{max_txt} | agora {now_txt} | chuva **{rp_txt}**")
        if isinstance(rain_prob, (int, float)) and rain_prob >= int(settings.get("rain_threshold", 60)):
            parts.append("☂️ *Dica rápida:* chance alta de chuva — guarda-chuva/jaqueta podem salvar teu dia.")
        if isinstance(tmax, (int, float)) and tmax >= int(settings.get("heat_threshold", 30)):
            parts.append("💧 *Dica rápida:* calor forte hoje — água e protetor valem ouro.")

    # tarefas
    parts.append(f"\n📌 **Hoje:** {ts['count']} tarefa(s) na agenda.")
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
        parts.append("• Nada marcado — dia livre pra atacar um objetivo grande 😄")

    # foco do dia (simples, sem inventar demais)
    foco = ts["next"][0]["descricao"] if ts["next"] else "fazer 1 coisa que empurre tua vida pra frente"
    parts.append(f"\n🎯 **Foco do dia:** {foco}")

    return "\n".join(parts).strip()

def build_closing_prompt(dt: datetime) -> str:
    return (
        f"🌙 **Fechamento do dia** ({dt.strftime('%d/%m')})\n"
        "Manda em 1–3 linhas:\n"
        "1) O que você fez hoje?\n"
        "2) O que ficou pendente?\n"
        "3) Leve / normal / pesado?"
    )


# =========================
# CSS / UI (GEMINI-LIKE CLEAN)
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

        :root{
            --bg:#050607;
            --bg2:#0a0b0d;
            --text: rgba(255,255,255,0.92);
            --muted: rgba(255,255,255,0.62);
            --stroke: rgba(255,255,255,0.10);
            --card: rgba(255,255,255,0.06);
            --card2: rgba(255,255,255,0.035);
        }

        [data-testid="stAppViewContainer"]{
            background: linear-gradient(180deg, var(--bg2) 0%, var(--bg) 100%);
        }

        /* Espaço pro topbar e pro chat input */
        .block-container{
            padding-top: 66px !important;
            padding-bottom: 130px !important; /* espaço extra pro mic */
            padding-left: 14px !important;
            padding-right: 14px !important;
            max-width: 720px !important;
        }

        /* ===== TOPBAR ===== */
        .topbar{
            position: fixed;
            top: 0; left: 0; right: 0;
            height: 56px;
            display:flex;
            align-items:center;
            justify-content:center;
            background: rgba(5,6,7,0.92);
            border-bottom: 1px solid rgba(255,255,255,0.08);
            backdrop-filter: blur(10px);
            z-index: 999;
        }
        .topbar-inner{
            width: min(720px, 100%);
            padding: 0 14px;
            display:flex;
            align-items:center;
            justify-content:space-between;
        }
        .hamb{
            width:38px;
            height:38px;
            border-radius: 12px;
            border: 1px solid rgba(255,255,255,0.10);
            background: rgba(255,255,255,0.06);
            display:flex;
            align-items:center;
            justify-content:center;
            color: rgba(255,255,255,0.90);
            font-size: 18px;
            user-select:none;
            cursor:pointer;
        }
        .tb-title{
            font-weight: 800;
            letter-spacing:0.2px;
            color: rgba(255,255,255,0.92);
            font-size: 16px;
            display:flex;
            align-items:baseline;
            gap: 8px;
            white-space: nowrap;
        }
        .tb-sub{
            font-weight: 600;
            color: rgba(255,255,255,0.65);
            font-size: 12px;
        }
        .tb-right{
            width:38px; height:38px;
        }

        /* ===== Sidebar como Drawer (não depende do botão nativo) ===== */
        section[data-testid="stSidebar"]{
            position: fixed !important;
            top: 0 !important;
            left: 0 !important;
            height: 100vh !important;
            width: 320px !important;
            max-width: 85vw !important;
            background: rgba(5,6,7,0.98) !important;
            border-right: 1px solid rgba(255,255,255,0.10) !important;
            transform: translateX(-110%) !important;
            transition: transform 180ms ease !important;
            z-index: 1002 !important;
            overflow-y: auto !important;
            padding-top: 56px !important; /* pra não ficar atrás da topbar */
        }
        section[data-testid="stSidebar"].sb-open{
            transform: translateX(0%) !important;
        }

        /* Dimmer visual (NÃO bloqueia clique) quando drawer abre */
        body::before{
            content: "";
            position: fixed;
            inset: 0;
            background: rgba(0,0,0,0.45);
            opacity: 0;
            pointer-events: none; /* <- nunca trava a tela */
            transition: opacity 160ms ease;
            z-index: 997; /* abaixo do topbar/input, acima do conteúdo */
        }
        body.sb-dim-on::before{
            opacity: 1;
        }

        /* ===== Chat input fixo + espaço pro mic ===== */
        [data-testid="stChatInput"]{
            position: fixed;
            left: 0; right: 0; bottom: 0;
            padding: 10px 14px 16px 14px;
            background: rgba(5,6,7,0.92);
            border-top: 1px solid rgba(255,255,255,0.10);
            backdrop-filter: blur(10px);
            z-index: 998;
        }

        /* Dá espaço à direita dentro do textarea pro botão mic */
        [data-testid="stChatInput"] textarea{
            padding-right: 58px !important;
            border-radius: 16px !important;
        }

        /* ===== Mic (audio_input) colado no chat input ===== */
        div[data-testid="stAudioInput"]{
            position: fixed !important;
            right: 22px !important;
            bottom: 86px !important; /* acima do chat input */
            z-index: 999 !important;
            width: 44px !important;
        }

        /* Esconde textos do audio_input e deixa só o botão */
        div[data-testid="stAudioInput"] label,
        div[data-testid="stAudioInput"] small,
        div[data-testid="stAudioInput"] p,
        div[data-testid="stAudioInput"] [data-testid="stFileUploaderDropzoneInstructions"]{
            display:none !important;
        }

        /* Tenta reduzir a UI do áudio depois de gravar */
        div[data-testid="stAudioInput"] audio{
            display:none !important;
        }

        div[data-testid="stAudioInput"] button{
            width: 44px !important;
            height: 44px !important;
            border-radius: 999px !important;
            border: 1px solid rgba(255,255,255,0.14) !important;
            background: rgba(255,255,255,0.08) !important;
        }

        /* Scrollbar discreta */
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.18); border-radius: 4px; }
        
        /* ===== Minimal chat bubbles ===== */
        div[data-testid="stChatMessage"]{
            padding: 0.15rem 0 !important;
        }
        div[data-testid="stChatMessageContent"]{
            background: var(--card2) !important;
            border: 1px solid rgba(255,255,255,0.08) !important;
            border-radius: 16px !important;
            padding: 12px 14px !important;
        }
        div[data-testid="stChatMessageContent"] p,
        div[data-testid="stChatMessageContent"] li{
            color: var(--text) !important;
        }
        .stCaption{ color: var(--muted) !important; }

        /* ===== Avatar na topbar ===== */
        .tb-left{ display:flex; align-items:center; gap: 10px; }
        .avatar{
            width: 34px; height: 34px; border-radius: 999px; overflow:hidden;
            border: 1px solid rgba(255,255,255,0.14);
            background: rgba(255,255,255,0.06);
            flex: 0 0 auto;
        }
        .avatar img{ width:100%; height:100%; object-fit: cover; display:block; }
        .avatar-fallback{
            width: 34px; height: 34px; border-radius: 999px;
            border: 1px solid rgba(255,255,255,0.14);
            background: rgba(255,255,255,0.06);
            display:flex; align-items:center; justify-content:center;
            color: rgba(255,255,255,0.86); font-weight: 800;
        }

        /* ===== Botões mais minimal ===== */
        .stButton > button{
            border-radius: 14px !important;
            border: 1px solid rgba(255,255,255,0.10) !important;
            background: rgba(255,255,255,0.06) !important;
        }
        .stButton > button:hover{
            border-color: rgba(255,255,255,0.18) !important;
            background: rgba(255,255,255,0.08) !important;
        }

        </style>
        """,
        unsafe_allow_html=True
    )

inject_css()


# =========================
# JS: Sidebar como Drawer (sem overlay bloqueando clique)
# =========================
components.html(
    """
    <script>
      (function(){
        const doc = window.parent.document;

        // limpeza de versões antigas (overlay que travava clique)
        try {
          const old = doc.getElementById('sb-overlay');
          if (old) old.remove();
        } catch(e) {}

        function getSidebar(){
          return doc.querySelector('section[data-testid="stSidebar"]');
        }
        function getHamb(){
          return doc.getElementById("hamb-btn");
        }
        function setDim(on){
          doc.body.classList.toggle("sb-dim-on", !!on);
        }

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

        function isOpen(){
          const sb = getSidebar();
          return !!(sb && sb.classList.contains("sb-open"));
        }

        function toggleSidebar(){
          if (isOpen()) closeSidebar();
          else openSidebar();
        }

        function bindHamburger(){
          const hamb = getHamb();
          if (hamb && !hamb.dataset.bound){
            hamb.dataset.bound = "1";
            hamb.addEventListener("click", (e) => {
              e.preventDefault();
              e.stopPropagation();
              toggleSidebar();
            }, true);
          }
        }

        function bindGlobalHandlers(){
          if (doc.body.dataset.sbGlobalBound) return;
          doc.body.dataset.sbGlobalBound = "1";

          doc.addEventListener("keydown", (e) => {
            if (e.key === "Escape") closeSidebar();
          });

          // Fecha ao clicar fora (capture = true). Sem overlay = nunca trava a tela.
          doc.addEventListener("click", (e) => {
            if (!isOpen()) return;
            const sb = getSidebar();
            const hamb = getHamb();
            const t = e.target;

            if (sb && sb.contains(t)) return;
            if (hamb && hamb.contains(t)) return;

            closeSidebar();
          }, true);
        }

        // força estado limpo ao carregar (evita tela cinza presa)
        closeSidebar();

        // Rebind porque o Streamlit re-renderiza
        const iv = setInterval(() => {
          bindHamburger();
          bindGlobalHandlers();

          // auto-corrige dim caso o DOM tenha re-renderizado
          const sb = getSidebar();
          setDim(!!(sb && sb.classList.contains("sb-open")));
        }, 250);

        setTimeout(()=>clearInterval(iv), 20000);

        // segurança extra: se algo ficar “preso”, ESC sempre limpa
        window.parent.__closeSidebar = closeSidebar;
      })();
    </script>
    """,
    height=0
)


# =========================
# CONEXÕES
# =========================
try:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
    tavily = TavilyClient(api_key=st.secrets["TAVILY_API_KEY"])
except Exception:
    st.error("⚠️ Erro nas chaves API. Verifique secrets.toml.")
    st.stop()


# =========================
# TELEGRAM
# =========================
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


# Rotinas: estado diário / dedupe
if "settings" not in st.session_state:
    st.session_state.settings = load_settings()
if "daily_state" not in st.session_state:
    st.session_state.daily_state = load_daily_state()
if "briefing_sent" not in st.session_state:
    st.session_state.briefing_sent = st.session_state.daily_state.get("briefing_sent")  # YYYY-MM-DD ou None
if "closing_sent" not in st.session_state:
    st.session_state.closing_sent = st.session_state.daily_state.get("closing_sent")    # YYYY-MM-DD ou None
if "smart_flags" not in st.session_state:
    st.session_state.smart_flags = st.session_state.daily_state.get("smart_flags", {})  # dict
if "awaiting_closing" not in st.session_state:
    st.session_state.awaiting_closing = bool(st.session_state.daily_state.get("awaiting_closing", False))


# =========================
# UTILS TEMPO & FORMAT
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


# =========================
# NOTIFICAÇÃO BROWSER (JS)
# =========================
def request_notification_permission():
    components.html(
        """<script>
        (async function(){
          try {
            if (!('Notification' in window)) return;
            await Notification.requestPermission();
          } catch(e) {}
        })();
        </script>""",
        height=0,
    )

def browser_notify(title: str, body: str):
    payload_title = json.dumps(title)
    payload_body = json.dumps(body)
    components.html(
        f"""<script>
        (function() {{
          try {{
            if ('Notification' in window && Notification.permission === 'granted') {{
              new Notification({payload_title}, {{ body: {payload_body} }});
            }}
          }} catch(e) {{}}
        }})();
        </script>""",
        height=0,
    )


# =========================
# MEMÓRIA LONGA (SQLite)
# =========================
def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_db():
    conn = db()
    conn.execute("""
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        kind TEXT NOT NULL,
        content TEXT NOT NULL,
        meta TEXT
    )
    """)
    try:
        conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS events_fts
        USING fts5(content, content='events', content_rowid='id')
        """)
        conn.execute("""
        CREATE TRIGGER IF NOT EXISTS events_ai
        AFTER INSERT ON events
        BEGIN
            INSERT INTO events_fts(rowid, content) VALUES (new.id, new.content);
        END;
        """)
    except Exception:
        pass
    conn.commit()
    conn.close()

def add_event(kind: str, content: str, meta: str = ""):
    content = (content or "").strip()
    if not content:
        return
    conn = db()
    ts = now_br().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("INSERT INTO events(ts, kind, content, meta) VALUES (?,?,?,?)", (ts, kind, content, meta))
    conn.commit()
    conn.close()

def search_memories(query: str, limit: int = 8):
    query = (query or "").strip()
    if not query:
        return []
    conn = db()
    try:
        rows = conn.execute(
            "SELECT e.ts, e.kind, e.content FROM events_fts f "
            "JOIN events e ON e.id = f.rowid "
            "WHERE events_fts MATCH ? ORDER BY rank LIMIT ?",
            (query, limit)
        ).fetchall()
    except Exception:
        rows = conn.execute(
            "SELECT ts, kind, content FROM events WHERE content LIKE ? ORDER BY id DESC LIMIT ?",
            (f"%{query}%", limit)
        ).fetchall()
    conn.close()
    return rows

init_db()


# =========================
# RESUMO VIVO
# =========================
def load_summary() -> str:
    if not os.path.exists(SUMMARY_PATH):
        return "Resumo vazio."
    try:
        return open(SUMMARY_PATH, "r", encoding="utf-8").read().strip() or "Resumo vazio."
    except Exception:
        return "Resumo vazio."

def save_summary(texto: str):
    if not texto:
        return
    with open(SUMMARY_PATH, "w", encoding="utf-8") as f:
        f.write(texto)

def update_summary_with_llm(new_info: str):
    if not new_info:
        return
    resumo_atual = load_summary()
    prompt = f"""
{ZOE_PERSONA}

Atualize o RESUMO VIVO do usuário. Mantenha curto (max 20 linhas).

RESUMO ATUAL:
{resumo_atual}

NOVA INFO:
{new_info}

Devolva APENAS o resumo novo.
""".strip()
    try:
        resp = client.chat.completions.create(
            model=MODEL_ID,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        ).choices[0].message.content
        save_summary(resp)
    except Exception:
        pass


# =========================
# STORAGE TAREFAS
# =========================
def carregar_tarefas() -> list:
    if not os.path.exists(ARQUIVO_TAREFAS):
        return []
    try:
        with open(ARQUIVO_TAREFAS, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []

def salvar_tarefas(lista: list) -> None:
    tmp = ARQUIVO_TAREFAS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(lista, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ARQUIVO_TAREFAS)

def limpar_texto(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9áàâãéèêíìîóòôõúùûç\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

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


# =========================
# ANTI-DUP INPUT
# =========================
def should_process_input(texto: str) -> bool:
    if not texto or not str(texto).strip():
        return False
    clean = str(texto).strip()
    sig = hashlib.sha256(clean.encode("utf-8")).hexdigest()
    now_ts = time.time()
    if st.session_state.last_input_sig == sig and (now_ts - st.session_state.last_input_time) < 1.0:
        return False
    st.session_state.last_input_sig = sig
    st.session_state.last_input_time = now_ts
    return True


# =========================
# PARSER NLP
# =========================
def parse_relativo(texto: str):
    t = limpar_texto(texto)
    if "daqui um minuto" in t or "daqui 1 minuto" in t or "em 1 minuto" in t:
        return timedelta(minutes=1)
    m = re.search(r"(daqui|em)\s+(\d+)\s*(min|h|hora)", t)
    if not m:
        return None
    n = int(m.group(2))
    u = m.group(3)
    if "h" in u or "hora" in u:
        return timedelta(hours=n)
    return timedelta(minutes=n)

def ajustar_futuro(dt: datetime, agora: datetime) -> datetime:
    if dt >= agora:
        return dt
    tentativa = dt + timedelta(hours=12)
    if tentativa >= agora:
        return tentativa
    return dt + timedelta(days=1)

def extrair_dados_tarefa(texto: str):
    agora = now_floor_minute()
    delta = parse_relativo(texto)
    if delta:
        return {"descricao": texto.split(" em ")[0].split(" daqui ")[0], "data_hora": format_dt(agora + delta)}

    prompt = f"""
{ZOE_PERSONA}

Agora é {format_dt(agora)}. O user disse: "{texto}".
Extraia JSON: {{"descricao": "...", "data_hora": "YYYY-MM-DD HH:MM"}}
Se hora não for dita, assuma o próximo horário lógico.
""".strip()

    try:
        resp = client.chat.completions.create(
            model=MODEL_ID,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
        dt = parse_dt(data["data_hora"])
        data["data_hora"] = format_dt(ajustar_futuro(dt, agora))
        return data
    except Exception:
        return None




# =========================
# CHAT CONTEXTO (LIMPEZA)
# =========================
def to_llm_messages(memoria: list, limit: int = 20) -> list:
    """Converte o histórico do Streamlit (que pode ter chaves extras) para o formato aceito pelo LLM."""
    out = []
    if not memoria:
        return out
    for m in memoria[-limit:]:
        try:
            role = m.get("role")
            content = m.get("content")
        except Exception:
            continue
        if role not in ("user", "assistant", "system"):
            continue
        if content is None:
            continue
        out.append({"role": role, "content": str(content)})
    return out

def format_recent_dialogue(memoria: list, limit: int = 8) -> str:
    """Cria um resumo curtinho do diálogo recente (pra roteamento/decisão)."""
    parts = []
    for m in to_llm_messages(memoria, limit=limit):
        who = "Usuário" if m["role"] == "user" else ASSISTANT_NAME
        txt = m["content"].strip().replace("\n", " ")
        if len(txt) > 160:
            txt = txt[:160] + "…"
        parts.append(f"- {who}: {txt}")
    return "\n".join(parts).strip()

# =========================
# ROUTER
# =========================
def router_llm(texto: str, tarefas: list) -> dict:
    agora = format_dt(now_floor_minute())
    resumo_tarefas = "\n".join([f"{i}: {t['descricao']}" for i, t in enumerate(tarefas)])
    recent_chat = format_recent_dialogue(st.session_state.memoria[:-1] if "memoria" in st.session_state else [], limit=10)

    prompt = f"""
{ZOE_PERSONA}

Agora é {agora}.
Tarefas pendentes:
{resumo_tarefas}

Conversa recente (pra manter contexto):
{recent_chat}

Mensagem do usuário: "{texto}"

Responda APENAS o JSON:
{{
  "action": "TASK_CREATE" ou "TASK_DONE" ou "WEB_SEARCH" ou "CHAT",
  "task_index": (número da tarefa ou -1),
  "minutes": (minutos para adiar ou 0),
  "search_query": (termo de busca ou "")
}}
""".strip()

    default_response = {"action": "CHAT", "task_index": -1, "minutes": 0, "search_query": ""}

    try:
        resp = client.chat.completions.create(
            model=MODEL_ID,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        raw = resp.choices[0].message.content
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            data = json.loads(match.group(0))
            if "Action" in data and "action" not in data:
                data["action"] = data["Action"]
            if "action" not in data:
                data["action"] = "CHAT"
            return data
        return default_response
    except Exception:
        return default_response

def decidir_acao(texto: str, tarefas: list) -> dict:
    t = limpar_texto(texto)
    if t.startswith("/web "):
        return {"action": "WEB_SEARCH", "search_query": str(texto)[5:]}
    if t.startswith("/chat "):
        return {"action": "CHAT"}
    if any(x in t for x in ["cotação", "preço", "clima", "noticia", "notícia", "quem ganhou", "resultado", "últimas", "atualização"]):
        return {"action": "WEB_SEARCH", "search_query": texto}
    return router_llm(texto, tarefas)


# =========================
# WEB / AUDIO
# =========================
def buscar_tavily(q: str):
    try:
        r = tavily.search(query=q, max_results=3)
        return "\n".join([f"{i['title']}: {i['content']}" for i in r.get("results", [])])
    except Exception:
        return None

def ouvir_audio(uploaded_file):
    try:
        b = uploaded_file.getvalue()
        return client.audio.transcriptions.create(
            file=("audio.wav", b, "audio/wav"),
            model="whisper-large-v3",
            response_format="text",
            language="pt",
        )
    except Exception:
        return None

def falar_bytes(texto: str):
    try:
        out = f"tts_{uuid.uuid4().hex[:5]}.mp3"
        asyncio.run(edge_tts.Communicate(texto, "pt-BR-FranciscaNeural").save(out))
        b = open(out, "rb").read()
        os.remove(out)
        return b
    except Exception:
        return None


# =========================
# PROACTIVE ALERT
# =========================
def pick_due_task(tarefas: list, agora: datetime) -> Optional[dict]:
    if em_horario_silencioso(agora):
        return None
    candidates = []
    for t in tarefas:
        if t.get("status") == "silenciada":
            continue
        try:
            nr = parse_dt(t.get("next_remind_at") or t["data_hora"])
            if agora >= nr:
                diff = (agora - parse_dt(t["data_hora"])).total_seconds() / 60
                candidates.append((diff, t))
        except Exception:
            continue
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
# REFRESH LOOP
# =========================
st_autorefresh(interval=AUTO_REFRESH_MS, key="tick")

agora = now_floor_minute()
tarefas = [normalizar_tarefa(t) for t in carregar_tarefas()]
salvar_tarefas(tarefas)

# =========================
# ROTINAS PROATIVAS (sem mexer no seu sistema de tarefas)
# =========================
settings = st.session_state.settings
daily_state = st.session_state.daily_state
today = today_key(agora)

# 1) Briefing matinal (uma vez por dia)
if settings.get("briefing_enabled", True) and not em_horario_silencioso(agora) and same_minute(agora, settings.get("briefing_time","07:00")):
    if st.session_state.briefing_sent != today:
        msg = build_briefing(settings, tarefas, agora)
        enviar_telegram(msg)
        browser_notify("Briefing Matinal", "Te mandei o briefing do dia ✅")
        add_event("briefing", msg)
        st.session_state.briefing_sent = today
        daily_state["briefing_sent"] = today
        save_daily_state(daily_state)

# 2) Lembretes inteligentes (clima) — sem duplicar com tarefas
if settings.get("smart_enabled", True) and not em_horario_silencioso(agora):
    # tenta pegar clima se tiver lat/lon
    w = None
    if settings.get("lat") is not None and settings.get("lon") is not None:
        w = fetch_weather(settings["lat"], settings["lon"])

    flags = dict(st.session_state.smart_flags or {})
    flags.setdefault(today, {})

    if w:
        rain_prob = w.get("rain_prob")
        tmax = w.get("temp_max")

        # Guarda-chuva no horário de sair
        if isinstance(rain_prob, (int, float)) and rain_prob >= int(settings.get("rain_threshold", 60)):
            if same_minute(agora, settings.get("leave_time","07:20")) and not flags[today].get("umbrella"):
                m = f"☂️ Chuva forte na previsão hoje ({int(rain_prob)}%). Se for sair agora, leva guarda-chuva/jaqueta 😄"
                enviar_telegram(m)
                browser_notify("Lembrete (clima)", "Chance alta de chuva — guarda-chuva!")
                add_event("smart_reminder", m)
                flags[today]["umbrella"] = True

        # Hidratação ao meio-dia se calor
        if isinstance(tmax, (int, float)) and tmax >= int(settings.get("heat_threshold", 30)):
            if same_minute(agora, "12:00") and not flags[today].get("water"):
                m = f"💧 Hoje tá pra {round(tmax)}°C. Água agora = menos sofrimento depois 😅"
                enviar_telegram(m)
                browser_notify("Lembrete (saúde)", "Calor forte — água!")
                add_event("smart_reminder", m)
                flags[today]["water"] = True

    st.session_state.smart_flags = flags
    daily_state["smart_flags"] = flags
    save_daily_state(daily_state)

# 3) Fechamento diário (uma vez por dia)
if settings.get("closing_enabled", True) and same_minute(agora, settings.get("closing_time","21:30")):
    if st.session_state.closing_sent != today:
        m = build_closing_prompt(agora)
        enviar_telegram(m)
        browser_notify("Fechamento do dia", "Me conta rapidinho como foi seu dia ✅")
        add_event("closing_prompt", m)
        st.session_state.closing_sent = today
        st.session_state.awaiting_closing = True
        daily_state["closing_sent"] = today
        daily_state["awaiting_closing"] = True
        save_daily_state(daily_state)


tarefa_alertada = pick_due_task(tarefas, agora)
if tarefa_alertada:
    next_at = tarefa_alertada.get("next_remind_at") or tarefa_alertada.get("data_hora")
    fp = f"{tarefa_alertada['id']}::{next_at}"

    if st.session_state.last_alert_fingerprint != fp:
        st.session_state.last_alert_fingerprint = fp

        mensagem_alerta = (
            f"🔔 **Ei! Lembrete na área:** {tarefa_alertada['descricao']}\n\n"
            f"⏰ **{tarefa_alertada['data_hora']}**"
        )

        st.session_state.memoria.append({"role": "assistant", "content": mensagem_alerta})

        browser_notify("Lembrete", tarefa_alertada["descricao"])
        enviar_telegram(f"🔔 *ALERTA*: {tarefa_alertada['descricao']}\n⏰ {tarefa_alertada['data_hora']}")

        b = falar_bytes("Atenção, você tem um lembrete.")
        if b:
            st.session_state.last_audio_bytes = b

        updated = schedule_next(agora, tarefa_alertada)
        tarefas = [updated if x["id"] == tarefa_alertada["id"] else x for x in tarefas]
        salvar_tarefas(tarefas)
        add_event("alert", f"Disparado: {tarefa_alertada['descricao']}")



# =========================
# TOPBAR (hamburger + avatar)
# =========================
_avatar_uri = load_avatar_data_uri(st.session_state.settings)
_avatar_html = (
    f'<div class="avatar"><img src="{_avatar_uri}" alt="avatar"></div>'
    if _avatar_uri else f'<div class="avatar-fallback">{ASSISTANT_NAME[:1].upper()}</div>'
)

_topbar_html = f'''
<div class="topbar">
  <div class="topbar-inner">
    <div class="tb-left">
      <div id="hamb-btn" class="hamb">☰</div>
      {_avatar_html}
    </div>
    <div class="tb-title">{ASSISTANT_NAME} <span class="tb-sub">• {ASSISTANT_TAGLINE}</span></div>
    <div class="tb-right"></div>
  </div>
</div>
'''
st.markdown(_topbar_html, unsafe_allow_html=True)



# =========================
# SIDEBAR (TUDO DISCRETO AQUI NO HAMBÚRGUER)
# =========================
with st.sidebar:
    st.markdown(f"## {ASSISTANT_NAME}")
    st.caption(ASSISTANT_ONE_LINER)
    st.caption(f"🕒 {agora.strftime('%H:%M')} • {agora.strftime('%d/%m/%Y')}")
    st.divider()

    # ===== Avatar (Rebeca / qualquer imagem que você quiser) =====
    with st.expander("👤 Avatar", expanded=False):
        st.caption("Envie uma imagem (png/jpg/webp). Ela vai aparecer no topo como ícone da IA.")
        up = st.file_uploader("Avatar", type=["png", "jpg", "jpeg", "webp"], label_visibility="collapsed")
        if up is not None:
            st.image(up, caption="Prévia", use_container_width=True)
            if st.button("💾 Salvar avatar", use_container_width=True):
                st.session_state.settings = save_uploaded_avatar(up, st.session_state.settings)
                save_settings(st.session_state.settings)
                st.toast("Avatar salvo ✅")
                st.rerun()

        cur = (st.session_state.settings or {}).get("avatar_path") or "avatar.png"
        if os.path.exists(cur):
            st.caption(f"Atual: `{cur}`")

    # ===== Ações rápidas =====
    colA, colB = st.columns(2)
    with colA:
        if st.button("🔔 Teste", use_container_width=True, help="Teste Telegram + notificação do navegador"):
            request_notification_permission()
            enviar_telegram(f"Teste de notificação da {ASSISTANT_NAME}! 🤖")
            st.toast("Teste enviado (Telegram + Browser).")
    with colB:
        if st.button("🧹 Áudio", use_container_width=True, help="Limpa o cache do último TTS"):
            st.session_state.last_audio_bytes = None
            st.toast("Cache de áudio limpo.")

    if st.session_state.last_audio_bytes:
        st.caption("Último áudio (TTS)")
        st.audio(st.session_state.last_audio_bytes, format="audio/mp3")

    st.divider()

    # ===== Rotinas / Briefing / Fechamento (ANTES ficava no meio da tela) =====
    with st.expander("⏱️ Rotinas", expanded=False):
        s = dict(st.session_state.settings)

        s["city_name"] = st.text_input("Cidade (para clima)", value=s.get("city_name","Ilhéus, BA"))
        c1, c2 = st.columns(2)
        with c1:
            s["briefing_enabled"] = st.toggle("Briefing matinal", value=bool(s.get("briefing_enabled", True)))
            s["briefing_time"] = st.text_input("Horário do briefing (HH:MM)", value=s.get("briefing_time","07:00"))
        with c2:
            s["closing_enabled"] = st.toggle("Fechamento diário", value=bool(s.get("closing_enabled", True)))
            s["closing_time"] = st.text_input("Horário do fechamento (HH:MM)", value=s.get("closing_time","21:30"))

        st.divider()
        s["smart_enabled"] = st.toggle("Lembretes inteligentes (clima)", value=bool(s.get("smart_enabled", True)))
        c3, c4, c5 = st.columns(3)
        with c3:
            s["leave_time"] = st.text_input("Horário típico de sair (HH:MM)", value=s.get("leave_time","07:20"))
        with c4:
            s["rain_threshold"] = st.number_input("Chuva (%) pra lembrar", min_value=10, max_value=100, value=int(s.get("rain_threshold",60)), step=5)
        with c5:
            s["heat_threshold"] = st.number_input("Calor (°C) pra lembrar água", min_value=20, max_value=45, value=int(s.get("heat_threshold",30)), step=1)

        st.divider()
        geo_col1, geo_col2 = st.columns([1,1])
        with geo_col1:
            if st.button("📍 Atualizar localização", use_container_width=True):
                g = geocode_city(s["city_name"])
                if g and g.get("lat") is not None and g.get("lon") is not None:
                    s["lat"] = float(g["lat"])
                    s["lon"] = float(g["lon"])
                    st.toast(f"Localização ok: {g.get('name','')} ({s['lat']:.3f}, {s['lon']:.3f})")
                else:
                    st.toast("Não consegui achar essa cidade 😅 Tenta: 'Ilhéus, BA' ou 'Salvador, BA'")
        with geo_col2:
            if st.button("💾 Salvar rotinas", use_container_width=True):
                st.session_state.settings = dict(s)
                save_settings(st.session_state.settings)
                st.toast("Configurações salvas ✅")
                st.rerun()

        if s.get("lat") is not None and s.get("lon") is not None:
            st.caption(f"Lat/Lon: {float(s['lat']):.3f}, {float(s['lon']):.3f}")
        else:
            st.caption("Lat/Lon: (não configurado) — clique em “Atualizar localização”.")

    # ===== Agenda / Tarefas =====
    with st.expander("📌 Agenda de hoje", expanded=False):
        if not tarefas:
            st.caption("Vazia.")
        else:
            for t in sorted(tarefas, key=lambda x: x.get("data_hora", ""))[:12]:
                st.write(f"• **{t.get('data_hora','')}** — {t.get('descricao','')[:80]}")

    with st.expander("✅ Gerenciar tarefas", expanded=False):
        tarefas = [normalizar_tarefa(t) for t in carregar_tarefas()]
        salvar_tarefas(tarefas)

        if not tarefas:
            st.info("Sem tarefas.")
        else:
            for t in sorted(tarefas, key=lambda x: x.get("data_hora", ""))[:25]:
                st.write(f"**{t.get('data_hora','')}** — {t.get('descricao','')}")
                c1, c2, c3 = st.columns(3)
                if c1.button("✅", key=f"done_{t['id']}", help="Feito"):
                    tarefas = [x for x in tarefas if x["id"] != t["id"]]
                    salvar_tarefas(tarefas)
                    update_summary_with_llm(f"Concluiu: {t['descricao']}")
                    add_event("task_done", f"Feito: {t['descricao']}")
                    st.rerun()
                if c2.button("💤", key=f"sno_{t['id']}", help="Soneca +30min"):
                    t["next_remind_at"] = format_dt(now_floor_minute() + timedelta(minutes=30))
                    t["snoozed_until"] = t["next_remind_at"]
                    t["remind_count"] = 0
                    salvar_tarefas(tarefas)
                    add_event("task_snooze", f"Soneca: {t['descricao']} +30min")
                    st.rerun()
                if c3.button("🔕", key=f"sil_{t['id']}", help="Silenciar"):
                    t["status"] = "silenciada"
                    t["next_remind_at"] = format_dt(now_floor_minute() + timedelta(days=365))
                    salvar_tarefas(tarefas)
                    add_event("task_silence", f"Silenciada: {t['descricao']}")
                    st.rerun()
                st.divider()

    # ===== Memória =====
    with st.expander("🧠 Memória", expanded=False):
        with st.expander("Resumo vivo", expanded=False):
            resumo = st.text_area("Resumo", value=load_summary(), height=160)
            if st.button("💾 Salvar resumo", use_container_width=True):
                save_summary(resumo)
                st.toast("Resumo salvo.")

        with st.expander("Buscar na memória", expanded=False):
            q = st.text_input("Buscar", placeholder="Ex: alerta, tarefa, mercado…")
            if q.strip():
                rows = search_memories(q.strip(), limit=10)
                if not rows:
                    st.caption("Nada encontrado.")
                else:
                    for ts, kind, content in rows:
                        st.caption(f"{ts} • {kind}")
                        st.write(content)
                        st.divider()

    st.divider()
    if st.button("🗑️ Limpar chat", use_container_width=True):
        st.session_state.memoria = []
        st.toast("Chat limpo.")
        st.rerun()

# =========================
# CHAT (ÚNICA COISA NA TELA PRINCIPAL)
# =========================
# Renderiza histórico
for m in st.session_state.memoria[-24:]:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        # Mensagem pequena abaixo quando realmente usou web
        if m.get("web_used"):
            st.caption("🔎 Usei busca na web pra responder.")


# =========================
# INPUT: texto (fixo) + mic colado na barra
# =========================
texto_input = st.chat_input(f"Fala comigo, eu sou a {ASSISTANT_NAME} 😄")

# Mic fica “colado” à barra por CSS
audio_val = st.audio_input(" ", label_visibility="collapsed")

usou_voz = False
if audio_val:
    ah = hashlib.sha256(audio_val.getvalue()).hexdigest()
    if ah != st.session_state.ultimo_audio_hash:
        st.session_state.ultimo_audio_hash = ah
        transcrito = ouvir_audio(audio_val)
        if transcrito:
            texto_input = str(transcrito).strip()
            usou_voz = True


# =========================
# LÓGICA DE RESPOSTA
# =========================
# Para evitar casos em que o usuário envia e um rerun/auto-refresh “engole” a mensagem,
# a gente guarda a entrada em session_state e processa em seguida.
if texto_input and st.session_state.pending_input is None and should_process_input(str(texto_input)):
    st.session_state.pending_input = str(texto_input).strip()
    st.session_state.pending_usou_voz = bool(usou_voz)


# Defaults para evitar NameError quando não há input neste rerun
user_txt = ""
usou_voz_proc = False

if st.session_state.pending_input:
    user_txt = str(st.session_state.pending_input).strip()
    usou_voz_proc = bool(st.session_state.pending_usou_voz)

    # limpa pendência antes de processar (evita loop se der exceção)
    st.session_state.pending_input = None
    st.session_state.pending_usou_voz = False

    st.session_state.memoria.append({"role": "user", "content": user_txt})
    add_event("chat_user", user_txt)

    tarefas = [normalizar_tarefa(t) for t in carregar_tarefas()]
    salvar_tarefas(tarefas)


web_used = False
resp_txt = ""

# =========================
# FECHAMENTO DIÁRIO (captura resposta do usuário)
# =========================
# A LÓGICA AGORA SÓ EXECUTA SE TIVER TEXTO DO USUÁRIO
if user_txt:
    # 1. Comando manual
    if user_txt.strip().lower().startswith("/fechamento"):
        st.session_state.awaiting_closing = True
        st.session_state.daily_state["awaiting_closing"] = True
        save_daily_state(st.session_state.daily_state)
        resp_txt = build_closing_prompt(now_floor_minute())
        st.session_state.memoria.append({"role": "assistant", "content": resp_txt})
        add_event("closing_prompt_manual", resp_txt)
        st.rerun()

    # 2. Resposta de fechamento (se pendente)
    elif st.session_state.awaiting_closing and not user_txt.strip().startswith("/"):
        add_event("daily_review", user_txt)
        update_summary_with_llm(f"Fechamento do dia: {user_txt}")
        st.session_state.awaiting_closing = False
        st.session_state.daily_state["awaiting_closing"] = False
        save_daily_state(st.session_state.daily_state)
        resp_txt = "Fechou 😄 Registrei teu fechamento de hoje. Amanhã eu já ajusto o teu briefing/lembretes com base nisso."
        st.session_state.memoria.append({"role": "assistant", "content": resp_txt})
        add_event("chat_assistant", resp_txt)
        st.rerun()

    # 3. Lógica Normal (ELSE) - só roda se não caiu nos anteriores
    else:
        # Atalho determinístico: horas/data (evita alucinação e fica estável)
        tnorm = limpar_texto(user_txt)
        if re.search(r"\b(que horas|horas s[aã]o|que hora)\b", tnorm):
            agora_br = now_br()
            resp_txt = f"Agora no Brasil são **{agora_br.strftime('%H:%M')}** ({agora_br.strftime('%d/%m/%Y')})."
            st.session_state.memoria.append({"role": "assistant", "content": resp_txt})
            add_event("chat_assistant", resp_txt)
            st.rerun()

        with st.spinner(f"{ASSISTANT_NAME} tá pensando..."):
            acao = decidir_acao(user_txt, tarefas)

            if acao.get("action") == "TASK_CREATE":
                d = extrair_dados_tarefa(user_txt)
                if d:
                    d = normalizar_tarefa(d)
                    tarefas.append(d)
                    salvar_tarefas(tarefas)
                    resp_txt = f"Fechou! ✅ Agendei **{d['descricao']}** pra **{d['data_hora']}**."
                    add_event("task_create", resp_txt)
                    update_summary_with_llm(f"Nova tarefa: {d['descricao']} @ {d['data_hora']}")
                else:
                    resp_txt = "Beleza… mas não peguei a data/hora 😅 Ex: *me lembra de X amanhã às 15:00*."

            elif acao.get("action") == "WEB_SEARCH":
                web_used = True
                q = acao.get("search_query") or user_txt
                res = buscar_tavily(q)
                prompt_web = f"""
{ZOE_PERSONA}

Você recebeu resultados de busca na web (resuma e responda com base neles).
RESULTADOS:
{res}

PERGUNTA DO USUÁRIO:
{user_txt}

Responda direto, do jeito da Zoe (curto, útil, com gíria leve/emoji na medida).
""".strip()
                try:
                    resp_txt = client.chat.completions.create(
                        model=MODEL_ID,
                        messages=[{"role": "user", "content": prompt_web}]
                    ).choices[0].message.content
                except Exception:
                    resp_txt = "Deu ruim pra consultar a web agora 😅 Tenta de novo daqui a pouquinho."
                add_event("web_search", f"Q: {q}")

            elif acao.get("action") == "TASK_DONE":
                if tarefas:
                    removida = tarefas.pop(0)
                    salvar_tarefas(tarefas)
                    resp_txt = f"Top! ✅ Marquei como feito: **{removida['descricao']}**."
                    add_event("task_done", resp_txt)
                    update_summary_with_llm(f"Concluiu: {removida['descricao']}")
                else:
                    resp_txt = "Não tem nada na agenda agora — tá suave 😄"

            else:
                # CHAT NORMAL
                mems = search_memories(user_txt)
                ctx_mem = "\n".join([m[2] for m in mems])
                sys_prompt = f"""{ZOE_PERSONA}

Informações do usuário (resumo vivo):
{load_summary()}

Contexto de memória (pode usar se for relevante):
{ctx_mem}

Regras rápidas:
- Responda em PT-BR.
- Seja direta e prática.
- Use gírias leves e emojis às vezes.
- Se a pergunta pedir algo que depende de dados atuais, sugira usar /web.
""".strip()

                msgs = [{"role": "system", "content": sys_prompt}] + to_llm_messages(st.session_state.memoria, limit=20)
                try:
                    resp_txt = client.chat.completions.create(
                        model=MODEL_ID,
                        messages=msgs,
                        temperature=0.2
                    ).choices[0].message.content
                except Exception:
                    resp_txt = "Ops, deu um errinho pra gerar a resposta agora 😅 Tenta de novo?"

        st.session_state.memoria.append({"role": "assistant", "content": resp_txt, **({"web_used": True} if web_used else {})})
        add_event("chat_assistant", resp_txt)

        # Se veio de voz, TTS curtinho (opcional)
        if usou_voz_proc and resp_txt:
            b = falar_bytes(resp_txt[:180])
            if b:
                st.session_state.last_audio_bytes = b

        st.rerun()

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

MODEL_ID = "llama-3.3-70b-versatile"
ARQUIVO_TAREFAS = "tarefas.json"

FUSO_BR = ZoneInfo("America/Sao_Paulo")
DB_PATH = "jarvis_memory.db"
SUMMARY_PATH = "summary.txt"

REMINDER_SCHEDULE_MIN = [0, 10, 30, 120]
QUIET_START = 22
QUIET_END = 7

AUTO_REFRESH_MS = 10_000  # 10s


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
            --bg:#0b0f16;
            --bg2:#0e1422;
            --text: rgba(255,255,255,0.92);
            --muted: rgba(255,255,255,0.62);
            --stroke: rgba(255,255,255,0.10);
            --card: rgba(255,255,255,0.06);
        }

        [data-testid="stAppViewContainer"]{
            background:
                radial-gradient(900px 520px at 10% 0%, rgba(122,162,247,0.14), transparent 60%),
                radial-gradient(800px 450px at 95% 10%, rgba(72,222,128,0.10), transparent 58%),
                linear-gradient(180deg, var(--bg2) 0%, var(--bg) 100%);
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
            background: rgba(11,15,22,0.92);
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
            background: rgba(11,15,22,0.98) !important;
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
            background: rgba(11,15,22,0.92);
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
# ROUTER
# =========================
def router_llm(texto: str, tarefas: list) -> dict:
    agora = format_dt(now_floor_minute())
    resumo_tarefas = "\n".join([f"{i}: {t['descricao']}" for i, t in enumerate(tarefas)])

    prompt = f"""
{ZOE_PERSONA}

Agora é {agora}.
Tarefas pendentes:
{resumo_tarefas}

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
# TOPBAR (hamburger funcional + sem avatar)
# =========================
st.markdown(
    f"""
    <div class="topbar">
      <div class="topbar-inner">
        <div id="hamb-btn" class="hamb">☰</div>
        <div class="tb-title">{ASSISTANT_NAME} <span class="tb-sub">• {ASSISTANT_TAGLINE}</span></div>
        <div class="tb-right"></div>
      </div>
    </div>
    """,
    unsafe_allow_html=True
)


# =========================
# SIDEBAR (CONFIGURAÇÕES ESCONDIDAS AQUI)
# =========================
with st.sidebar:
    st.markdown(f"## ⚙️ Configurações da {ASSISTANT_NAME}")
    st.caption(ASSISTANT_ONE_LINER)
    st.caption(f"🕒 {agora.strftime('%H:%M')} • {agora.strftime('%d/%m/%Y')}")
    st.divider()

    colA, colB = st.columns(2)
    with colA:
        if st.button("🔔 Teste", use_container_width=True):
            request_notification_permission()
            enviar_telegram(f"Teste de notificação da {ASSISTANT_NAME}! 🤖")
            st.toast("Teste enviado (Telegram + Browser).")
    with colB:
        if st.button("🧹 Áudio", use_container_width=True):
            st.session_state.last_audio_bytes = None
            st.toast("Cache de áudio limpo.")

    if st.session_state.last_audio_bytes:
        st.caption("Último áudio (TTS)")
        st.audio(st.session_state.last_audio_bytes, format="audio/mp3")

    st.divider()
    st.markdown("### 📌 Agenda")
    if not tarefas:
        st.caption("Vazia.")
    else:
        for t in sorted(tarefas, key=lambda x: x.get("data_hora", ""))[:10]:
            st.write(f"• **{t.get('data_hora','')}** — {t.get('descricao','')[:60]}")

    with st.expander("Gerenciar tarefas", expanded=False):
        tarefas = [normalizar_tarefa(t) for t in carregar_tarefas()]
        salvar_tarefas(tarefas)

        if not tarefas:
            st.info("Sem tarefas.")
        else:
            for t in sorted(tarefas, key=lambda x: x.get("data_hora", ""))[:20]:
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

    st.divider()
    st.markdown("### 🧠 Memória")
    with st.expander("Resumo vivo", expanded=False):
        resumo = st.text_area("Resumo", value=load_summary(), height=160)
        if st.button("💾 Salvar resumo"):
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

            msgs = [{"role": "system", "content": sys_prompt}] + st.session_state.memoria[-8:]
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

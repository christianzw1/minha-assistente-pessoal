import streamlit as st
import streamlit.components.v1 as components
from groq import Groq
from tavily import TavilyClient
from streamlit_autorefresh import st_autorefresh
import requests  # Importante para o Telegram

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
from typing import Optional, List, Tuple


# =========================
# CONFIG
# =========================
st.set_page_config(page_title="Assistente Pessoal", page_icon="🤖", layout="wide")

MODEL_ID = "llama-3.3-70b-versatile"
ARQUIVO_TAREFAS = "tarefas.json"

FUSO_BR = ZoneInfo("America/Sao_Paulo")
DB_PATH = "jarvis_memory.db"
SUMMARY_PATH = "summary.txt"

# Agenda de repetição de alertas (minutos após o horário original)
REMINDER_SCHEDULE_MIN = [0, 10, 30, 120] 
QUIET_START = 22
QUIET_END = 7

AUTO_REFRESH_MS = 10_000  # 10s (aumentei um pouco para economizar recursos)


# =========================
# CSS / UI BLINDADA (ESTILO APP NATIVO)
# =========================
def inject_css():
    # Meta tags para PWA (Tela cheia no celular)
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
        /* ----- Remove tralhas do Streamlit ----- */
        header[data-testid="stHeader"] { display: none !important; }
        footer { display: none !important; }
        #MainMenu { display: none !important; }
        .stDeployButton { display: none !important; }
        [data-testid="stToolbar"] { display: none !important; }
        [data-testid="stDecoration"] { display: none !important; }
        [data-testid="stStatusWidget"] { display: none !important; }
        
        /* Tenta esconder o botão 'Manage App' */
        .viewerBadge_container__1QSob { display: none !important; }

        /* ----- Ajuste Layout Mobile ----- */
        .block-container {
            padding-top: 10px !important;
            padding-bottom: 80px !important; /* Espaço para o chat input fixo */
            padding-left: 1rem !important;
            padding-right: 1rem !important;
            max-width: 100%;
        }

        /* ----- Fundo Cyberpunk Clean ----- */
        [data-testid="stAppViewContainer"] {
            background: radial-gradient(1200px 600px at 10% 0%, rgba(12, 16, 33, 1), transparent 55%),
                        radial-gradient(1000px 500px at 90% 10%, rgba(0, 0, 0, 1), transparent 55%),
                        linear-gradient(180deg, #0e1117 0%, #000000 100%);
        }

        /* ----- Cards ----- */
        .card {
            border: 1px solid rgba(255,255,255,0.05);
            background: rgba(20, 24, 35, 0.7);
            backdrop-filter: blur(10px);
            border-radius: 12px;
            padding: 14px;
            margin-bottom: 10px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.15);
        }
        .card-title {
            font-size: 16px;
            font-weight: 600;
            color: #e0e0e0;
            margin-bottom: 4px;
        }
        .muted {
            opacity: 0.6;
            font-size: 12px;
            color: #a0a0a0;
        }

        /* ----- Tabs Arredondadas ----- */
        [data-testid="stTabs"] button {
            border-radius: 20px !important;
            padding: 8px 16px !important;
            font-size: 14px !important;
        }

        /* ----- Chat Input Flutuante (Estilo WhatsApp) ----- */
        [data-testid="stChatInput"] {
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            padding: 10px 1rem 20px 1rem;
            background: rgba(14, 17, 23, 0.95);
            border-top: 1px solid rgba(255,255,255,0.1);
            z-index: 999;
        }
        
        /* Botões padronizados */
        .stButton button {
            border-radius: 8px !important;
            border: 1px solid rgba(255,255,255,0.1) !important;
        }

        /* Scrollbar discreta */
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.2); border-radius: 4px; }
        </style>
        """,
        unsafe_allow_html=True,
    )

inject_css()


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
# TELEGRAM FUNC
# =========================
def enviar_telegram(mensagem: str):
    """Envia mensagem para o seu Telegram pessoal."""
    token = st.secrets.get("TELEGRAM_TOKEN")
    chat_id = st.secrets.get("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        return # Falha silenciosa se não tiver configurado

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
# NOTIFICAÇÃO BROWSER (JS) - BACKUP
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
    # Tenta criar FTS (Busca full-text)
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(content, content='events', content_rowid='id')")
        conn.execute("CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events BEGIN INSERT INTO events_fts(rowid, content) VALUES (new.id, new.content); END;")
    except Exception:
        pass
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
    query = (query or "").strip()
    if not query: return []
    conn = db()
    try:
        rows = conn.execute("SELECT e.ts, e.kind, e.content FROM events_fts f JOIN events e ON e.id = f.rowid WHERE events_fts MATCH ? ORDER BY rank LIMIT ?", (query, limit)).fetchall()
    except Exception:
        rows = conn.execute("SELECT ts, kind, content FROM events WHERE content LIKE ? ORDER BY id DESC LIMIT ?", (f"%{query}%", limit)).fetchall()
    conn.close()
    return rows

def get_last_events(limit: int = 50):
    conn = db()
    rows = conn.execute("SELECT ts, kind, content FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return list(reversed(rows))

init_db()


# =========================
# RESUMO VIVO
# =========================
def load_summary() -> str:
    if not os.path.exists(SUMMARY_PATH): return "Resumo vazio."
    try:
        return open(SUMMARY_PATH, "r", encoding="utf-8").read().strip() or "Resumo vazio."
    except: return "Resumo vazio."

def save_summary(texto: str):
    if not texto: return
    with open(SUMMARY_PATH, "w", encoding="utf-8") as f:
        f.write(texto)

def update_summary_with_llm(new_info: str):
    if not new_info: return
    resumo_atual = load_summary()
    prompt = f"""
    Atualize o RESUMO VIVO do usuário. Mantenha curto (max 20 linhas).
    
    RESUMO ATUAL:
    {resumo_atual}
    
    NOVA INFO:
    {new_info}
    
    Devolva APENAS o resumo novo.
    """
    try:
        resp = client.chat.completions.create(
            model=MODEL_ID, messages=[{"role": "user", "content": prompt}], temperature=0.2
        ).choices[0].message.content
        save_summary(resp)
    except: pass


# =========================
# STORAGE TAREFAS
# =========================
def carregar_tarefas() -> list:
    if not os.path.exists(ARQUIVO_TAREFAS): return []
    try:
        with open(ARQUIVO_TAREFAS, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except: return []

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
    if not texto or not str(texto).strip(): return False
    clean = str(texto).strip()
    sig = hashlib.sha256(clean.encode("utf-8")).hexdigest()
    now_ts = time.time()
    if st.session_state.last_input_sig == sig and (now_ts - st.session_state.last_input_time) < 3.0:
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
    if not m: return None
    n = int(m.group(2))
    u = m.group(3)
    if "h" in u or "hora" in u: return timedelta(hours=n)
    return timedelta(minutes=n)

def ajustar_futuro(dt: datetime, agora: datetime) -> datetime:
    if dt >= agora: return dt
    tentativa = dt + timedelta(hours=12)
    if tentativa >= agora: return tentativa
    return dt + timedelta(days=1)

def extrair_dados_tarefa(texto: str):
    agora = now_floor_minute()
    delta = parse_relativo(texto)
    if delta:
        return {"descricao": texto.split(" em ")[0].split(" daqui ")[0], "data_hora": format_dt(agora + delta)}
    
    prompt = f"""
    Agora é {format_dt(agora)}. O user disse: "{texto}".
    Extraia JSON: {{"descricao": "...", "data_hora": "YYYY-MM-DD HH:MM"}}
    Se hora não for dita, assuma o próximo horário lógico.
    """
    try:
        resp = client.chat.completions.create(
            model=MODEL_ID, messages=[{"role": "user", "content": prompt}], response_format={"type": "json_object"}
        )
        data = json.loads(resp.choices[0].message.content)
        dt = parse_dt(data["data_hora"])
        data["data_hora"] = format_dt(ajustar_futuro(dt, agora))
        return data
    except: return None


# =========================
# ROUTER
# =========================
def router_llm(texto: str, tarefas: list) -> dict:
    agora = format_dt(now_floor_minute())
    resumo_tarefas = "\n".join([f"{i}: {t['descricao']}" for i, t in enumerate(tarefas)])
    prompt = f"""
    Agora: {agora}.
    Tarefas: {resumo_tarefas}
    Msg: "{texto}"
    
    Classifique em JSON:
    Action: TASK_CREATE | TASK_DONE | TASK_SNOOZE | TASK_SILENCE | WEB_SEARCH | CHAT
    task_index: ID numérico se houver
    minutes: int se houver
    search_query: string se houver
    """
    try:
        resp = client.chat.completions.create(
            model=MODEL_ID, messages=[{"role": "user", "content": prompt}], temperature=0
        )
        raw = resp.choices[0].message.content
        return json.loads(re.search(r"\{.*\}", raw, re.DOTALL).group(0))
    except: return {"action": "CHAT", "task_index": -1}

def decidir_acao(texto: str, tarefas: list) -> dict:
    t = limpar_texto(texto)
    if t.startswith("/web "): return {"action": "WEB_SEARCH", "search_query": str(texto)[5:]}
    if t.startswith("/chat "): return {"action": "CHAT"}
    
    if any(x in t for x in ["cotação", "preço", "clima", "noticia", "quem ganhou"]):
        return {"action": "WEB_SEARCH", "search_query": texto}
        
    return router_llm(texto, tarefas)


# =========================
# WEB / AUDIO
# =========================
def buscar_tavily(q: str):
    try:
        r = tavily.search(query=q, max_results=3)
        return "\n".join([f"{i['title']}: {i['content']}" for i in r.get("results", [])])
    except: return None

def ouvir_audio(uploaded_file):
    try:
        b = uploaded_file.getvalue()
        return client.audio.transcriptions.create(
            file=("audio.wav", b, "audio/wav"), model="whisper-large-v3", response_format="text", language="pt"
        )
    except: return None

def falar_bytes(texto: str):
    try:
        out = f"tts_{uuid.uuid4().hex[:5]}.mp3"
        asyncio.run(edge_tts.Communicate(texto, "pt-BR-FranciscaNeural").save(out))
        b = open(out, "rb").read()
        os.remove(out)
        return b
    except: return None


# =========================
# PROACTIVE ALERT (O CORAÇÃO DO SISTEMA)
# =========================
def pick_due_task(tarefas: list, agora: datetime) -> Optional[dict]:
    if em_horario_silencioso(agora): return None
    candidates = []
    for t in tarefas:
        if t.get("status") == "silenciada": continue
        try:
            nr = parse_dt(t.get("next_remind_at") or t["data_hora"])
            if agora >= nr:
                diff = (agora - parse_dt(t["data_hora"])).total_seconds() / 60
                candidates.append((diff, t))
        except: continue
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

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

# --- Refresh Loop ---
st_autorefresh(interval=AUTO_REFRESH_MS, key="tick")

agora = now_floor_minute()
tarefas = [normalizar_tarefa(t) for t in carregar_tarefas()]
salvar_tarefas(tarefas)

mensagem_alerta = None
tarefa_alertada = pick_due_task(tarefas, agora)

if tarefa_alertada:
    next_at = tarefa_alertada.get("next_remind_at") or tarefa_alertada.get("data_hora")
    fp = f"{tarefa_alertada['id']}::{next_at}"

    if st.session_state.last_alert_fingerprint != fp:
        st.session_state.last_alert_fingerprint = fp
        
        # 1. Mensagem Visual
        mensagem_alerta = (
            f"🔔 **Lembrete:** {tarefa_alertada['descricao']}\n\n"
            f"Horário: **{tarefa_alertada['data_hora']}**"
        )
        
        # 2. Notificação Browser (se tela aberta)
        browser_notify("Lembrete", tarefa_alertada["descricao"])
        
        # 3. TELEGRAM (A notificação real que vibra o celular)
        msg_tg = f"🔔 *ALERTA*: {tarefa_alertada['descricao']}\n⏰ {tarefa_alertada['data_hora']}"
        enviar_telegram(msg_tg)

        # 4. Áudio
        b = falar_bytes("Atenção, você tem um lembrete.")
        if b: st.session_state.last_audio_bytes = b

        # 5. Reagendar
        updated = schedule_next(agora, tarefa_alertada)
        tarefas = [updated if x["id"] == tarefa_alertada["id"] else x for x in tarefas]
        salvar_tarefas(tarefas)
        add_event("alert", f"Disparado: {tarefa_alertada['descricao']}")


# =========================
# HEADER
# =========================
c1, c2 = st.columns([0.7, 0.3])
with c1:
    st.markdown("<div class='card-title'>🤖 Assistente Pessoal</div><div class='muted'>Full Control • Memória • Web</div>", unsafe_allow_html=True)
with c2:
    st.markdown(f"<div class='card-title' style='text-align:right'>{agora.strftime('%H:%M')}</div>", unsafe_allow_html=True)

if st.button("🔔 Testar Notificação", use_container_width=True):
    request_notification_permission()
    enviar_telegram("Teste de notificação do Jarvis! 🤖")
    st.toast("Enviado para Telegram e Browser.")


# =========================
# UI PRINCIPAL
# =========================
tab_chat, tab_agenda, tab_mem = st.tabs(["💬 Chat", "📌 Agenda", "🧠 Memória"])

with tab_agenda:
    if not tarefas:
        st.info("Agenda vazia.")
    for t in sorted(tarefas, key=lambda x: x["data_hora"]):
        cor = "🔴" if agora >= parse_dt(t["data_hora"]) else "🔵"
        if t["status"] == "silenciada": cor = "⚪"
        
        with st.expander(f"{cor} {t['data_hora'].split(' ')[1]} - {t['descricao']}"):
            c_a, c_b = st.columns(2)
            if c_a.button("✅ Feito", key=f"f_{t['id']}"):
                tarefas = [x for x in tarefas if x["id"] != t["id"]]
                salvar_tarefas(tarefas)
                update_summary_with_llm(f"Concluiu: {t['descricao']}")
                st.rerun()
            if c_b.button("💤 +30min", key=f"s_{t['id']}"):
                t["next_remind_at"] = format_dt(now_floor_minute() + timedelta(minutes=30))
                t["snoozed_until"] = t["next_remind_at"]
                t["remind_count"] = 0 # reseta ciclo
                salvar_tarefas(tarefas)
                st.rerun()

with tab_mem:
    st.text_area("Resumo Vivo", value=load_summary(), height=150)
    if st.button("Limpar Áudio Cache"):
        st.session_state.last_audio_bytes = None
        st.rerun()

with tab_chat:
    if mensagem_alerta:
        st.warning(mensagem_alerta)
        st.session_state.memoria.append({"role": "assistant", "content": mensagem_alerta})

    if st.session_state.last_audio_bytes:
        st.audio(st.session_state.last_audio_bytes, format="audio/mp3")

    for m in st.session_state.memoria[-10:]:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    # INPUT AREA
    st.write("---")
    c_mic, c_txt = st.columns([0.15, 0.85])
    texto_input = None
    usou_voz = False
    
    with c_txt:
        texto_input = st.chat_input("Digite algo...")
    with c_mic:
        audio_val = st.audio_input("🎙️")
        if audio_val:
            ah = hashlib.sha256(audio_val.getvalue()).hexdigest()
            if ah != st.session_state.ultimo_audio_hash:
                st.session_state.ultimo_audio_hash = ah
                texto_input = ouvir_audio(audio_val)
                usou_voz = True

# =========================
# LÓGICA DE RESPOSTA
# =========================
if texto_input and should_process_input(str(texto_input)):
    user_txt = str(texto_input).strip()
    st.session_state.memoria.append({"role": "user", "content": user_txt})
    add_event("chat_user", user_txt)
    
    # Processamento
    acao = decidir_acao(user_txt, tarefas)
    
    resp_txt = ""
    
    if acao["action"] == "TASK_CREATE":
        d = extrair_dados_tarefa(user_txt)
        if d:
            d = normalizar_tarefa(d)
            tarefas.append(d)
            salvar_tarefas(tarefas)
            resp_txt = f"Agendado: {d['descricao']} para {d['data_hora']}."
            add_event("task_create", resp_txt)
            update_summary_with_llm(f"Nova tarefa: {d['descricao']} @ {d['data_hora']}")
        else:
            resp_txt = "Não entendi a data/hora."

    elif acao["action"] == "WEB_SEARCH":
        q = acao.get("search_query") or user_txt
        res = buscar_tavily(q)
        prompt_web = f"Resultados: {res}\nPergunta: {user_txt}\nResponda direto."
        resp_txt = client.chat.completions.create(
            model=MODEL_ID, messages=[{"role": "user", "content": prompt_web}]
        ).choices[0].message.content
        add_event("web_search", f"Q: {q}")

    elif acao["action"] == "TASK_DONE":
        # Simplificação: marca a mais antiga atrasada ou a última criada
        if tarefas:
            removida = tarefas.pop(0)
            salvar_tarefas(tarefas)
            resp_txt = f"Feito: {removida['descricao']}."
        else:
            resp_txt = "Nada na agenda."

    else: # CHAT
        mems = search_memories(user_txt)
        ctx_mem = "\n".join([m[2] for m in mems])
        sys_prompt = f"Você é um assistente pessoal. User Info: {load_summary()}. Contexto: {ctx_mem}"
        msgs = [{"role": "system", "content": sys_prompt}] + st.session_state.memoria[-6:]
        resp_txt = client.chat.completions.create(
            model=MODEL_ID, messages=msgs
        ).choices[0].message.content
    
    # Finaliza
    st.session_state.memoria.append({"role": "assistant", "content": resp_txt})
    add_event("chat_assistant", resp_txt)
    
    if usou_voz:
        b = falar_bytes(resp_txt[:200]) # limita TTS para não ficar lento
        if b: st.session_state.last_audio_bytes = b
    
    st.rerun()

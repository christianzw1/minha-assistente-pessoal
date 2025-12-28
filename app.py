import streamlit as st
import streamlit.components.v1 as components
from groq import Groq
from tavily import TavilyClient
from streamlit_autorefresh import st_autorefresh

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

REMINDER_SCHEDULE_MIN = [0, 10, 30, 120]  # 0 = no horário; depois 10, 30, 120
QUIET_START = 22
QUIET_END = 7

AUTO_REFRESH_MS = 5_000  # 5s


# =========================
# CSS / UI POLISH
# =========================
def inject_css():
    # --- META TAGS PARA MODO "APP" (Mobile Fullscreen) ---
    st.markdown(
        """
        <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=0">
        <meta name="apple-mobile-web-app-capable" content="yes">
        <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
        """,
        unsafe_allow_html=True
    )

    # --- CSS AGRESSIVO ---
    st.markdown(
        """
        <style>
        /* Remove barras do Streamlit (cabeçalho, rodapé, menu) */
        header[data-testid="stHeader"] { display: none !important; }
        footer { display: none !important; }
        #MainMenu { display: none !important; }
        .stDeployButton { display: none !important; }
        [data-testid="stToolbar"] { display: none !important; }
        [data-testid="stDecoration"] { display: none !important; }
        [data-testid="stStatusWidget"] { display: none !important; }
        
        /* Remove o botão "Manage App" se possível (depende do host) */
        .viewerBadge_container__1QSob { display: none !important; }
        
        /* Ajuste de layout para Mobile (Remove padding excessivo) */
        .block-container {
            padding-top: 10px !important;
            padding-bottom: 60px !important;
            padding-left: 1rem !important;
            padding-right: 1rem !important;
            max-width: 100%;
        }

        /* Fundo estilo Cyberpunk/Dark Modern */
        [data-testid="stAppViewContainer"] {
            background: radial-gradient(1200px 600px at 10% 0%, rgba(12, 16, 33, 1), transparent 55%),
                        radial-gradient(1000px 500px at 90% 10%, rgba(0, 0, 0, 1), transparent 55%),
                        linear-gradient(180deg, #0e1117 0%, #000000 100%);
        }

        /* Cards mais limpos */
        .card {
            border: 1px solid rgba(255,255,255,0.05);
            background: rgba(20, 24, 35, 0.6);
            backdrop-filter: blur(10px);
            border-radius: 12px;
            padding: 14px;
            margin-bottom: 10px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
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

        /* Input de chat flutuante na parte inferior (estilo WhatsApp) */
        [data-testid="stChatInput"] {
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            padding: 1rem;
            background: rgba(14, 17, 23, 0.95);
            z-index: 999;
            border-top: 1px solid rgba(255,255,255,0.1);
        }
        
        /* Ajusta o botão de Audio Input para alinhar com o chat */
        [data-testid="stAudioInput"] {
             transform: scale(0.9);
        }

        /* Esconde barras de rolagem feias */
        ::-webkit-scrollbar {
            width: 4px;
            background: transparent;
        }
        ::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.1);
            border-radius: 4px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

# =========================
# CONEXÕES
# =========================
try:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
    tavily = TavilyClient(api_key=st.secrets["TAVILY_API_KEY"])
except Exception:
    st.error("⚠️ Erro nas chaves API. Verifique GROQ_API_KEY e TAVILY_API_KEY nos Secrets.")
    st.stop()


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
# UTILS TEMPO
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
# NOTIFICAÇÃO DO NAVEGADOR (JS)
# =========================
def request_notification_permission():
    components.html(
        """
        <script>
        (async function(){
          try {
            if (!('Notification' in window)) return;
            await Notification.requestPermission();
          } catch(e) {}
        })();
        </script>
        """,
        height=0,
    )

def browser_notify(title: str, body: str):
    payload_title = json.dumps(title)
    payload_body = json.dumps(body)
    components.html(
        f"""
        <script>
        (function() {{
          try {{
            if ('Notification' in window && Notification.permission === 'granted') {{
              new Notification({payload_title}, {{ body: {payload_body} }});
            }}
          }} catch(e) {{}}

          // Beep curto (pode falhar se o browser bloquear áudio sem gesto)
          try {{
            const AudioContext = window.AudioContext || window.webkitAudioContext;
            if (AudioContext) {{
              const ctx = new AudioContext();
              const o = ctx.createOscillator();
              const g = ctx.createGain();
              o.type = 'sine';
              o.frequency.value = 880;
              g.gain.value = 0.05;
              o.connect(g);
              g.connect(ctx.destination);
              o.start();
              setTimeout(() => {{
                o.stop();
                ctx.close();
              }}, 200);
            }}
          }} catch(e) {{}}
        }})();
        </script>
        """,
        height=0,
    )


# =========================
# MEMÓRIA LONGA (SQLite + FTS)
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
        CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events BEGIN
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
        rows = conn.execute("""
            SELECT e.ts, e.kind, e.content
            FROM events_fts f
            JOIN events e ON e.id = f.rowid
            WHERE events_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (query, limit)).fetchall()
    except Exception:
        rows = conn.execute("""
            SELECT ts, kind, content
            FROM events
            WHERE content LIKE ?
            ORDER BY id DESC
            LIMIT ?
        """, (f"%{query}%", limit)).fetchall()
    conn.close()
    return rows

def get_last_events(limit: int = 50):
    conn = db()
    rows = conn.execute("""
        SELECT ts, kind, content
        FROM events
        ORDER BY id DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return list(reversed(rows))

init_db()


# =========================
# RESUMO VIVO
# =========================
def load_summary() -> str:
    if not os.path.exists(SUMMARY_PATH):
        return "Resumo do usuário ainda vazio."
    try:
        return open(SUMMARY_PATH, "r", encoding="utf-8").read().strip() or "Resumo do usuário ainda vazio."
    except Exception:
        return "Resumo do usuário ainda vazio."

def save_summary(texto: str):
    texto = (texto or "").strip()
    if not texto:
        return
    with open(SUMMARY_PATH, "w", encoding="utf-8") as f:
        f.write(texto)

def update_summary_with_llm(new_info: str):
    new_info = (new_info or "").strip()
    if not new_info:
        return

    resumo_atual = load_summary()
    prompt = f"""
Você mantém um RESUMO VIVO do usuário e do contexto do projeto. Curto e útil.

Atualize o resumo com a informação nova, sem ficar grande demais.
- Preserve decisões, preferências, projetos, tarefas importantes, dados relevantes.
- Remova redundâncias.
- Máximo ~20 linhas.

RESUMO ATUAL:
{resumo_atual}

NOVA INFORMAÇÃO:
{new_info}

Devolva SOMENTE o resumo atualizado.
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
    s = re.sub(r"\s+", " ", s).strip()
    return s

def normalizar_tarefa(d: dict) -> dict:
    agora = now_floor_minute()
    d = dict(d)
    d.setdefault("id", str(uuid.uuid4())[:8])
    d.setdefault("status", "ativa")  # ativa | silenciada
    d.setdefault("remind_count", 0)
    d.setdefault("last_reminded_at", None)
    d.setdefault("snoozed_until", None)
    d.setdefault("created_at", format_dt(agora))
    d.setdefault("next_remind_at", d.get("data_hora"))
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

    if st.session_state.last_input_sig == sig and (now_ts - st.session_state.last_input_time) < 3.0:
        return False

    st.session_state.last_input_sig = sig
    st.session_state.last_input_time = now_ts
    return True


# =========================
# PARSER TAREFA
# =========================
def parse_relativo(texto: str):
    t = limpar_texto(texto)
    if "daqui um minuto" in t or "daqui 1 minuto" in t or "em 1 minuto" in t:
        return timedelta(minutes=1)
    m = re.search(r"(daqui\s+a|daqui|em)\s+(\d+)\s*(min|minuto|minutos|h|hora|horas)", t)
    if not m:
        return None
    n = int(m.group(2))
    unidade = m.group(3)
    if unidade.startswith("h") or "hora" in unidade:
        return timedelta(hours=n)
    return timedelta(minutes=n)

def ajustar_para_futuro(dt_extraido: datetime, agora: datetime) -> datetime:
    if dt_extraido >= agora:
        return dt_extraido
    tentativa = dt_extraido + timedelta(hours=12)
    if tentativa >= agora:
        return tentativa
    return dt_extraido + timedelta(days=1)

def extrair_descricao(texto: str) -> str:
    t = limpar_texto(texto)
    t = re.sub(r"^(me\s+lembra\s+de|me\s+lembra|lembra\s+de|me\s+avisa|avisa\s+me|me\s+cobra)\s+", "", t)
    t = re.sub(r"(daqui\s+a\s+\d+\s*(min|minuto|minutos|h|hora|horas)|daqui\s+um\s+minuto|em\s+\d+\s*(min|minuto|minutos|h|hora|horas))", "", t)
    t = re.sub(r"\b(por\s+favor|pfv|porfav(or)?)\b", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t if t else "tarefa"

def extrair_dados_tarefa(texto: str):
    agora = now_floor_minute()

    delta = parse_relativo(texto)
    if delta:
        dt = agora + delta
        return {"descricao": extrair_descricao(texto), "data_hora": format_dt(dt)}

    agora_fmt = format_dt(agora)
    prompt = (
        f"Agora é {agora_fmt} (America/Sao_Paulo).\n"
        f'O usuário disse: "{texto}".\n'
        "Extraia uma tarefa e uma data/hora no formato YYYY-MM-DD HH:MM.\n"
        "Se o usuário citar apenas hora/minuto, use a data de hoje.\n"
        "Escolha o PRÓXIMO horário futuro possível.\n"
        "Responda APENAS em JSON: {\"descricao\": \"...\", \"data_hora\": \"YYYY-MM-DD HH:MM\"}"
    )

    try:
        resp = client.chat.completions.create(
            model=MODEL_ID,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0
        )
        data = json.loads(resp.choices[0].message.content)
        if "descricao" not in data or "data_hora" not in data:
            return None
        dt_extraido = parse_dt(data["data_hora"])
        dt_ok = ajustar_para_futuro(dt_extraido, agora)
        data["data_hora"] = format_dt(dt_ok)
        data["descricao"] = (data["descricao"] or "").strip() or extrair_descricao(texto)
        return data
    except Exception:
        return None


# =========================
# ROUTER (regras + LLM)
# =========================
TIME_SENSITIVE_HINTS = ["agora", "hoje", "últimas", "ultimas", "atual", "atualmente", "neste momento", "ao vivo", "recentemente", "essa semana", "esse mês", "este mês"]
FACT_QUERIES = ["cotação", "cotacao", "preço", "preco", "valor", "quanto ta", "quanto tá", "quanto está",
                "taxa", "selic", "dólar", "dolar", "euro", "inflação", "inflacao",
                "clima", "tempo", "previsão", "previsao",
                "placar", "resultado", "quem ganhou", "tabela", "classificação", "classificacao",
                "notícia", "noticia", "bitcoin", "btc", "ethereum", "eth", "cripto", "criptomoeda"]
QUESTION_WORDS = ["qual", "quanto", "quando", "onde", "quem", "por que", "porque", "como"]

def parece_pergunta_factual(texto: str) -> bool:
    t = limpar_texto(texto)
    if any(h in t for h in TIME_SENSITIVE_HINTS):
        return True
    if any(w in t for w in QUESTION_WORDS) and any(k in t for k in FACT_QUERIES):
        return True
    if re.search(r"\b(top\s*\d+|ranking|melhor(es)?|pior(es)?|compar(a|e))\b", t):
        return True
    return False

def router_llm(texto: str, tarefas: list) -> dict:
    agora = format_dt(now_floor_minute())
    resumo = "\n".join([f"- {i}: {t.get('descricao','')} @ {t.get('data_hora','')}" for i, t in enumerate(tarefas)]) or "(vazio)"

    prompt = f"""
Você é um roteador de intenções para um app de assistente pessoal.
Agora é {agora} (America/Sao_Paulo).

Tarefas atuais:
{resumo}

Mensagem do usuário:
\"\"\"{texto}\"\"\"

Regras:
1) lembrar/agendar => TASK_CREATE
2) já fiz/feito/terminei/desconsidera porque já fiz => TASK_DONE
3) adiar/mais tarde/daqui X min => TASK_SNOOZE (minutes)
4) para de lembrar/cancelar/silenciar lembrete (sem dizer que fez) => TASK_SILENCE
5) pergunta factual atual (cotação, notícia, clima, placar...) => WEB_SEARCH + search_query
6) senão => CHAT

Responda APENAS JSON:
{{
  "action":"TASK_CREATE|TASK_DONE|TASK_SNOOZE|TASK_SILENCE|WEB_SEARCH|CHAT",
  "task_index":-1,
  "minutes":0,
  "search_query":""
}}
""".strip()

    try:
        resp = client.chat.completions.create(
            model=MODEL_ID,
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        raw = (resp.choices[0].message.content or "").strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(m.group(0)) if m else {}
        return {
            "action": data.get("action", "CHAT"),
            "task_index": int(data.get("task_index", -1) or -1),
            "minutes": int(data.get("minutes", 0) or 0),
            "search_query": data.get("search_query", "") or "",
        }
    except Exception:
        return {"action": "CHAT", "task_index": -1, "minutes": 0, "search_query": ""}

def decidir_acao(texto: str, tarefas: list) -> dict:
    t = limpar_texto(texto)

    if t.startswith("/web "):
        return {"action": "WEB_SEARCH", "search_query": str(texto)[5:].strip(), "task_index": -1, "minutes": 0}
    if t.startswith("/chat "):
        return {"action": "CHAT", "search_query": "", "task_index": -1, "minutes": 0}
    if t.startswith("/resumo"):
        return {"action": "SHOW_SUMMARY", "search_query": "", "task_index": -1, "minutes": 0}
    if t.startswith("/memoria "):
        return {"action": "SHOW_MEMORY", "search_query": str(texto)[9:].strip(), "task_index": -1, "minutes": 0}

    if re.search(r"^(me\s+lembra|lembra\s+de|me\s+avisa|avisa\s+me|me\s+cobra)", t):
        return {"action": "TASK_CREATE", "search_query": "", "task_index": -1, "minutes": 0}

    if any(x in t for x in ["adiar", "mais tarde", "snooze", "me lembra em", "daqui a"]):
        mins = 30
        mm = re.search(r"(\d+)\s*(min|minuto|minutos|h|hora|horas)", t)
        if mm:
            mins = int(mm.group(1))
            if "h" in mm.group(2) or "hora" in mm.group(2):
                mins *= 60
        return {"action": "TASK_SNOOZE", "search_query": "", "task_index": -1, "minutes": mins}

    done_terms = ["já fiz", "ja fiz", "feito", "terminei", "conclui", "finalizei", "resolvi"]
    if any(x in t for x in done_terms):
        return {"action": "TASK_DONE", "search_query": "", "task_index": -1, "minutes": 0}

    silence_terms = ["desconsidera", "cancela", "cancelar", "para de lembrar", "pare de lembrar", "não me lembra", "nao me lembra", "silencia"]
    if any(x in t for x in silence_terms):
        return {"action": "TASK_SILENCE", "search_query": "", "task_index": -1, "minutes": 0}

    if parece_pergunta_factual(texto):
        return {"action": "WEB_SEARCH", "search_query": str(texto).strip(), "task_index": -1, "minutes": 0}

    return router_llm(texto, tarefas)


# =========================
# ESCOLHER TAREFA
# =========================
def escolher_tarefa(texto_usuario: str, tarefas: list) -> int:
    if not tarefas:
        return -1
    if len(tarefas) == 1:
        return 0

    agora = now_floor_minute()
    atrasadas = []
    for i, t in enumerate(tarefas):
        try:
            if t.get("status") == "silenciada":
                continue
            if agora >= parse_dt(t["data_hora"]):
                atrasadas.append(i)
        except Exception:
            pass
    if len(atrasadas) == 1:
        return atrasadas[0]

    linhas = [f"ID {i}: {t.get('descricao','')} @ {t.get('data_hora','')}" for i, t in enumerate(tarefas)]
    prompt = (
        "Lista de tarefas:\n" + "\n".join(linhas) + "\n\n"
        f'O usuário disse: "{texto_usuario}"\n'
        "Qual ID ele quer afetar? Responda SÓ o número. Se nenhuma, -1."
    )
    try:
        resp = client.chat.completions.create(
            model=MODEL_ID,
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        out = (resp.choices[0].message.content or "").strip()
        m = re.search(r"-?\d+", out)
        return int(m.group()) if m else -1
    except Exception:
        return -1


# =========================
# WEB / ÁUDIO
# =========================
def buscar_tavily(q: str):
    try:
        r = tavily.search(query=q, max_results=3)
        if not r.get("results"):
            return None
        partes = []
        for item in r["results"][:3]:
            partes.append(
                f"TÍTULO: {item.get('title','')}\n"
                f"URL: {item.get('url','')}\n"
                f"TRECHO: {item.get('content','')}"
            )
        return "\n\n---\n\n".join(partes)
    except Exception:
        return None

def ouvir_audio(uploaded_file):
    try:
        b = uploaded_file.getvalue()
        return client.audio.transcriptions.create(
            file=("audio.wav", b, "audio/wav"),
            model="whisper-large-v3",
            response_format="text",
            language="pt"
        )
    except Exception:
        return None

async def falar_async(texto: str, out_path: str):
    await edge_tts.Communicate(texto, "pt-BR-FranciscaNeural").save(out_path)
    return out_path

def falar_bytes(texto: str):
    try:
        out_path = f"tts_{uuid.uuid4().hex[:10]}.mp3"
        try:
            mp3_path = asyncio.run(falar_async(texto, out_path))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                mp3_path = loop.run_until_complete(falar_async(texto, out_path))
            finally:
                loop.close()

        b = open(mp3_path, "rb").read()
        try:
            os.remove(mp3_path)
        except Exception:
            pass
        return b
    except Exception:
        return None


# =========================
# PROMPT COM MEMÓRIA
# =========================
def build_chat_messages(user_text: str):
    resumo = load_summary()
    mems = search_memories(user_text, limit=8)
    mem_txt = "\n".join([f"[{ts} | {kind}] {content}" for ts, kind, content in mems]) or "(nenhuma)"
    last_turns = st.session_state.memoria[-25:] if st.session_state.memoria else []

    msgs = [
        {"role": "system", "content": "Você é uma assistente útil, direta e amigável. Responda em pt-BR."},
        {"role": "system", "content": f"MEMÓRIA (resumo vivo):\n{resumo}"},
        {"role": "system", "content": f"MEMÓRIAS RELEVANTES DO PASSADO:\n{mem_txt}"},
    ]
    msgs += [{"role": m["role"], "content": m["content"]} for m in last_turns if str(m.get("content", "")).strip()]
    msgs.append({"role": "user", "content": user_text})
    return msgs


# =========================
# VIGIA PROATIVO
# =========================
def compute_next_remind_at(agora: datetime, t: dict) -> Optional[datetime]:
    if t.get("status") == "silenciada":
        return None

    if t.get("snoozed_until"):
        try:
            su = parse_dt(t["snoozed_until"])
            if su >= agora:
                return su
        except Exception:
            pass

    try:
        nr = parse_dt(t.get("next_remind_at") or t["data_hora"])
        return nr
    except Exception:
        return None

def schedule_after_fire(agora: datetime, t: dict) -> dict:
    t = dict(t)
    t["last_reminded_at"] = format_dt(agora)
    t["remind_count"] = int(t.get("remind_count", 0) or 0) + 1
    t["snoozed_until"] = None

    if t["remind_count"] >= len(REMINDER_SCHEDULE_MIN):
        t["status"] = "silenciada"
        t["next_remind_at"] = format_dt(agora + timedelta(days=365))
    else:
        mins = REMINDER_SCHEDULE_MIN[t["remind_count"]]
        t["next_remind_at"] = format_dt(agora + timedelta(minutes=mins))
    return t

def pick_due_task(tarefas: list, agora: datetime) -> Optional[dict]:
    if em_horario_silencioso(agora):
        return None

    candidates: List[Tuple[float, dict]] = []
    for t in tarefas:
        if t.get("status") == "silenciada":
            continue
        try:
            due = parse_dt(t["data_hora"])
        except Exception:
            continue

        if agora < due:
            continue

        nr = compute_next_remind_at(agora, t)
        if nr is None:
            continue
        if agora >= nr:
            atraso_min = (agora - due).total_seconds() / 60.0
            candidates.append((atraso_min, t))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


# =========================
# AUTOREFRESH
# =========================
st_autorefresh(interval=AUTO_REFRESH_MS, key="tick")


# =========================
# LOAD TAREFAS
# =========================
agora = now_floor_minute()
tarefas = [normalizar_tarefa(t) for t in carregar_tarefas()]
salvar_tarefas(tarefas)


# =========================
# PROACTIVE ALERT
# =========================
mensagem_alerta = None
tarefa_alertada = pick_due_task(tarefas, agora)

if tarefa_alertada:
    next_at_str = tarefa_alertada.get("next_remind_at") or tarefa_alertada.get("data_hora")
    fingerprint = f"{tarefa_alertada['id']}::{next_at_str}"

    if st.session_state.last_alert_fingerprint != fingerprint:
        st.session_state.last_alert_fingerprint = fingerprint

        mensagem_alerta = (
            f"🔔 **Lembrete:** {tarefa_alertada['descricao']}\n\n"
            f"Horário: **{tarefa_alertada['data_hora']}**\n\n"
            "Você pode responder: **feito**, **adiar 30 min**, **silenciar**."
        )

        browser_notify("Lembrete", tarefa_alertada["descricao"])

        b = falar_bytes("Ei! Você tem um lembrete.")
        if b:
            st.session_state.last_audio_bytes = b

        updated = schedule_after_fire(agora, tarefa_alertada)
        tarefas2 = []
        for x in tarefas:
            tarefas2.append(updated if x.get("id") == tarefa_alertada["id"] else x)
        tarefas = tarefas2
        salvar_tarefas(tarefas)

        add_event("task", f"LEMBRETE: {tarefa_alertada['descricao']}", meta=json.dumps(updated, ensure_ascii=False))


# =========================
# HEADER (bonito)
# =========================
top = st.container()
with top:
    c1, c2 = st.columns([0.72, 0.28])
    with c1:
        st.markdown(
            """
            <div class="card">
              <div class="card-title">🤖 Assistente Pessoal (Full Control)</div>
              <div class="muted">Agenda + memória + web + voz • tudo em um lugar</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f"""
            <div class="card">
              <div class="card-title">🕒 {agora.strftime('%H:%M')}</div>
              <div class="muted">Auto-refresh: {AUTO_REFRESH_MS//1000}s • BR</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.write("")
    cc1, cc2, cc3 = st.columns([0.35, 0.35, 0.30])
    with cc1:
        if st.button("🔔 Ativar notificações", use_container_width=True):
            request_notification_permission()
            st.toast("Permissão solicitada. Se o navegador perguntar, toque em **Permitir**.", icon="✅")
    with cc2:
        if em_horario_silencioso(agora):
            st.info("Modo silencioso (22:00–07:00)")
        else:
            st.success("Alertas ativos")
    with cc3:
        if st.button("🧠 Ver resumo rápido", use_container_width=True):
            st.toast(load_summary()[:180] + ("..." if len(load_summary()) > 180 else ""), icon="🧠")


st.write("")


# =========================
# TABS: Chat / Agenda / Memória
# =========================
tab_chat, tab_agenda, tab_mem = st.tabs(["💬 Chat", "📌 Agenda", "🧠 Memória"])


# =========================
# TAB: AGENDA
# =========================
with tab_agenda:
    st.markdown("<div class='card-title'>📌 Sua agenda</div>", unsafe_allow_html=True)
    st.write("")

    if not tarefas:
        st.markdown("<div class='card'>✅ <b>Livre!</b> Nenhuma tarefa agendada.</div>", unsafe_allow_html=True)
    else:
        def sort_key(x):
            try:
                return parse_dt(x["data_hora"])
            except Exception:
                return datetime.max.replace(tzinfo=FUSO_BR)

        for t in sorted(tarefas, key=sort_key):
            try:
                dt = parse_dt(t["data_hora"])
                atrasada = agora >= dt
            except Exception:
                atrasada = False

            status = t.get("status", "ativa")
            badge = "🔕" if status == "silenciada" else ("🔥" if atrasada else "📌")
            hora = t.get("data_hora", "??").split(" ")[1] if " " in t.get("data_hora", "") else t.get("data_hora", "??")

            st.markdown(
                f"""
                <div class="card">
                  <div class="card-title">{badge} {hora} — {t.get('descricao','(sem descrição)')}</div>
                  <div class="muted">Status: {status} • Lembretes: {t.get('remind_count',0)}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            b1, b2, _ = st.columns([0.22, 0.22, 0.56])
            with b1:
                if st.button("✅ Feito", key=f"feito_{t['id']}", use_container_width=True):
                    tarefas2 = [x for x in tarefas if x.get("id") != t.get("id")]
                    salvar_tarefas(tarefas2)
                    add_event("task", f"CONCLUÍDA (botão): {t['descricao']}", meta=json.dumps(t, ensure_ascii=False))
                    update_summary_with_llm(f"Tarefa concluída: {t['descricao']}")
                    st.rerun()
            with b2:
                if st.button("🔕 Silenciar", key=f"sil_{t['id']}", use_container_width=True):
                    for x in tarefas:
                        if x.get("id") == t.get("id"):
                            x["status"] = "silenciada"
                            x["next_remind_at"] = format_dt(now_floor_minute() + timedelta(days=365))
                    salvar_tarefas(tarefas)
                    add_event("task", f"SILENCIADA (botão): {t['descricao']}", meta=json.dumps(t, ensure_ascii=False))
                    st.rerun()

            st.write("")


# =========================
# TAB: MEMÓRIA
# =========================
with tab_mem:
    st.markdown("<div class='card-title'>🧠 Memória</div>", unsafe_allow_html=True)
    st.write("")

    with st.expander("📄 Resumo vivo", expanded=True):
        st.markdown(f"<div class='card'>{load_summary()}</div>", unsafe_allow_html=True)

    with st.expander("🧪 Debug (opcional)", expanded=False):
        if st.button("Últimos 20 eventos"):
            evs = get_last_events(20)
            st.write([{"ts": a, "kind": b, "content": c[:160]} for a, b, c in evs])
        if st.button("Limpar áudio"):
            st.session_state.last_audio_bytes = None
            st.rerun()


# =========================
# TAB: CHAT
# =========================
with tab_chat:
    if mensagem_alerta:
        st.warning(mensagem_alerta)
        st.toast(f"Lembrete: {tarefa_alertada['descricao']}", icon="🔔")
        st.session_state.memoria.append({"role": "assistant", "content": mensagem_alerta})

    if st.session_state.last_audio_bytes:
        st.audio(st.session_state.last_audio_bytes, format="audio/mp3")

    # histórico do chat
    for m in st.session_state.memoria:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    st.divider()

    # INPUT (texto + voz)
    c1, c2 = st.columns([0.18, 0.82])
    texto = None
    usou_voz = False

    with c2:
        texto = st.chat_input("Mensagem...")

    with c1:
        up = st.audio_input("🎙️")
        if up is not None:
            try:
                b = up.getvalue()
                h = hashlib.sha256(b).hexdigest()
                if h != st.session_state.ultimo_audio_hash:
                    st.session_state.ultimo_audio_hash = h
                    with st.spinner("Transcrevendo..."):
                        texto = ouvir_audio(up)
                        usou_voz = True
            except Exception:
                pass


# =========================
# PROCESSAMENTO
# =========================
if texto and should_process_input(str(texto)):
    user_text = str(texto).strip()

    st.session_state.memoria.append({"role": "user", "content": user_text})
    add_event("chat_user", user_text)

    tarefas = [normalizar_tarefa(t) for t in carregar_tarefas()]
    salvar_tarefas(tarefas)

    acao = decidir_acao(user_text, tarefas)

    if acao["action"] == "SHOW_SUMMARY":
        resumo = load_summary()
        st.session_state.memoria.append({"role": "assistant", "content": resumo})
        add_event("chat_assistant", resumo)
        st.rerun()

    if acao["action"] == "SHOW_MEMORY":
        q = (acao.get("search_query") or "").strip()
        rows = search_memories(q, limit=10)
        if not rows:
            out = "Não encontrei memórias relevantes pra isso."
        else:
            out = "### Memórias encontradas:\n" + "\n".join([f"- **{ts}** ({kind}) — {c[:220]}" for ts, kind, c in rows])
        st.session_state.memoria.append({"role": "assistant", "content": out})
        add_event("chat_assistant", out)
        st.rerun()

    # TASK_CREATE
    if acao["action"] == "TASK_CREATE":
        d = extrair_dados_tarefa(user_text)
        if d:
            d = normalizar_tarefa(d)
            d["next_remind_at"] = d["data_hora"]
            tarefas.append(d)
            salvar_tarefas(tarefas)

            msg = f"📌 Agendado: **{d['descricao']}** às **{d['data_hora'].split(' ')[1]}**."
            st.session_state.memoria.append({"role": "assistant", "content": msg})

            add_event("task", f"CRIADA: {d['descricao']} @ {d['data_hora']}", meta=json.dumps(d, ensure_ascii=False))
            update_summary_with_llm(f"Nova tarefa agendada: {d['descricao']} às {d['data_hora']}.")

            if usou_voz:
                b = falar_bytes("Agendado.")
                if b:
                    st.session_state.last_audio_bytes = b

            st.rerun()
        else:
            msg = "Não consegui entender o horário. Ex: “me lembra de X às 19:19” ou “daqui 10 min”."
            st.session_state.memoria.append({"role": "assistant", "content": msg})
            add_event("chat_assistant", msg)
            st.rerun()

    # TASK_DONE / TASK_SILENCE / TASK_SNOOZE
    elif acao["action"] in ["TASK_DONE", "TASK_SILENCE", "TASK_SNOOZE"]:
        if not tarefas:
            msg = "Sua agenda já está vazia!"
            st.session_state.memoria.append({"role": "assistant", "content": msg})
            add_event("chat_assistant", msg)
            st.rerun()
        else:
            idx = escolher_tarefa(user_text, tarefas)
            if idx == -1 and isinstance(acao.get("task_index", None), int):
                if 0 <= acao["task_index"] < len(tarefas):
                    idx = acao["task_index"]

            if idx == -1 or idx >= len(tarefas):
                msg = "Não consegui identificar qual tarefa você quis afetar."
                st.session_state.memoria.append({"role": "assistant", "content": msg})
                add_event("chat_assistant", msg)
                st.rerun()
            else:
                if acao["action"] == "TASK_DONE":
                    removida = tarefas.pop(idx)
                    salvar_tarefas(tarefas)
                    msg = f"✅ Marquei como feito: **{removida['descricao']}**."
                    st.session_state.memoria.append({"role": "assistant", "content": msg})
                    add_event("task", f"CONCLUÍDA: {removida['descricao']}", meta=json.dumps(removida, ensure_ascii=False))
                    update_summary_with_llm(f"Tarefa concluída: {removida['descricao']}.")
                    st.rerun()

                elif acao["action"] == "TASK_SILENCE":
                    agora2 = now_floor_minute()
                    tarefas[idx]["status"] = "silenciada"
                    tarefas[idx]["next_remind_at"] = format_dt(agora2 + timedelta(days=365))
                    salvar_tarefas(tarefas)
                    msg = f"🔕 Parei de te lembrar: **{tarefas[idx]['descricao']}**."
                    st.session_state.memoria.append({"role": "assistant", "content": msg})
                    add_event("task", f"SILENCIADA: {tarefas[idx]['descricao']}", meta=json.dumps(tarefas[idx], ensure_ascii=False))
                    st.rerun()

                elif acao["action"] == "TASK_SNOOZE":
                    mins = int(acao.get("minutes", 30) or 30)
                    mins = max(1, min(mins, 24 * 60))
                    agora2 = now_floor_minute()
                    snooze_until = agora2 + timedelta(minutes=mins)
                    tarefas[idx]["snoozed_until"] = format_dt(snooze_until)
                    tarefas[idx]["next_remind_at"] = tarefas[idx]["snoozed_until"]
                    salvar_tarefas(tarefas)

                    msg = f"⏳ Adiei por {mins} min: **{tarefas[idx]['descricao']}**."
                    st.session_state.memoria.append({"role": "assistant", "content": msg})
                    add_event("task", f"ADIADA {mins}min: {tarefas[idx]['descricao']}", meta=json.dumps(tarefas[idx], ensure_ascii=False))
                    st.rerun()

    # WEB_SEARCH
    elif acao["action"] == "WEB_SEARCH":
        q = (acao.get("search_query") or user_text).strip()
        web = buscar_tavily(q)

        if web:
            add_event("web", f"QUERY: {q}\n\n{web[:2500]}")
            prompt = (
                "Você recebeu resultados de busca (podem ter ruído).\n"
                "Responda em pt-BR com objetividade.\n"
                "- Se for cotação/preço/valor: traga número e unidade.\n"
                "- Se não houver número confiável: diga que não encontrou valor exato.\n"
                "- Cite a fonte pelo domínio ou título se possível.\n\n"
                f"RESULTADOS:\n{web}\n\nPERGUNTA:\n{user_text}"
            )
            resp = client.chat.completions.create(
                model=MODEL_ID,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2
            ).choices[0].message.content

            st.session_state.memoria.append({"role": "assistant", "content": resp})
            add_event("chat_assistant", resp)

            if usou_voz:
                b = falar_bytes(resp[:220])
                if b:
                    st.session_state.last_audio_bytes = b
            st.rerun()
        else:
            msg = "Não consegui puxar dados da internet agora (busca vazia)."
            st.session_state.memoria.append({"role": "assistant", "content": msg})
            add_event("chat_assistant", msg)
            st.rerun()

    # CHAT
    else:
        msgs = build_chat_messages(user_text)
        resp = client.chat.completions.create(
            model=MODEL_ID,
            messages=msgs,
            temperature=0.4
        ).choices[0].message.content

        st.session_state.memoria.append({"role": "assistant", "content": resp})
        add_event("chat_assistant", resp)

        if any(k in limpar_texto(user_text) for k in ["meu objetivo", "quero", "preciso", "decidi", "daqui pra frente", "sempre", "nunca"]):
            update_summary_with_llm(f"Usuário disse: {user_text}\nAssistente respondeu: {resp[:400]}")

        if usou_voz:
            b = falar_bytes(resp[:220])
            if b:
                st.session_state.last_audio_bytes = b

        st.rerun()

import streamlit as st
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


# =========================
# CONFIG
# =========================
st.set_page_config(page_title="Assistente Pessoal (Full Control)", page_icon="🤖", layout="wide")

MODEL_ID = "llama-3.3-70b-versatile"
ARQUIVO_TAREFAS = "tarefas.json"

FUSO_BR = ZoneInfo("America/Sao_Paulo")
DB_PATH = "jarvis_memory.db"
SUMMARY_PATH = "summary.txt"

REMINDER_SCHEDULE_MIN = [0, 10, 30, 120]  # lembretes naturais por tarefa após vencer

QUIET_START = 22
QUIET_END = 7

# =========================
# ESTILO CLEAN (CSS)
# =========================
st.markdown(
    """
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}

.block-container {max-width: 1150px; padding-top: 2.0rem; padding-bottom: 2rem;}
.stApp {
  background: radial-gradient(1200px 600px at 20% 0%, rgba(99,102,241,0.15), transparent 55%),
              radial-gradient(900px 500px at 80% 10%, rgba(16,185,129,0.12), transparent 60%),
              #0b0f19;
  color: rgba(255,255,255,0.92);
}

h1, h2, h3 {letter-spacing: -0.02em;}
h1 {font-size: 2.1rem !important; margin-bottom: 0.8rem !important;}

[data-testid="stVerticalBlockBorderWrapper"]{
  background: rgba(255,255,255,0.04);
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 16px;
}

[data-testid="stChatMessage"] { padding: 0.2rem 0; }
[data-testid="stChatMessage"] > div {
  border-radius: 16px;
  border: 1px solid rgba(255,255,255,0.08);
  background: rgba(255,255,255,0.04);
}
[data-testid="stChatMessage"]:has([aria-label="user"]) > div {
  background: rgba(99,102,241,0.14);
  border-color: rgba(99,102,241,0.30);
}
[data-testid="stChatMessage"]:has([aria-label="assistant"]) > div {
  background: rgba(255,255,255,0.04);
}

.stButton>button {
  border-radius: 12px;
  border: 1px solid rgba(255,255,255,0.16);
  background: rgba(255,255,255,0.06);
  color: rgba(255,255,255,0.92);
  padding: 0.45rem 0.8rem;
}
.stButton>button:hover {
  border-color: rgba(255,255,255,0.24);
  background: rgba(255,255,255,0.10);
}

[data-testid="stChatInput"] textarea {
  border-radius: 14px !important;
  border: 1px solid rgba(255,255,255,0.14) !important;
  background: rgba(255,255,255,0.05) !important;
}

[data-testid="stAlert"] {
  border-radius: 14px;
  border: 1px solid rgba(255,255,255,0.10);
  background: rgba(255,255,255,0.04);
}
</style>
""",
    unsafe_allow_html=True
)

st.title("Assistente Pessoal (Full Control)")


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
if "last_alert_sig" not in st.session_state:
    st.session_state.last_alert_sig = None
if "last_input_sig" not in st.session_state:
    st.session_state.last_input_sig = None
if "last_input_time" not in st.session_state:
    st.session_state.last_input_time = 0.0

# áudio persistente + pausa do refresh
if "last_audio_bytes" not in st.session_state:
    st.session_state.last_audio_bytes = None
if "audio_hold_until" not in st.session_state:
    st.session_state.audio_hold_until = 0.0


# =========================
# UTILS TEMPO
# =========================
def now_br() -> datetime:
    return datetime.now(FUSO_BR)

def format_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")

def parse_dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=FUSO_BR)

def em_horario_silencioso(agora: datetime) -> bool:
    h = agora.hour
    return (h >= QUIET_START) or (h < QUIET_END)


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
    # FTS5: se não existir, cria
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
    rows = []
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
        # fallback sem FTS
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
            messages=[{"role":"user","content":prompt}],
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
    with open(ARQUIVO_TAREFAS, "w", encoding="utf-8") as f:
        json.dump(lista, f, ensure_ascii=False, indent=2)

def limpar_texto(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9áàâãéèêíìîóòôõúùûç\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def normalizar_tarefa(d: dict) -> dict:
    agora = now_br()
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
# DETECTOR FACTUAL (WEB)
# =========================
TIME_SENSITIVE_HINTS = [
    "agora", "hoje", "últimas", "ultimas", "atual", "atualmente",
    "neste momento", "ao vivo", "recentemente", "essa semana", "esse mês", "este mês"
]
FACT_QUERIES = [
    "cotação", "cotacao", "preço", "preco", "valor", "quanto ta", "quanto tá", "quanto está",
    "taxa", "selic", "dólar", "dolar", "euro", "inflação", "inflacao",
    "clima", "tempo", "previsão", "previsao",
    "placar", "resultado", "quem ganhou", "tabela", "classificação", "classificacao",
    "notícia", "noticia",
    "bitcoin", "btc", "ethereum", "eth", "cripto", "criptomoeda",
]
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
    if dt_extraido > agora + timedelta(seconds=5):
        return dt_extraido
    tentativa = dt_extraido + timedelta(hours=12)
    if tentativa > agora + timedelta(seconds=5):
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
    agora = now_br()

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
        data["data_hora"] = format_dt(ajustar_para_futuro(dt_extraido, agora))
        data["descricao"] = (data["descricao"] or "").strip() or extrair_descricao(texto)
        return data
    except Exception:
        return None


# =========================
# ROUTER (regras + LLM)
# =========================
def router_llm(texto: str, tarefas: list) -> dict:
    agora = format_dt(now_br())
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
  "search_query":"",
  "confidence":0.0
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
            "confidence": float(data.get("confidence", 0.0) or 0.0),
        }
    except Exception:
        return {"action": "CHAT", "task_index": -1, "minutes": 0, "search_query": "", "confidence": 0.0}

def decidir_acao(texto: str, tarefas: list) -> dict:
    t = limpar_texto(texto)

    if t.startswith("/web "):
        return {"action": "WEB_SEARCH", "search_query": str(texto)[5:].strip(), "task_index": -1, "minutes": 0, "confidence": 1.0}
    if t.startswith("/chat "):
        return {"action": "CHAT", "search_query": "", "task_index": -1, "minutes": 0, "confidence": 1.0}
    if t.startswith("/resumo"):
        return {"action":"SHOW_SUMMARY","search_query":"","task_index":-1,"minutes":0,"confidence":1.0}
    if t.startswith("/memoria "):
        return {"action":"SHOW_MEMORY","search_query":str(texto)[9:].strip(),"task_index":-1,"minutes":0,"confidence":1.0}

    if re.search(r"^(me\s+lembra|lembra\s+de|me\s+avisa|avisa\s+me|me\s+cobra)", t):
        return {"action": "TASK_CREATE", "search_query": "", "task_index": -1, "minutes": 0, "confidence": 1.0}

    if any(x in t for x in ["adiar", "mais tarde", "snooze", "me lembra em", "daqui a"]):
        mins = 30
        mm = re.search(r"(\d+)\s*(min|minuto|minutos|h|hora|horas)", t)
        if mm:
            mins = int(mm.group(1))
            if "h" in mm.group(2) or "hora" in mm.group(2):
                mins *= 60
        return {"action": "TASK_SNOOZE", "search_query": "", "task_index": -1, "minutes": mins, "confidence": 0.95}

    done_terms = ["já fiz", "ja fiz", "feito", "terminei", "conclui", "finalizei", "resolvi", "já abri", "ja abri", "já fechei", "ja fechei"]
    if any(x in t for x in done_terms):
        return {"action": "TASK_DONE", "search_query": "", "task_index": -1, "minutes": 0, "confidence": 0.95}

    silence_terms = ["desconsidera", "cancela", "cancelar", "para de lembrar", "pare de lembrar", "não me lembra", "nao me lembra", "silencia"]
    if any(x in t for x in silence_terms):
        return {"action": "TASK_SILENCE", "search_query": "", "task_index": -1, "minutes": 0, "confidence": 0.9}

    if parece_pergunta_factual(texto):
        return {"action": "WEB_SEARCH", "search_query": str(texto).strip(), "task_index": -1, "minutes": 0, "confidence": 0.85}

    return router_llm(texto, tarefas)


# =========================
# ESCOLHER TAREFA
# =========================
def escolher_tarefa(texto_usuario: str, tarefas: list) -> int:
    if not tarefas:
        return -1
    if len(tarefas) == 1:
        return 0

    agora = now_br()
    atrasadas = []
    for i, t in enumerate(tarefas):
        try:
            if t.get("status") == "silenciada":
                continue
            if agora > parse_dt(t["data_hora"]):
                atrasadas.append(i)
        except Exception:
            pass
    if len(atrasadas) == 1:
        return atrasadas[0]

    texto = limpar_texto(texto_usuario)
    tokens = ["hl", "hr", "ahl", "dpr", "rib"]
    for tok in tokens:
        if tok in texto:
            for i, t in enumerate(tarefas):
                if tok in limpar_texto(t.get("descricao", "")):
                    return i

    linhas = [f"ID {i}: {t.get('descricao','')} @ {t.get('data_hora','')}" for i, t in enumerate(tarefas)]
    prompt = (
        "Lista de tarefas:\n" + "\n".join(linhas) + "\n\n"
        f'O usuário disse: "{texto_usuario}"\n'
        "Qual ID ele quer afetar? Responda SÓ o número. Se nenhuma, -1."
    )
    try:
        resp = client.chat.completions.create(model=MODEL_ID, messages=[{"role": "user", "content": prompt}], temperature=0)
        out = (resp.choices[0].message.content or "").strip()
        m = re.search(r"-?\d+", out)
        return int(m.group()) if m else -1
    except Exception:
        return -1


# =========================
# WEB / AUDIO
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

def falar_bytes(texto: str, hold_seconds: int = 25):
    """
    Gera MP3 em arquivo único, lê bytes e segura o autorefresh por hold_seconds.
    """
    try:
        # arquivo único pra não brigar com rerun
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
        # guarda em memória da sessão
        st.session_state.last_audio_bytes = b
        st.session_state.audio_hold_until = time.time() + float(hold_seconds)

        # limpeza best-effort (não quebra se falhar)
        try:
            os.remove(mp3_path)
        except Exception:
            pass

        return b
    except Exception:
        return None


# =========================
# PROMPT COM MEMÓRIA (segundo cérebro)
# =========================
def build_chat_messages(user_text: str):
    resumo = load_summary()
    mems = search_memories(user_text, limit=8)
    mem_txt = "\n".join([f"[{ts} | {kind}] {content}" for ts, kind, content in mems]) or "(nenhuma)"

    last_turns = st.session_state.memoria[-25:] if st.session_state.memoria else []

    msgs = [
        {"role":"system","content":"Você é uma assistente útil, direta e amigável. Responda em pt-BR."},
        {"role":"system","content":f"MEMÓRIA (resumo vivo):\n{resumo}"},
        {"role":"system","content":f"MEMÓRIAS RELEVANTES DO PASSADO:\n{mem_txt}"},
    ]
    msgs += [{"role":m["role"], "content":m["content"]} for m in last_turns if str(m.get("content","")).strip()]
    msgs.append({"role":"user","content":user_text})
    return msgs


# =========================
# VIGIA NATURAL
# =========================
def proximo_lembrete(agora: datetime, remind_count: int):
    if remind_count >= len(REMINDER_SCHEDULE_MIN):
        return None
    return agora + timedelta(minutes=REMINDER_SCHEDULE_MIN[remind_count])

tarefas = [normalizar_tarefa(t) for t in carregar_tarefas()]
salvar_tarefas(tarefas)

agora = now_br()
mensagem_alerta = None
tarefa_alertada_id = None

if tarefas and (not em_horario_silencioso(agora)):
    for t in tarefas:
        try:
            if t.get("status") == "silenciada":
                continue

            dt_tarefa = parse_dt(t["data_hora"])
            if agora <= dt_tarefa:
                continue

            if t.get("snoozed_until"):
                if agora < parse_dt(t["snoozed_until"]):
                    continue

            next_at = parse_dt(t.get("next_remind_at", t["data_hora"]))
            if agora < next_at:
                continue

            mensagem_alerta = (
                f"🔔 Ei! Já passou do horário de **{t['descricao']}**.\n\n"
                "Responda:\n"
                "- **feito** / “já fiz”\n"
                "- **adiar 30 min**\n"
                "- **desconsidera / para de lembrar**"
            )
            tarefa_alertada_id = t["id"]

            t["last_reminded_at"] = format_dt(agora)
            t["remind_count"] = int(t.get("remind_count", 0)) + 1

            prox = proximo_lembrete(agora, t["remind_count"])
            if prox is None:
                t["status"] = "silenciada"
                t["next_remind_at"] = format_dt(agora + timedelta(days=365))
            else:
                t["next_remind_at"] = format_dt(prox)

            salvar_tarefas(tarefas)
            add_event("task", f"LEMBRETE: {t['descricao']} (vencida)", meta=json.dumps(t, ensure_ascii=False))
            break
        except Exception:
            pass


# =========================
# REFRESH CONTROLADO (PAUSA DURANTE ÁUDIO)
# =========================
now_ts = time.time()
if now_ts > st.session_state.audio_hold_until:
    st_autorefresh(interval=10_000, key="auto_refresh")
else:
    # sem refresh agora: deixa o áudio tocar
    pass


# =========================
# UI (2 colunas)
# =========================
col_main, col_agenda = st.columns([0.7, 0.3], gap="large")

with col_agenda:
    st.subheader("📌 Agenda")

    # utilitários de debug e memória
    with st.expander("🧠 Memória (debug)", expanded=False):
        if st.button("Mostrar resumo vivo"):
            st.info(load_summary())
        if st.button("Mostrar últimos 20 eventos"):
            evs = get_last_events(20)
            st.write([{"ts":a,"kind":b,"content":c[:120]} for a,b,c in evs])
        if st.button("Limpar áudio atual"):
            st.session_state.last_audio_bytes = None
            st.session_state.audio_hold_until = 0.0
            st.rerun()

    if tarefas:
        for t in tarefas:
            try:
                atrasada = agora > parse_dt(t["data_hora"])
            except Exception:
                atrasada = False

            icone = "🔕" if t.get("status") == "silenciada" else ("🔥" if atrasada else "📅")
            st.info(f"{icone} **{t['data_hora'].split(' ')[1]}** — {t['descricao']}")

            b1, b2 = st.columns([0.5, 0.5])
            with b1:
                if st.button("Feito", key=f"feito_{t['id']}"):
                    tarefas2 = [x for x in tarefas if x.get("id") != t.get("id")]
                    salvar_tarefas(tarefas2)
                    add_event("task", f"CONCLUÍDA (botão): {t['descricao']}", meta=json.dumps(t, ensure_ascii=False))
                    update_summary_with_llm(f"Tarefa concluída: {t['descricao']}")
                    st.rerun()
            with b2:
                if st.button("Silenciar", key=f"sil_{t['id']}"):
                    agora2 = now_br()
                    for x in tarefas:
                        if x.get("id") == t.get("id"):
                            x["status"] = "silenciada"
                            x["next_remind_at"] = format_dt(agora2 + timedelta(days=365))
                    salvar_tarefas(tarefas)
                    add_event("task", f"SILENCIADA (botão): {t['descricao']}", meta=json.dumps(t, ensure_ascii=False))
                    st.rerun()
    else:
        st.success("Livre!")

with col_main:
    # alerta proativo (com áudio persistente)
    if mensagem_alerta:
        st.warning(mensagem_alerta)
        st.session_state.memoria.append({"role": "assistant", "content": mensagem_alerta})

        sig = f"{tarefa_alertada_id}:{mensagem_alerta}"
        if st.session_state.last_alert_sig != sig:
            st.session_state.last_alert_sig = sig
            b = falar_bytes("Ei! Sua tarefa já passou do horário.", hold_seconds=18)
            if b:
                st.audio(b, format="audio/mp3", autoplay=True)

    # render do áudio persistido (não some nos reruns)
    if st.session_state.last_audio_bytes:
        st.audio(st.session_state.last_audio_bytes, format="audio/mp3", autoplay=False)

    # histórico
    for m in st.session_state.memoria:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    st.divider()

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

        # registra na memória curta + longa
        st.session_state.memoria.append({"role": "user", "content": user_text})
        add_event("chat_user", user_text)

        with st.chat_message("user"):
            st.markdown(user_text)

        # recarrega tarefas
        tarefas = [normalizar_tarefa(t) for t in carregar_tarefas()]
        salvar_tarefas(tarefas)

        with st.chat_message("assistant"):
            acao = decidir_acao(user_text, tarefas)

            # comandos de debug
            if acao["action"] == "SHOW_SUMMARY":
                resumo = load_summary()
                st.markdown(resumo)
                st.session_state.memoria.append({"role":"assistant","content":resumo})
                add_event("chat_assistant", resumo)
                st.stop()

            if acao["action"] == "SHOW_MEMORY":
                q = (acao.get("search_query") or "").strip()
                rows = search_memories(q, limit=10)
                if not rows:
                    out = "Não encontrei memórias relevantes pra isso."
                else:
                    out = "### Memórias encontradas:\n" + "\n".join([f"- **{ts}** ({kind}) — {c[:220]}" for ts,kind,c in rows])
                st.markdown(out)
                st.session_state.memoria.append({"role":"assistant","content":out})
                add_event("chat_assistant", out)
                st.stop()

            # TASK_CREATE
            if acao["action"] == "TASK_CREATE":
                d = extrair_dados_tarefa(user_text)
                if d:
                    d = normalizar_tarefa(d)
                    tarefas.append(d)
                    salvar_tarefas(tarefas)

                    msg = f"📌 Agendado: **{d['descricao']}** às **{d['data_hora'].split(' ')[1]}**."
                    st.success(msg)
                    st.session_state.memoria.append({"role": "assistant", "content": msg})

                    add_event("task", f"CRIADA: {d['descricao']} @ {d['data_hora']}", meta=json.dumps(d, ensure_ascii=False))
                    update_summary_with_llm(f"Nova tarefa agendada: {d['descricao']} às {d['data_hora']}.")

                    if usou_voz:
                        b = falar_bytes("Agendado.", hold_seconds=10)
                        if b:
                            st.audio(b, format="audio/mp3", autoplay=True)

                    st.rerun()
                else:
                    msg = "Não consegui entender o horário. Ex: “me lembra de X às 19:19” ou “daqui 10 min”."
                    st.warning(msg)
                    st.session_state.memoria.append({"role": "assistant", "content": msg})
                    add_event("chat_assistant", msg)

            # TASK_DONE / TASK_SILENCE / TASK_SNOOZE
            elif acao["action"] in ["TASK_DONE", "TASK_SILENCE", "TASK_SNOOZE"]:
                if not tarefas:
                    msg = "Sua agenda já está vazia!"
                    st.info(msg)
                    st.session_state.memoria.append({"role": "assistant", "content": msg})
                    add_event("chat_assistant", msg)
                else:
                    idx = escolher_tarefa(user_text, tarefas)
                    if idx == -1 and isinstance(acao.get("task_index", None), int):
                        if 0 <= acao["task_index"] < len(tarefas):
                            idx = acao["task_index"]

                    if idx == -1 or idx >= len(tarefas):
                        msg = "Não consegui identificar qual tarefa você quis afetar."
                        st.warning(msg)
                        st.session_state.memoria.append({"role": "assistant", "content": msg})
                        add_event("chat_assistant", msg)
                    else:
                        if acao["action"] == "TASK_DONE":
                            removida = tarefas.pop(idx)
                            salvar_tarefas(tarefas)
                            msg = f"✅ Marquei como feito: **{removida['descricao']}**."
                            st.success(msg)
                            st.session_state.memoria.append({"role": "assistant", "content": msg})

                            add_event("task", f"CONCLUÍDA: {removida['descricao']}", meta=json.dumps(removida, ensure_ascii=False))
                            update_summary_with_llm(f"Tarefa concluída: {removida['descricao']}.")

                            if usou_voz:
                                b = falar_bytes("Tarefa concluída.", hold_seconds=10)
                                if b:
                                    st.audio(b, format="audio/mp3", autoplay=True)

                            st.rerun()

                        elif acao["action"] == "TASK_SILENCE":
                            agora2 = now_br()
                            tarefas[idx]["status"] = "silenciada"
                            tarefas[idx]["next_remind_at"] = format_dt(agora2 + timedelta(days=365))
                            salvar_tarefas(tarefas)
                            msg = f"🔕 Beleza. Parei de te lembrar: **{tarefas[idx]['descricao']}**."
                            st.success(msg)
                            st.session_state.memoria.append({"role": "assistant", "content": msg})

                            add_event("task", f"SILENCIADA: {tarefas[idx]['descricao']}", meta=json.dumps(tarefas[idx], ensure_ascii=False))

                            st.rerun()

                        elif acao["action"] == "TASK_SNOOZE":
                            mins = int(acao.get("minutes", 30) or 30)
                            mins = max(1, min(mins, 24 * 60))
                            agora2 = now_br()
                            snooze_until = agora2 + timedelta(minutes=mins)
                            tarefas[idx]["snoozed_until"] = format_dt(snooze_until)
                            tarefas[idx]["next_remind_at"] = tarefas[idx]["snoozed_until"]
                            salvar_tarefas(tarefas)
                            msg = f"⏳ Adiei por {mins} min: **{tarefas[idx]['descricao']}**."
                            st.success(msg)
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

                    st.markdown(resp)
                    st.session_state.memoria.append({"role": "assistant", "content": resp})
                    add_event("chat_assistant", resp)

                    # resumo vivo (só se for informação relevante de vida/decisão)
                    if any(k in limpar_texto(user_text) for k in ["meta", "objetivo", "decidi", "vou", "preciso", "projeto"]):
                        update_summary_with_llm(f"O usuário pesquisou: {user_text}. Resposta: {resp[:400]}")

                    if usou_voz:
                        b = falar_bytes(resp[:220], hold_seconds=18)
                        if b:
                            st.audio(b, format="audio/mp3", autoplay=True)
                else:
                    msg = "Não consegui puxar dados da internet agora (busca vazia)."
                    st.warning(msg)
                    st.session_state.memoria.append({"role": "assistant", "content": msg})
                    add_event("chat_assistant", msg)

            # CHAT (com memória inteligente)
            else:
                msgs = build_chat_messages(user_text)

                resp = client.chat.completions.create(
                    model=MODEL_ID,
                    messages=msgs,
                    temperature=0.4
                ).choices[0].message.content

                st.markdown(resp)
                st.session_state.memoria.append({"role": "assistant", "content": resp})
                add_event("chat_assistant", resp)

                # Atualiza resumo vivo de forma parcimoniosa (gatilhos)
                if any(k in limpar_texto(user_text) for k in ["meu objetivo", "quero", "preciso", "decidi", "daqui pra frente", "sempre", "nunca"]):
                    update_summary_with_llm(f"Usuário disse: {user_text}\nAssistente respondeu: {resp[:400]}")

                if usou_voz:
                    b = falar_bytes(resp[:220], hold_seconds=18)
                    if b:
                        st.audio(b, format="audio/mp3", autoplay=True)

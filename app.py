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
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


# =========================
# CONFIG
# =========================
st.set_page_config(page_title="Assistente Pessoal (Full Control)", page_icon="🤖")
st.title("Assistente Pessoal (Full Control)")

MODEL_ID = "llama-3.3-70b-versatile"
ARQUIVO_TAREFAS = "tarefas.json"
FUSO_BR = ZoneInfo("America/Sao_Paulo")

# lembretes naturais por tarefa (após vencer)
REMINDER_SCHEDULE_MIN = [0, 10, 30, 120]  # depois disso: silencia

# horário silencioso (sem alertas proativos)
QUIET_START = 22
QUIET_END = 7

# refresh leve e seguro (substitui time.sleep + rerun)
st_autorefresh(interval=10_000, key="auto_refresh")  # 10s


# =========================
# CONEXÕES
# =========================
try:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
    tavily = TavilyClient(api_key=st.secrets["TAVILY_API_KEY"])
except Exception:
    st.error("⚠️ Erro nas Chaves API. Verifique os Secrets (GROQ_API_KEY e TAVILY_API_KEY).")
    st.stop()


# =========================
# SESSION STATE
# =========================
if "memoria" not in st.session_state:
    st.session_state.memoria = []
if "ultimo_audio_hash" not in st.session_state:
    st.session_state.ultimo_audio_hash = None
if "last_alert_sig" not in st.session_state:
    st.session_state.last_alert_sig = None  # evita repetir áudio por refresh


# =========================
# STORAGE / UTILS
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


def parse_dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=FUSO_BR)


def format_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


def now_br() -> datetime:
    return datetime.now(FUSO_BR)


def em_horario_silencioso(agora: datetime) -> bool:
    h = agora.hour
    return (h >= QUIET_START) or (h < QUIET_END)


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

    # next_remind_at: primeiro alerta é no horário da tarefa
    d.setdefault("next_remind_at", d.get("data_hora"))
    return d


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
    "iphone", "ps5", "steam", "preço do", "preco do",
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
# PARSER RELATIVO + AJUSTE FUTURO
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
    # se já é futuro, ok
    if dt_extraido > agora + timedelta(seconds=5):
        return dt_extraido

    # tenta +12h (resolve 07:19 vs 19:19)
    tentativa = dt_extraido + timedelta(hours=12)
    if tentativa.hour <= 23 and tentativa > agora + timedelta(seconds=5):
        return tentativa

    # senão joga pro dia seguinte
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

    # 1) relativo (sem LLM)
    delta = parse_relativo(texto)
    if delta:
        dt = agora + delta
        return {"descricao": extrair_descricao(texto), "data_hora": format_dt(dt)}

    # 2) LLM para data/hora explícita
    agora_fmt = format_dt(agora)
    prompt = (
        f"Agora é {agora_fmt} (America/Sao_Paulo).\n"
        f'O usuário disse: "{texto}".\n'
        "Extraia uma tarefa e uma data/hora no formato YYYY-MM-DD HH:MM.\n"
        "Se o usuário citar apenas hora/minuto, use a data de hoje.\n"
        "IMPORTANTE: escolha o PRÓXIMO horário futuro possível.\n"
        "Responda APENAS em JSON: "
        "{\"descricao\": \"...\", \"data_hora\": \"YYYY-MM-DD HH:MM\"}"
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
        dt_corrigido = ajustar_para_futuro(dt_extraido, agora)
        data["data_hora"] = format_dt(dt_corrigido)
        data["descricao"] = data["descricao"].strip() or extrair_descricao(texto)
        return data
    except Exception:
        return None


# =========================
# ROUTER (2 camadas: regras + LLM)
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

Regras IMPORTANTES:
1) Se pedir lembrar/agendar ("me lembra", "lembra de", "anota", "agenda", "daqui X min/h", "às HH:MM"), action = TASK_CREATE.
2) Se disser que já fez/terminou/feito/concluiu ou "desconsidera porque já fiz", action = TASK_DONE.
3) Se pedir adiar ("adiar", "mais tarde", "daqui X min"), action = TASK_SNOOZE (minutes).
4) Se pedir parar de lembrar/cancelar/silenciar lembrete (sem dizer que fez), action = TASK_SILENCE.
5) Se exigir informação atual/precisa do mundo (cotação, notícia, placar, clima, preços), action = WEB_SEARCH e gere search_query curta e objetiva.
6) Caso contrário, action = CHAT.

Responda APENAS com JSON:
{{
  "action": "TASK_CREATE|TASK_DONE|TASK_SNOOZE|TASK_SILENCE|WEB_SEARCH|CHAT",
  "task_index": -1,
  "minutes": 0,
  "search_query": "",
  "confidence": 0.0,
  "notes": ""
}}
"""

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
            "notes": data.get("notes", "")
        }
    except Exception:
        return {"action": "CHAT", "task_index": -1, "minutes": 0, "search_query": "", "confidence": 0.0, "notes": "router_error"}


def decidir_acao(texto: str, tarefas: list) -> dict:
    t = limpar_texto(texto)

    # comandos explícitos (forçam ação)
    if t.startswith("/web "):
        return {"action": "WEB_SEARCH", "search_query": texto[5:].strip(), "task_index": -1, "minutes": 0, "confidence": 1.0, "notes": "forced_web"}
    if t.startswith("/chat "):
        return {"action": "CHAT", "search_query": "", "task_index": -1, "minutes": 0, "confidence": 1.0, "notes": "forced_chat"}

    # 1) regras determinísticas (mais importantes)
    if re.search(r"^(me\s+lembra|lembra\s+de|me\s+avisa|avisa\s+me|me\s+cobra)", t):
        return {"action": "TASK_CREATE", "search_query": "", "task_index": -1, "minutes": 0, "confidence": 1.0, "notes": "prefix_lembra"}

    if any(x in t for x in ["adiar", "mais tarde", "snooze", "me lembra em", "daqui a"]):
        mins = 30
        mm = re.search(r"(\d+)\s*(min|minuto|minutos|h|hora|horas)", t)
        if mm:
            mins = int(mm.group(1))
            if "h" in mm.group(2) or "hora" in mm.group(2):
                mins *= 60
        return {"action": "TASK_SNOOZE", "search_query": "", "task_index": -1, "minutes": mins, "confidence": 0.95, "notes": "heur_snooze"}

    done_terms = ["já fiz", "ja fiz", "feito", "terminei", "conclui", "finalizei", "resolvi", "já abri", "ja abri", "já fechei", "ja fechei"]
    if any(x in t for x in done_terms):
        return {"action": "TASK_DONE", "search_query": "", "task_index": -1, "minutes": 0, "confidence": 0.95, "notes": "heur_done"}

    silence_terms = ["desconsidera", "cancela", "cancelar", "para de lembrar", "pare de lembrar", "não me lembra", "nao me lembra", "silencia"]
    if any(x in t for x in silence_terms):
        # se veio junto com “já fiz”, acima já teria pegado
        return {"action": "TASK_SILENCE", "search_query": "", "task_index": -1, "minutes": 0, "confidence": 0.9, "notes": "heur_silence"}

    # 2) detector factual: força WEB quando precisar
    if parece_pergunta_factual(texto):
        return {"action": "WEB_SEARCH", "search_query": texto.strip(), "task_index": -1, "minutes": 0, "confidence": 0.85, "notes": "factual_detector"}

    # 3) casos ambíguos: LLM router
    return router_llm(texto, tarefas)


# =========================
# ESCOLHA DE TAREFA
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

    # fallback LLM
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
# WEB / AUDIO
# =========================
def buscar_tavily(q: str):
    try:
        r = tavily.search(query=q, max_results=3)
        if not r.get("results"):
            return None
        # junta 2-3 snippets pra dar contexto
        partes = []
        for item in r["results"][:3]:
            titulo = item.get("title", "")
            url = item.get("url", "")
            content = item.get("content", "")
            partes.append(f"TÍTULO: {titulo}\nURL: {url}\nTRECHO: {content}")
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


async def falar_async(texto: str):
    await edge_tts.Communicate(texto, "pt-BR-FranciscaNeural").save("alerta.mp3")
    return "alerta.mp3"


def falar(texto: str):
    # wrapper seguro
    try:
        return asyncio.run(falar_async(texto))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(falar_async(texto))
        finally:
            loop.close()


# =========================
# VIGIA NATURAL (PROATIVO)
# =========================
def proximo_lembrete(agora: datetime, remind_count: int):
    if remind_count >= len(REMINDER_SCHEDULE_MIN):
        return None
    return agora + timedelta(minutes=REMINDER_SCHEDULE_MIN[remind_count])


# carrega/normaliza tarefas sempre
tarefas = [normalizar_tarefa(t) for t in carregar_tarefas()]
salvar_tarefas(tarefas)

agora = now_br()
mensagem_alerta = None
tarefa_alertada_id = None

if not em_horario_silencioso(agora):
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

            # dispara lembrete
            mensagem_alerta = (
                f"🔔 Ei! Já passou do horário de **{t['descricao']}**.\n\n"
                "Responda:\n"
                "- **feito** / “já fiz”\n"
                "- **adiar 30 min**\n"
                "- **desconsidera / para de lembrar**"
            )
            tarefa_alertada_id = t["id"]

            # atualiza estado da tarefa
            t["last_reminded_at"] = format_dt(agora)
            t["remind_count"] = int(t.get("remind_count", 0)) + 1

            prox = proximo_lembrete(agora, t["remind_count"])
            if prox is None:
                t["status"] = "silenciada"
                t["next_remind_at"] = format_dt(agora + timedelta(days=365))
            else:
                t["next_remind_at"] = format_dt(prox)

            salvar_tarefas(tarefas)
            break
        except Exception:
            pass

if mensagem_alerta:
    st.warning(mensagem_alerta)
    st.session_state.memoria.append({"role": "assistant", "content": mensagem_alerta})

    # áudio só uma vez por alerta
    sig = f"{tarefa_alertada_id}:{mensagem_alerta}"
    if st.session_state.last_alert_sig != sig:
        st.session_state.last_alert_sig = sig
        try:
            mp3 = falar("Ei! Sua tarefa já passou do horário.")
            st.audio(mp3, format="audio/mp3", autoplay=True)
        except Exception:
            pass


# =========================
# UI LAYOUT
# =========================
col_main, col_agenda = st.columns([0.7, 0.3])

with col_agenda:
    st.subheader("📌 Agenda")

    if tarefas:
        for t in tarefas:
            try:
                atrasada = agora > parse_dt(t["data_hora"])
            except Exception:
                atrasada = False

            icone = "🔕" if t.get("status") == "silenciada" else ("🔥" if atrasada else "📅")
            st.warning(f"{icone} {t['data_hora'].split(' ')[1]}\n{t['descricao']}")

            b1, b2 = st.columns([0.5, 0.5])
            with b1:
                if st.button("Feito", key=f"feito_{t['id']}"):
                    tarefas2 = [x for x in tarefas if x.get("id") != t.get("id")]
                    salvar_tarefas(tarefas2)
                    st.rerun()
            with b2:
                if st.button("Silenciar", key=f"sil_{t['id']}"):
                    agora2 = now_br()
                    for x in tarefas:
                        if x.get("id") == t.get("id"):
                            x["status"] = "silenciada"
                            x["next_remind_at"] = format_dt(agora2 + timedelta(days=365))
                    salvar_tarefas(tarefas)
                    st.rerun()
    else:
        st.success("Livre!")


with col_main:
    container = st.container()
    with container:
        for m in st.session_state.memoria:
            with st.chat_message(m["role"]):
                st.markdown(m["content"])

    st.divider()

    c1, c2 = st.columns([0.15, 0.85])
    texto = None
    usou_voz = False

    with c2:
        if t := st.chat_input("Mensagem..."):
            texto = t

    with c1:
        up = st.audio_input("🎙️")
        if up is not None:
            try:
                b = up.getvalue()
                h = hash(b)
                if h != st.session_state.ultimo_audio_hash:
                    st.session_state.ultimo_audio_hash = h
                    with st.spinner("Transcrevendo..."):
                        texto = ouvir_audio(up)
                        usou_voz = True
            except Exception:
                pass

    if texto:
        # registra user
        st.session_state.memoria.append({"role": "user", "content": str(texto)})
        with container.chat_message("user"):
            st.markdown(str(texto))

        # recarrega tarefas atualizadas
        tarefas = [normalizar_tarefa(t) for t in carregar_tarefas()]
        salvar_tarefas(tarefas)

        with container.chat_message("assistant"):
            acao = decidir_acao(str(texto), tarefas)

            # ------------- TASK_CREATE -------------
            if acao["action"] == "TASK_CREATE":
                d = extrair_dados_tarefa(str(texto))
                if d:
                    d = normalizar_tarefa(d)
                    tarefas.append(d)
                    salvar_tarefas(tarefas)

                    msg = f"📌 Agendado: **{d['descricao']}** às **{d['data_hora'].split(' ')[1]}**."
                    st.success(msg)
                    st.session_state.memoria.append({"role": "assistant", "content": msg})
                    st.rerun()
                else:
                    msg = "Não consegui entender o horário. Ex: “me lembra de X às 19:19” ou “daqui 10 min”."
                    st.warning(msg)
                    st.session_state.memoria.append({"role": "assistant", "content": msg})

            # ------------- TASK_DONE / SILENCE / SNOOZE -------------
            elif acao["action"] in ["TASK_DONE", "TASK_SILENCE", "TASK_SNOOZE"]:
                if not tarefas:
                    msg = "Sua agenda já está vazia!"
                    st.info(msg)
                    st.session_state.memoria.append({"role": "assistant", "content": msg})
                else:
                    idx = escolher_tarefa(str(texto), tarefas)
                    if idx == -1 and isinstance(acao.get("task_index", None), int):
                        if 0 <= acao["task_index"] < len(tarefas):
                            idx = acao["task_index"]

                    if idx == -1 or idx >= len(tarefas):
                        msg = "Não consegui identificar qual tarefa você quis afetar."
                        st.warning(msg)
                        st.session_state.memoria.append({"role": "assistant", "content": msg})
                    else:
                        if acao["action"] == "TASK_DONE":
                            removida = tarefas.pop(idx)
                            salvar_tarefas(tarefas)

                            msg = f"✅ Marquei como feito: **{removida['descricao']}**."
                            st.success(msg)
                            st.session_state.memoria.append({"role": "assistant", "content": msg})
                            st.rerun()

                        elif acao["action"] == "TASK_SILENCE":
                            agora2 = now_br()
                            tarefas[idx]["status"] = "silenciada"
                            tarefas[idx]["next_remind_at"] = format_dt(agora2 + timedelta(days=365))
                            salvar_tarefas(tarefas)

                            msg = f"🔕 Beleza. Parei de te lembrar: **{tarefas[idx]['descricao']}**."
                            st.success(msg)
                            st.session_state.memoria.append({"role": "assistant", "content": msg})
                            st.rerun()

                        elif acao["action"] == "TASK_SNOOZE":
                            mins = int(acao.get("minutes", 30) or 30)
                            mins = max(1, min(mins, 24 * 60))  # trava

                            agora2 = now_br()
                            snooze_until = agora2 + timedelta(minutes=mins)
                            tarefas[idx]["snoozed_until"] = format_dt(snooze_until)
                            tarefas[idx]["next_remind_at"] = tarefas[idx]["snoozed_until"]
                            salvar_tarefas(tarefas)

                            msg = f"⏳ Adiei por {mins} min: **{tarefas[idx]['descricao']}**."
                            st.success(msg)
                            st.session_state.memoria.append({"role": "assistant", "content": msg})
                            st.rerun()

            # ------------- WEB_SEARCH -------------
            elif acao["action"] == "WEB_SEARCH":
                q = (acao.get("search_query") or str(texto)).strip()
                web = buscar_tavily(q)

                if web:
                    prompt = (
                        "Você recebeu resultados de busca (podem ter ruído).\n"
                        "Responda em pt-BR com objetividade.\n"
                        "- Se for cotação/preço/valor: traga o número, unidade e contexto.\n"
                        "- Se não houver número confiável: diga que não encontrou um valor exato.\n"
                        "- Cite rapidamente a fonte pelo título ou domínio se aparecer.\n\n"
                        f"RESULTADOS:\n{web}\n\nPERGUNTA:\n{texto}"
                    )
                    resp = client.chat.completions.create(
                        model=MODEL_ID,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.2
                    ).choices[0].message.content

                    st.markdown(resp)
                    st.session_state.memoria.append({"role": "assistant", "content": resp})

                    if usou_voz:
                        try:
                            mp3 = falar(resp[:220])
                            st.audio(mp3, format="audio/mp3", autoplay=True)
                        except Exception:
                            pass
                else:
                    msg = "Não consegui puxar dados da internet agora (busca vazia)."
                    st.warning(msg)
                    st.session_state.memoria.append({"role": "assistant", "content": msg})

            # ------------- CHAT -------------
            else:
                msgs = [{"role": "system", "content": "Você é uma assistente útil, direta e amigável. Responda em pt-BR."}]
                for m in st.session_state.memoria:
                    c = m.get("content", "")
                    if isinstance(c, str) and c.strip():
                        msgs.append({"role": m["role"], "content": c})

                resp = client.chat.completions.create(
                    model=MODEL_ID,
                    messages=msgs,
                    temperature=0.4
                ).choices[0].message.content

                st.markdown(resp)
                st.session_state.memoria.append({"role": "assistant", "content": resp})

                if usou_voz:
                    try:
                        mp3 = falar(resp[:220])
                        st.audio(mp3, format="audio/mp3", autoplay=True)
                    except Exception:
                        pass

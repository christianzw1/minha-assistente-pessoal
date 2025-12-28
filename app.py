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
st.set_page_config(page_title="Jarvis V3", page_icon="🤖")
st.title("Assistente Pessoal (Full Control)")

MODEL_ID = "llama-3.3-70b-versatile"
ARQUIVO_TAREFAS = "tarefas.json"
FUSO_BR = ZoneInfo("America/Sao_Paulo")

# lembrete natural: na hora, 10min, 30min, 2h e depois silencia
REMINDER_SCHEDULE_MIN = [0, 10, 30, 120]

# não perturbar
QUIET_START = 22
QUIET_END = 7

# refresh a cada 10s, sem loop infinito
st_autorefresh(interval=10_000, key="auto_refresh")


# =========================
# CONEXÕES
# =========================
try:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
    tavily = TavilyClient(api_key=st.secrets["TAVILY_API_KEY"])
except Exception:
    st.error("⚠️ Erro nas Chaves API. Verifique os Secrets.")
    st.stop()


# =========================
# SESSION STATE
# =========================
if "memoria_v3" not in st.session_state:
    st.session_state.memoria_v3 = []
if "ultimo_audio" not in st.session_state:
    st.session_state.ultimo_audio = None


# =========================
# STORAGE / UTILS
# =========================
def carregar_tarefas():
    if not os.path.exists(ARQUIVO_TAREFAS):
        return []
    try:
        with open(ARQUIVO_TAREFAS, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def salvar_tarefas(lista):
    with open(ARQUIVO_TAREFAS, "w", encoding="utf-8") as f:
        json.dump(lista, f, ensure_ascii=False, indent=2)


def parse_dt(s: str):
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=FUSO_BR)


def format_dt(dt: datetime):
    return dt.strftime("%Y-%m-%d %H:%M")


def em_horario_silencioso(agora: datetime):
    h = agora.hour
    return (h >= QUIET_START) or (h < QUIET_END)


def normalizar_tarefa(d: dict):
    """Garante campos que controlam lembrete por tarefa (não global)."""
    agora = datetime.now(FUSO_BR)
    d = dict(d)

    d.setdefault("id", str(uuid.uuid4())[:8])
    d.setdefault("status", "ativa")              # ativa | silenciada
    d.setdefault("remind_count", 0)             # quantas vezes já lembrou
    d.setdefault("last_reminded_at", None)      # "YYYY-MM-DD HH:MM"
    d.setdefault("snoozed_until", None)         # "YYYY-MM-DD HH:MM"
    d.setdefault("created_at", format_dt(agora))
    d.setdefault("next_remind_at", d.get("data_hora"))  # primeiro lembrete na hora da tarefa

    return d


def extrair_minutos(texto: str, padrao=30):
    m = re.search(r"(\d+)\s*(min|mins|minuto|minutos|h|hora|horas)", texto.lower())
    if not m:
        return padrao
    n = int(m.group(1))
    unidade = m.group(2)
    if unidade.startswith("h") or "hora" in unidade:
        return n * 60
    return n


def limpar_texto(s: str):
    s = s.lower()
    s = re.sub(r"[^a-z0-9áàâãéèêíìîóòôõúùûç\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# =========================
# INTENÇÕES
# =========================
def identificar_intencao(texto):
    t = texto.lower()

    # CONCLUIR
    termos_concluir = [
        "já fiz", "ja fiz", "feito", "conclu", "termin", "resolvi",
        "já abri", "ja abri", "já fechei", "ja fechei", "finalizei",
        "pode parar", "pode cancelar"
    ]
    if any(x in t for x in termos_concluir):
        return "CONCLUIR"

    # SILENCIAR (não remove, só para de cobrar)
    termos_silenciar = [
        "desconsidera", "cancela", "cancelar", "para de", "pare de",
        "não me lembra", "nao me lembra", "silencia", "chega"
    ]
    if any(x in t for x in termos_silenciar):
        return "SILENCIAR"

    # ADIAR
    termos_adiar = ["adiar", "mais tarde", "daqui a", "me lembra em", "snooze"]
    if any(x in t for x in termos_adiar):
        return "ADIAR"

    # AGENDAR
    termos_agenda = ["lembr", "agend", "anot", "marc", "cobr", "avis"]
    if any(x in t for x in termos_agenda):
        return "AGENDAR"

    # BUSCAR
    if any(x in t for x in ["hoje", "preço", "notícia", "valor", "dólar", "tempo", "quem ganhou"]):
        return "BUSCAR"

    return "RESPONDER"


# =========================
# HELPERS: escolher tarefa (sem depender 100% do LLM)
# =========================
def escolher_tarefa_por_heuristica(texto_usuario: str, tarefas: list):
    """
    Regras:
    1) Se só existe 1 tarefa -> escolhe ela.
    2) Se só existe 1 tarefa atrasada ativa -> escolhe ela.
    3) Se achar palavra-chave (ex: HL/HR/AHL/DPR) presente na descrição -> escolhe.
    4) Se nada disso, retorna -1 (aí usamos LLM como último recurso).
    """
    if not tarefas:
        return -1

    if len(tarefas) == 1:
        return 0

    agora = datetime.now(FUSO_BR)

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

    # tenta match por token forte
    texto_limpo = limpar_texto(texto_usuario)
    tokens_fortes = ["hl", "hr", "ahl", "dpr", "rib"]
    for tok in tokens_fortes:
        if tok in texto_limpo:
            for i, t in enumerate(tarefas):
                if tok in limpar_texto(t.get("descricao", "")):
                    return i

    return -1


def encontrar_tarefa_por_llm(texto_usuario: str, tarefas: list):
    """Último recurso: LLM escolhe o índice."""
    linhas = []
    for i, t in enumerate(tarefas):
        linhas.append(f"ID {i}: {t.get('descricao','')} (em {t.get('data_hora','')})")
    lista_texto = "\n".join(linhas)

    prompt = f"""
Lista de tarefas:
{lista_texto}

O usuário disse: "{texto_usuario}"

Qual é o ID da tarefa que ele quer afetar (concluir/silenciar/adiar)?
Responda APENAS o número (ex: 0). Se nenhuma bater, responda -1.
"""

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


def escolher_tarefa(texto_usuario: str, tarefas: list):
    idx = escolher_tarefa_por_heuristica(texto_usuario, tarefas)
    if idx != -1:
        return idx
    return encontrar_tarefa_por_llm(texto_usuario, tarefas)


# =========================
# EXTRATORES
# =========================
def extrair_dados_tarefa(texto):
    agora_br = datetime.now(FUSO_BR).strftime("%Y-%m-%d %H:%M")
    prompt = (
        f"Hoje é {agora_br}. O usuário disse: \"{texto}\".\n"
        "Extraia uma tarefa e uma data/hora no formato YYYY-MM-DD HH:MM.\n"
        "Se não tiver hora, use 18:00 de hoje.\n"
        "Responda APENAS em JSON assim:\n"
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
        if "descricao" in data and "data_hora" in data:
            return data
        return None
    except Exception:
        return None


def buscar_tavily(q):
    try:
        r = tavily.search(query=q, max_results=2)
        return r["results"][0]["content"] if r.get("results") else None
    except Exception:
        return None


def ouvir_audio(b):
    try:
        return client.audio.transcriptions.create(
            file=("t.wav", b, "audio/wav"),
            model="whisper-large-v3",
            response_format="text",
            language="pt"
        )
    except Exception:
        return None


async def falar(t):
    await edge_tts.Communicate(t, "pt-BR-FranciscaNeural").save("alerta.mp3")
    return "alerta.mp3"


# =========================
# VIGIA NATURAL (por tarefa)
# =========================
def proximo_lembrete(tarefa, agora):
    n = int(tarefa.get("remind_count", 0))
    if n >= len(REMINDER_SCHEDULE_MIN):
        return None
    return agora + timedelta(minutes=REMINDER_SCHEDULE_MIN[n])


tarefas = [normalizar_tarefa(t) for t in carregar_tarefas()]
salvar_tarefas(tarefas)

agora = datetime.now(FUSO_BR)
mensagem_cobranca = None
tarefa_cobrada = None

if not em_horario_silencioso(agora):
    for t in tarefas:
        try:
            if t.get("status") == "silenciada":
                continue

            data_tarefa = parse_dt(t["data_hora"])
            if agora <= data_tarefa:
                continue  # ainda não venceu

            # snooze
            if t.get("snoozed_until"):
                if agora < parse_dt(t["snoozed_until"]):
                    continue

            # só lembra se chegou no next_remind_at
            next_at = parse_dt(t.get("next_remind_at", t["data_hora"]))
            if agora < next_at:
                continue

            # dispara UMA cobrança por ciclo
            mensagem_cobranca = (
                f"🔔 Ei! Já passou do horário de **{t['descricao']}**.\n\n"
                "Responda:\n"
                "- **feito** / “já fiz”\n"
                "- **adiar 30 min**\n"
                "- **desconsidera / para de lembrar**"
            )
            tarefa_cobrada = t

            # atualiza estado da tarefa
            t["last_reminded_at"] = format_dt(agora)
            t["remind_count"] = int(t.get("remind_count", 0)) + 1

            prox = proximo_lembrete(t, agora)
            if prox is None:
                t["status"] = "silenciada"
                t["next_remind_at"] = format_dt(agora + timedelta(days=365))
            else:
                t["next_remind_at"] = format_dt(prox)

            salvar_tarefas(tarefas)
            break
        except Exception:
            pass

if mensagem_cobranca:
    st.warning(mensagem_cobranca)
    st.session_state.memoria_v3.append({"role": "assistant", "content": mensagem_cobranca})
    try:
        mp3 = asyncio.run(falar("Ei! Sua tarefa já passou do horário."))
        st.audio(mp3, format="audio/mp3", autoplay=True)
    except Exception:
        pass


# =========================
# UI
# =========================
col_main, col_agenda = st.columns([0.7, 0.3])

with col_agenda:
    st.subheader("📌 Agenda")

    if tarefas:
        for t in tarefas:
            try:
                dt_task = parse_dt(t["data_hora"])
                atrasada = agora > dt_task
            except Exception:
                atrasada = False

            if t.get("status") == "silenciada":
                icone = "🔕"
            else:
                icone = "🔥" if atrasada else "📅"

            st.warning(f"{icone} {t['data_hora'].split(' ')[1]}\n{t['descricao']}")

            cbtn1, cbtn2 = st.columns([0.5, 0.5])
            with cbtn1:
                if st.button("Feito", key=f"feito_{t['id']}"):
                    tarefas2 = [x for x in tarefas if x.get("id") != t.get("id")]
                    salvar_tarefas(tarefas2)
                    st.rerun()
            with cbtn2:
                if st.button("Silenciar", key=f"sil_{t['id']}"):
                    agora2 = datetime.now(FUSO_BR)
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
        for m in st.session_state.memoria_v3:
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
        if a := st.audio_input("🎙️"):
            if a != st.session_state.ultimo_audio:
                st.session_state.ultimo_audio = a
                with st.spinner("Transcrevendo..."):
                    texto = ouvir_audio(a)
                    usou_voz = True

    if texto:
        st.session_state.memoria_v3.append({"role": "user", "content": texto})
        with container.chat_message("user"):
            st.markdown(texto)

        with container.chat_message("assistant"):
            intencao = identificar_intencao(texto)

            # recarrega tarefas sempre (estado atual)
            tarefas = [normalizar_tarefa(t) for t in carregar_tarefas()]
            salvar_tarefas(tarefas)

            # -------- CONCLUIR / SILENCIAR / ADIAR --------
            if intencao in ["CONCLUIR", "SILENCIAR", "ADIAR"]:
                if not tarefas:
                    msg = "Sua agenda já está vazia!"
                    st.info(msg)
                    st.session_state.memoria_v3.append({"role": "assistant", "content": msg})
                else:
                    idx = escolher_tarefa(texto, tarefas)
                    if idx == -1 or idx >= len(tarefas):
                        msg = "Não consegui identificar qual tarefa você quis afetar."
                        st.warning(msg)
                        st.session_state.memoria_v3.append({"role": "assistant", "content": msg})
                    else:
                        if intencao == "CONCLUIR":
                            removida = tarefas.pop(idx)
                            salvar_tarefas(tarefas)

                            msg = f"✅ Marquei como feito: **{removida['descricao']}**."
                            st.success(msg)
                            st.session_state.memoria_v3.append({"role": "assistant", "content": msg})

                            if usou_voz:
                                try:
                                    mp3 = asyncio.run(falar("Maravilha! Tarefa concluída."))
                                    st.audio(mp3, format="audio/mp3", autoplay=True)
                                except Exception:
                                    pass

                            st.rerun()

                        elif intencao == "SILENCIAR":
                            agora2 = datetime.now(FUSO_BR)
                            tarefas[idx]["status"] = "silenciada"
                            tarefas[idx]["next_remind_at"] = format_dt(agora2 + timedelta(days=365))
                            salvar_tarefas(tarefas)

                            msg = f"🔕 Beleza. Parei de te lembrar: **{tarefas[idx]['descricao']}**."
                            st.success(msg)
                            st.session_state.memoria_v3.append({"role": "assistant", "content": msg})
                            st.rerun()

                        elif intencao == "ADIAR":
                            mins = extrair_minutos(texto, 30)
                            agora2 = datetime.now(FUSO_BR)
                            snooze_until = agora2 + timedelta(minutes=mins)

                            tarefas[idx]["snoozed_until"] = format_dt(snooze_until)
                            tarefas[idx]["next_remind_at"] = tarefas[idx]["snoozed_until"]
                            salvar_tarefas(tarefas)

                            msg = f"⏳ Adiei por {mins} min: **{tarefas[idx]['descricao']}**."
                            st.success(msg)
                            st.session_state.memoria_v3.append({"role": "assistant", "content": msg})
                            st.rerun()

            # -------- AGENDAR --------
            elif intencao == "AGENDAR":
                d = extrair_dados_tarefa(texto)
                if d:
                    d = normalizar_tarefa(d)
                    tarefas.append(d)
                    salvar_tarefas(tarefas)

                    msg = f"📌 Agendado: **{d['descricao']}** às **{d['data_hora'].split(' ')[1]}**."
                    st.success(msg)
                    st.session_state.memoria_v3.append({"role": "assistant", "content": msg})
                    st.rerun()
                else:
                    msg = "Não consegui extrair tarefa e horário. Ex: 'me lembra de abrir a HL às 19:13'."
                    st.warning(msg)
                    st.session_state.memoria_v3.append({"role": "assistant", "content": msg})

            # -------- BUSCAR --------
            elif intencao == "BUSCAR":
                web = buscar_tavily(texto)
                if web:
                    resp = client.chat.completions.create(
                        model=MODEL_ID,
                        messages=[{"role": "user", "content": f"Dados: {web}\nPergunta: {texto}"}],
                        temperature=0.2
                    ).choices[0].message.content

                    st.markdown(resp)
                    st.session_state.memoria_v3.append({"role": "assistant", "content": resp})
                else:
                    msg = "Não encontrei resultados agora."
                    st.warning(msg)
                    st.session_state.memoria_v3.append({"role": "assistant", "content": msg})

            # -------- CONVERSA --------
            else:
                msgs = [{"role": "system", "content": "Você é uma assistente útil, direta e amigável. Responda em pt-BR."}]
                for m in st.session_state.memoria_v3:
                    c = m.get("content", "")
                    if isinstance(c, str) and c.strip():
                        msgs.append({"role": m["role"], "content": c})

                resp = client.chat.completions.create(
                    model=MODEL_ID,
                    messages=msgs,
                    temperature=0.4
                ).choices[0].message.content

                st.markdown(resp)
                st.session_state.memoria_v3.append({"role": "assistant", "content": resp})

                if usou_voz:
                    try:
                        mp3 = asyncio.run(falar(resp))
                        st.audio(mp3, format="audio/mp3", autoplay=True)
                    except Exception:
                        pass

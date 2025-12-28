import streamlit as st
from groq import Groq
from tavily import TavilyClient
import edge_tts
import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# -----------------------------
# 1) CONFIG
# -----------------------------
st.set_page_config(page_title="Jarvis V3", page_icon="🤖")
st.title("Assistente Pessoal (Full Control)")

MODEL_ID = "llama-3.3-70b-versatile"
ARQUIVO_TAREFAS = "tarefas.json"
FUSO_BR = ZoneInfo("America/Sao_Paulo")

# lembretes naturais (quantas vezes lembrar e espaçamento)
REMINDER_SCHEDULE_MIN = [0, 10, 30, 120]  # após cada cobrança
QUIET_START = 22  # 22:00
QUIET_END = 7     # 07:00

# Refresh automático (sem loop maluco)
st.autorefresh(interval=10_000, key="auto_refresh")  # 10 segundos


# -----------------------------
# 2) CONEXÕES
# -----------------------------
try:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
    tavily = TavilyClient(api_key=st.secrets["TAVILY_API_KEY"])
except Exception:
    st.error("⚠️ Erro nas Chaves API. Verifique os Secrets.")
    st.stop()


# -----------------------------
# 3) SESSION STATE
# -----------------------------
if "memoria_v3" not in st.session_state:
    st.session_state.memoria_v3 = []
if "ultimo_audio" not in st.session_state:
    st.session_state.ultimo_audio = None


# -----------------------------
# 4) UTIL / STORAGE
# -----------------------------
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


def normalizar_tarefa(d):
    """Garante campos obrigatórios + estado do lembrete."""
    agora = datetime.now(FUSO_BR)
    d = dict(d)

    d.setdefault("id", str(uuid.uuid4())[:8])
    d.setdefault("status", "ativa")  # ativa | silenciada
    d.setdefault("remind_count", 0)
    d.setdefault("last_reminded_at", None)
    d.setdefault("snoozed_until", None)
    d.setdefault("created_at", format_dt(agora))

    # começa lembrando no horário da tarefa
    d.setdefault("next_remind_at", d.get("data_hora"))
    return d


def extrair_minutos(texto, padrao=30):
    m = re.search(r"(\d+)\s*(min|mins|minuto|minutos|h|hora|horas)", texto.lower())
    if not m:
        return padrao
    n = int(m.group(1))
    unidade = m.group(2)
    if unidade.startswith("h") or "hora" in unidade:
        return n * 60
    return n


# -----------------------------
# 5) INTENÇÕES
# -----------------------------
def identificar_intencao(texto):
    t = texto.lower()

    termos_concluir = [
        "já fiz", "ja fiz", "feito", "conclu", "termin", "resolvi",
        "já abri", "ja abri", "já fechei", "ja fechei", "finalizei"
    ]
    if any(x in t for x in termos_concluir):
        return "CONCLUIR"

    termos_silenciar = [
        "desconsidera", "cancela", "cancelar", "para de", "pare de",
        "não me lembra", "nao me lembra", "silencia", "chega", "parar isso"
    ]
    if any(x in t for x in termos_silenciar):
        return "SILENCIAR"

    termos_adiar = ["adiar", "mais tarde", "daqui a", "só daqui", "me lembra em", "snooze"]
    if any(x in t for x in termos_adiar):
        return "ADIAR"

    termos_agenda = ["lembr", "agend", "anot", "marc", "cobr", "avis"]
    if any(tk in t for tk in termos_agenda):
        return "AGENDAR"

    if any(x in t for x in ["hoje", "preço", "notícia", "valor", "dólar", "tempo", "quem ganhou"]):
        return "BUSCAR"

    return "RESPONDER"


# -----------------------------
# 6) IA HELPERS (tarefas)
# -----------------------------
def encontrar_tarefa_para_remover(texto_usuario, lista_tarefas):
    """LLM escolhe o índice com base na descrição. Retorna índice ou -1."""
    if not lista_tarefas:
        return -1

    descricao_tarefas = []
    for i, t in enumerate(lista_tarefas):
        desc = t.get("descricao", "")
        dh = t.get("data_hora", "")
        descricao_tarefas.append(f"ID {i}: {desc} (em {dh})")

    lista_texto = "\n".join(descricao_tarefas)

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
        resultado = (resp.choices[0].message.content or "").strip()
        numero = re.search(r"-?\d+", resultado)
        return int(numero.group()) if numero else -1
    except Exception:
        return -1


def tarefa_mais_relevante(texto_usuario, tarefas):
    """Heurística: se só tiver 1 atrasada ativa, escolhe ela; senão usa LLM."""
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

    return encontrar_tarefa_para_remover(texto_usuario, tarefas)


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


# -----------------------------
# 7) WEB / AUDIO
# -----------------------------
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


# -----------------------------
# 8) VIGIA (proativo, natural)
# -----------------------------
def proximo_lembrete(tarefa, agora):
    n = int(tarefa.get("remind_count", 0))
    if n >= len(REMINDER_SCHEDULE_MIN):
        return None
    return agora + timedelta(minutes=REMINDER_SCHEDULE_MIN[n])


tarefas = [normalizar_tarefa(t) for t in carregar_tarefas()]
salvar_tarefas(tarefas)  # garante migração dos campos

agora = datetime.now(FUSO_BR)
mensagem_cobranca = None

if not em_horario_silencioso(agora):
    for t in tarefas:
        try:
            if t.get("status") == "silenciada":
                continue

            data_tarefa = parse_dt(t["data_hora"])

            # só cobra se atrasou
            if agora <= data_tarefa:
                continue

            # snooze
            if t.get("snoozed_until"):
                if agora < parse_dt(t["snoozed_until"]):
                    continue

            next_at = parse_dt(t.get("next_remind_at", t["data_hora"]))
            if agora < next_at:
                continue

            mensagem_cobranca = (
                f"🔔 Ei! Já passou do horário de **{t['descricao']}**.\n\n"
                "Responda:\n"
                "- **feito** (ou “já fiz”)\n"
                "- **adiar 30 min**\n"
                "- **desconsidera / para de lembrar**"
            )

            # atualiza estado
            t["last_reminded_at"] = format_dt(agora)
            t["remind_count"] = int(t.get("remind_count", 0)) + 1

            prox = proximo_lembrete(t, agora)
            if prox is None:
                # depois de algumas tentativas, silencia automaticamente
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
        arquivo_bronca = asyncio.run(falar("Ei! Sua tarefa já passou do horário."))
        st.audio(arquivo_bronca, format="audio/mp3", autoplay=True)
    except Exception:
        pass


# -----------------------------
# 9) UI: COLUNAS
# -----------------------------
col_main, col_agenda = st.columns([0.7, 0.3])

# Sidebar / Agenda
with col_agenda:
    st.subheader("📌 Agenda")

    if tarefas:
        for i, t in enumerate(tarefas):
            try:
                dt_task = parse_dt(t["data_hora"])
                atrasada = agora > dt_task
            except Exception:
                atrasada = False

            icone = "🔥" if (atrasada and t.get("status") != "silenciada") else ("🔕" if t.get("status") == "silenciada" else "📅")

            st.warning(f"{icone} {t['data_hora'].split(' ')[1]}\n{t['descricao']}")

            cbtn1, cbtn2 = st.columns([0.5, 0.5])
            with cbtn1:
                if st.button("Feito", key=f"feito_{t['id']}"):
                    # remove
                    tarefas = [x for x in tarefas if x.get("id") != t.get("id")]
                    salvar_tarefas(tarefas)
                    st.rerun()
            with cbtn2:
                if st.button("Silenciar", key=f"sil_{t['id']}"):
                    for x in tarefas:
                        if x.get("id") == t.get("id"):
                            x["status"] = "silenciada"
                            x["next_remind_at"] = format_dt(agora + timedelta(days=365))
                    salvar_tarefas(tarefas)
                    st.rerun()
    else:
        st.success("Livre!")


# -----------------------------
# 10) CHAT
# -----------------------------
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

            # recarrega tarefas (sempre pega estado atualizado)
            tarefas = [normalizar_tarefa(t) for t in carregar_tarefas()]
            salvar_tarefas(tarefas)

            # -------- CONCLUIR / SILENCIAR / ADIAR --------
            if intencao in ["CONCLUIR", "SILENCIAR", "ADIAR"]:
                if not tarefas:
                    st.info("Sua agenda já está vazia!")
                else:
                    idx = tarefa_mais_relevante(texto, tarefas)
                    if idx == -1 or idx >= len(tarefas):
                        st.warning("Não consegui identificar qual tarefa você quis afetar.")
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
                            tarefas[idx]["status"] = "silenciada"
                            tarefas[idx]["next_remind_at"] = format_dt(datetime.now(FUSO_BR) + timedelta(days=365))
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
                    st.warning("Não consegui extrair a tarefa e o horário. Tenta: 'me lembra de X às 19:30'.")

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
                    if usou_voz:
                        try:
                            mp3 = asyncio.run(falar(resp))
                            st.audio(mp3, format="audio/mp3", autoplay=True)
                        except Exception:
                            pass
                else:
                    st.warning("Não encontrei resultados agora.")

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

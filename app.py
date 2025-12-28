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

REMINDER_SCHEDULE_MIN = [0, 10, 30, 120]  # lembretes naturais
QUIET_START = 22
QUIET_END = 7

# refresh leve (sem loop agressivo)
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
    agora = datetime.now(FUSO_BR)
    d = dict(d)
    d.setdefault("id", str(uuid.uuid4())[:8])
    d.setdefault("status", "ativa")         # ativa | silenciada
    d.setdefault("remind_count", 0)
    d.setdefault("last_reminded_at", None)
    d.setdefault("snoozed_until", None)
    d.setdefault("created_at", format_dt(agora))
    d.setdefault("next_remind_at", d.get("data_hora"))  # primeiro lembrete no horário
    return d


def limpar_texto(s: str):
    s = s.lower()
    s = re.sub(r"[^a-z0-9áàâãéèêíìîóòôõúùûç\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# =========================
# INTENÇÕES (CORRIGIDO)
# =========================
def identificar_intencao(texto: str):
    t = limpar_texto(texto)

    # 1) Se começa com “me lembra…” / “lembra de…” -> AGENDAR SEMPRE
    if re.search(r"^(me\s+lembra|lembra\s+de|me\s+avisa|avisa\s+me|me\s+cobra)", t):
        return "AGENDAR"

    # 2) ADIAR explícito
    if any(x in t for x in ["adiar", "mais tarde", "daqui a", "me lembra em", "snooze"]):
        return "ADIAR"

    # 3) SILENCIAR explícito (sem “já fiz”)
    if any(x in t for x in ["desconsidera", "cancela", "cancelar", "para de", "pare de", "silencia", "não me lembra", "nao me lembra"]):
        # se o usuário disse que já fez, vira CONCLUIR
        if any(x in t for x in ["ja fiz", "já fiz", "feito", "terminei", "conclui", "finalizei", "resolvi", "já abri", "ja abri", "já fechei", "ja fechei"]):
            return "CONCLUIR"
        return "SILENCIAR"

    # 4) CONCLUIR
    if any(x in t for x in ["ja fiz", "já fiz", "feito", "terminei", "conclui", "finalizei", "resolvi", "já abri", "ja abri", "já fechei", "ja fechei"]):
        return "CONCLUIR"

    # 5) BUSCAR
    if any(x in t for x in ["preço", "notícia", "valor", "dólar", "dolar", "tempo", "quem ganhou"]):
        return "BUSCAR"

    return "RESPONDER"


# =========================
# PARSER RELATIVO + AJUSTE FUTURO (CORRIGIDO)
# =========================
def parse_relativo(texto: str):
    """
    Detecta:
    - daqui um minuto / daqui 2 minutos
    - daqui 1 hora / daqui 3 horas
    - em 10 min
    Retorna timedelta ou None
    """
    t = limpar_texto(texto)

    # “daqui um minuto”
    if "daqui um minuto" in t or "daqui 1 minuto" in t:
        return timedelta(minutes=1)

    m = re.search(r"(daqui\s+a|daqui|em)\s+(\d+)\s*(min|minuto|minutos|h|hora|horas)", t)
    if not m:
        return None

    n = int(m.group(2))
    unidade = m.group(3)

    if unidade.startswith("h") or "hora" in unidade:
        return timedelta(hours=n)
    return timedelta(minutes=n)


def ajustar_para_futuro(dt_extraido: datetime, agora: datetime):
    """
    Garante que o horário agendado seja o PRÓXIMO futuro.
    Ex:
      agora = 19:13
      dt_extraido = hoje 07:19 -> tenta 19:19 hoje (se fizer sentido) senão joga pro dia seguinte
    """
    # se já é futuro, ok
    if dt_extraido > agora + timedelta(seconds=5):
        return dt_extraido

    # tenta +12h (resolve 07:19 vs 19:19)
    tentativa = dt_extraido + timedelta(hours=12)
    if tentativa.hour <= 23 and tentativa > agora + timedelta(seconds=5):
        return tentativa

    # senão joga pro dia seguinte no mesmo horário
    return dt_extraido + timedelta(days=1)


def extrair_descricao(texto: str):
    """
    Remove gatilhos tipo “me lembra de”, “daqui um minuto”, “por favor” etc.
    """
    t = limpar_texto(texto)
    t = re.sub(r"^(me\s+lembra\s+de|me\s+lembra|lembra\s+de|me\s+avisa|avisa\s+me|me\s+cobra)\s+", "", t)
    t = re.sub(r"(daqui\s+a\s+\d+\s*(min|minuto|minutos|h|hora|horas)|daqui\s+um\s+minuto|em\s+\d+\s*(min|minuto|minutos|h|hora|horas))", "", t)
    t = re.sub(r"\b(por\s+favor|pfv|porfav(or)?)\b", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t if t else "tarefa"


def extrair_dados_tarefa(texto: str):
    """
    1) Se for relativo -> agenda agora + delta.
    2) Senão usa LLM pra pegar horário.
    3) Ajusta para próximo futuro.
    """
    agora = datetime.now(FUSO_BR)

    delta = parse_relativo(texto)
    if delta:
        dt = agora + delta
        return {"descricao": extrair_descricao(texto), "data_hora": format_dt(dt)}

    agora_br = agora.strftime("%Y-%m-%d %H:%M")
    prompt = (
        f"Hoje é {agora_br} (America/Sao_Paulo).\n"
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
        return data
    except Exception:
        return None


# =========================
# ESCOLHER TAREFA (ROBUSTO)
# =========================
def escolher_tarefa(texto_usuario: str, tarefas: list):
    if not tarefas:
        return -1
    if len(tarefas) == 1:
        return 0

    # tenta escolher a tarefa mais atrasada ativa
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

    # tenta token forte
    texto_limpo = limpar_texto(texto_usuario)
    tokens_fortes = ["hl", "hr", "ahl", "dpr", "rib"]
    for tok in tokens_fortes:
        if tok in texto_limpo:
            for i, t in enumerate(tarefas):
                if tok in limpar_texto(t.get("descricao", "")):
                    return i

    # fallback LLM
    linhas = [f"ID {i}: {t.get('descricao','')} (em {t.get('data_hora','')})" for i, t in enumerate(tarefas)]
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

if not em_horario_silencioso(agora):
    for t in tarefas:
        try:
            if t.get("status") == "silenciada":
                continue

            data_tarefa = parse_dt(t["data_hora"])
            if agora <= data_tarefa:
                continue

            if t.get("snoozed_until") and agora < parse_dt(t["snoozed_until"]):
                continue

            next_at = parse_dt(t.get("next_remind_at", t["data_hora"]))
            if agora < next_at:
                continue

            mensagem_cobranca = (
                f"🔔 Ei! Já passou do horário de **{t['descricao']}**.\n\n"
                "Responda:\n"
                "- **feito** / “já fiz”\n"
                "- **adiar 30 min**\n"
                "- **desconsidera / para de lembrar**"
            )

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
                atrasada = agora > parse_dt(t["data_hora"])
            except Exception:
                atrasada = False

            icone = "🔕" if t.get("status") == "silenciada" else ("🔥" if atrasada else "📅")
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
            tarefas = [normalizar_tarefa(t) for t in carregar_tarefas()]
            salvar_tarefas(tarefas)

            if intencao == "AGENDAR":
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
                    msg = "Não consegui extrair tarefa e horário. Ex: 'me lembra de fechar a HL às 19:19' ou 'daqui 1 minuto'."
                    st.warning(msg)
                    st.session_state.memoria_v3.append({"role": "assistant", "content": msg})

            elif intencao in ["CONCLUIR", "SILENCIAR", "ADIAR"]:
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
                            mins = 30
                            # extrai minutos do texto
                            m = re.search(r"(\d+)\s*(min|minuto|minutos|h|hora|horas)", limpar_texto(texto))
                            if m:
                                mins = int(m.group(1))
                                if "h" in m.group(2) or "hora" in m.group(2):
                                    mins *= 60

                            agora2 = datetime.now(FUSO_BR)
                            snooze_until = agora2 + timedelta(minutes=mins)
                            tarefas[idx]["snoozed_until"] = format_dt(snooze_until)
                            tarefas[idx]["next_remind_at"] = tarefas[idx]["snoozed_until"]
                            salvar_tarefas(tarefas)
                            msg = f"⏳ Adiei por {mins} min: **{tarefas[idx]['descricao']}**."
                            st.success(msg)
                            st.session_state.memoria_v3.append({"role": "assistant", "content": msg})
                            st.rerun()

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

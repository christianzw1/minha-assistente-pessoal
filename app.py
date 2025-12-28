import streamlit as st
from groq import Groq
from tavily import TavilyClient
import edge_tts
import asyncio
import json
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo # Biblioteca de Fuso Horário

# --- 1. Configuração ---
st.set_page_config(page_title="Jarvis BR", page_icon="🇧🇷")
st.title("Assistente Pessoal (Horário de Brasília)")

# --- 2. Conexão ---
try:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
    tavily = TavilyClient(api_key=st.secrets["TAVILY_API_KEY"])
except:
    st.error("⚠️ Erro nas Chaves API. Verifique os Secrets.")
    st.stop()

MODEL_ID = "llama-3.3-70b-versatile"
ARQUIVO_TAREFAS = "tarefas.json"
FUSO_BR = ZoneInfo("America/Sao_Paulo") # Fuso Horário Oficial

# --- 3. Memória ---
if "memoria_v3" not in st.session_state:
    st.session_state.memoria_v3 = []
if "ultimo_audio" not in st.session_state:
    st.session_state.ultimo_audio = None
if "ultima_cobranca" not in st.session_state:
    st.session_state.ultima_cobranca = datetime.min.replace(tzinfo=FUSO_BR)

def carregar_tarefas():
    if not os.path.exists(ARQUIVO_TAREFAS): return []
    try:
        with open(ARQUIVO_TAREFAS, "r") as f: return json.load(f)
    except: return []

def salvar_tarefas(lista):
    with open(ARQUIVO_TAREFAS, "w") as f: json.dump(lista, f)

# --- FUNÇÕES INTELIGENTES ---
def identificar_intencao(texto):
    texto = texto.lower()
    # CORREÇÃO: Usamos partes das palavras para pegar "lembre", "lembrar", "lembra"
    palavras_chave_agenda = ["lembr", "agend", "anota", "marca", "cobr", "avis"]
    
    if any(p in texto for p in palavras_chave_agenda): 
        return "AGENDAR"
    if any(x in texto for x in ["hoje", "preço", "notícia", "valor", "dólar", "tempo"]): 
        return "BUSCAR"
    return "RESPONDER"

def extrair_dados_tarefa(texto):
    agora_br = datetime.now(FUSO_BR).strftime("%Y-%m-%d %H:%M")
    prompt = f"""
    Estamos no Brasil. Agora é: {agora_br}.
    O usuário disse: "{texto}".
    Extraia a tarefa e a data/hora limite.
    Se ele disse apenas a hora (ex: 18:30), use a data de hoje.
    Retorne JSON PURO: {{"descricao": "...", "data_hora": "YYYY-MM-DD HH:MM"}}
    """
    try:
        resp = client.chat.completions.create(model=MODEL_ID, messages=[{"role":"user","content":prompt}], response_format={"type":"json_object"})
        return json.loads(resp.choices[0].message.content)
    except: return None

def buscar_tavily(q):
    try: return tavily.search(query=q, max_results=2)['results'][0]['content']
    except: return None

def ouvir_audio(b):
    try: return client.audio.transcriptions.create(file=("t.wav", b, "audio/wav"), model="whisper-large-v3", response_format="text", language="pt")
    except: return None

async def falar(t):
    await edge_tts.Communicate(t, "pt-BR-FranciscaNeural").save("alerta.mp3")
    return "alerta.mp3"

# --- INTERFACE ---
col_main, col_agenda = st.columns([0.7, 0.3])

# --- O VIGIA (Com Fuso Correto) ---
tarefas = carregar_tarefas()
agora = datetime.now(FUSO_BR)
mensagem_cobranca = None

for t in tarefas:
    try:
        data_tarefa = datetime.strptime(t['data_hora'], "%Y-%m-%d %H:%M").replace(tzinfo=FUSO_BR)
        tempo_desde_ultima = (agora - st.session_state.ultima_cobranca).total_seconds()
        
        # Cobra se passou da hora e faz mais de 2 minutos que não fala
        if agora > data_tarefa and tempo_desde_ultima > 120:
            mensagem_cobranca = f"Ei! Já são {agora.strftime('%H:%M')} e a tarefa '{t['descricao']}' venceu às {t['data_hora'].split(' ')[1]}. Já fez?"
            st.session_state.ultima_cobranca = agora
            break
    except: pass

if mensagem_cobranca:
    st.error(mensagem_cobranca, icon="🚨")
    st.session_state.memoria_v3.append({"role": "assistant", "content": "🚨 " + mensagem_cobranca})
    arquivo_bronca = asyncio.run(falar(mensagem_cobranca))
    st.audio(arquivo_bronca, format="audio/mp3", autoplay=True)

# --- EXIBIÇÃO ---
with col_agenda:
    st.subheader("📌 Agenda")
    st.caption(f"🕒 Hora Brasil: {agora.strftime('%H:%M:%S')}")
    
    if tarefas:
        for i, t in enumerate(tarefas):
            cor_alerta = "🚨" if agora > datetime.strptime(t['data_hora'], "%Y-%m-%d %H:%M").replace(tzinfo=FUSO_BR) else "📅"
            st.write(f"{cor_alerta} **{t['data_hora'].split(' ')[1]}**")
            st.caption(t['descricao'])
            if st.button("Concluir", key=f"del_{i}"):
                tarefas.pop(i)
                salvar_tarefas(tarefas)
                st.rerun()
            st.divider()
    else:
        st.info("Tudo limpo!")

with col_main:
    container = st.container()
    with container:
        for m in st.session_state.memoria_v3:
            with st.chat_message(m["role"]): st.markdown(m["content"])

    st.divider()
    c1, c2 = st.columns([0.15, 0.85])
    texto = None
    usou_voz = False
    
    with c2: 
        if t := st.chat_input("Mensagem..."): texto = t
    with c1:
        if a := st.audio_input("🎙️"):
            if a != st.session_state.ultimo_audio:
                st.session_state.ultimo_audio = a
                with st.spinner("."): 
                    texto = ouvir_audio(a)
                    usou_voz = True

    if texto:
        st.session_state.memoria_v3.append({"role": "user", "content": texto})
        with container.chat_message("user"): st.markdown(texto)
        
        with container.chat_message("assistant"):
            intencao = identificar_intencao(texto)
            resp = ""
            
            if "AGENDAR" in intencao:
                d = extrair_dados_tarefa(texto)
                if d:
                    tarefas.append(d)
                    salvar_tarefas(tarefas)
                    resp = f"Agendado para {d['data_hora']}: {d['descricao']}"
                    st.rerun()
            
            elif "BUSCAR" in intencao:
                if web := buscar_tavily(texto):
                    resp = client.chat.completions.create(model=MODEL_ID, messages=[{"role":"user","content":f"Dados: {web}. Pergunta: {texto}"}]).choices[0].message.content
            
            else:
                msgs = [{"role":"system","content":"Assistente útil."}] + [{"role":m["role"],"content":str(m["content"])} for m in st.session_state.memoria_v3]
                resp = client.chat.completions.create(model=MODEL_ID, messages=msgs).choices[0].message.content

            if resp:
                st.markdown(resp)
                st.session_state.memoria_v3.append({"role": "assistant", "content": resp})
                if usou_voz:
                    mp3 = asyncio.run(falar(resp))
                    # AQUI ESTAVA O ERRO, AGORA ESTÁ CORRIGIDO:
                    st.audio(mp3, format="audio/mp3", autoplay=True)

# Loop de Vigília (10 segundos)
time.sleep(10)
st.rerun()

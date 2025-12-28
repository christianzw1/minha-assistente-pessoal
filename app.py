import streamlit as st
from groq import Groq
from tavily import TavilyClient
import edge_tts
import asyncio
import json
import os
import time
from datetime import datetime, timedelta

# --- 1. Configuração ---
st.set_page_config(page_title="Jarvis Proativo", page_icon="⏰")
st.title("Assistente Pessoal (Modo Vigília)")

# --- 2. Conexão ---
try:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
    tavily = TavilyClient(api_key=st.secrets["TAVILY_API_KEY"])
except:
    st.error("⚠️ Erro nas Chaves API.")
    st.stop()

MODEL_ID = "llama-3.3-70b-versatile"
ARQUIVO_TAREFAS = "tarefas.json"

# --- 3. Memória ---
if "memoria_v3" not in st.session_state:
    st.session_state.memoria_v3 = []
if "ultimo_audio" not in st.session_state:
    st.session_state.ultimo_audio = None
# Controle de "Encheção de Saco" (Para não cobrar a cada milissegundo)
if "ultima_cobranca" not in st.session_state:
    st.session_state.ultima_cobranca = datetime.min

def carregar_tarefas():
    if not os.path.exists(ARQUIVO_TAREFAS): return []
    try:
        with open(ARQUIVO_TAREFAS, "r") as f: return json.load(f)
    except: return []

def salvar_tarefas(lista):
    with open(ARQUIVO_TAREFAS, "w") as f: json.dump(lista, f)

# --- FUNÇÕES INTELIGENTES ---
def identificar_intencao(texto):
    if any(x in texto.lower() for x in ["lembrar", "agendar", "anotar", "marcar"]): return "AGENDAR"
    if any(x in texto.lower() for x in ["hoje", "preço", "notícia", "valor"]): return "BUSCAR"
    return "RESPONDER"

def extrair_dados_tarefa(texto):
    agora = datetime.now().strftime("%Y-%m-%d %H:%M")
    prompt = f"""Hoje: {agora}. Texto: "{texto}".
    Retorne JSON: {{"descricao": "...", "data_hora": "YYYY-MM-DD HH:MM"}}
    Se não der hora, use 18:00."""
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

# --- O VIGIA (Lógica de Cobrança Automática) ---
# Isso roda toda vez que a página atualiza
tarefas = carregar_tarefas()
agora = datetime.now()
mensagem_cobranca = None

for t in tarefas:
    data_tarefa = datetime.strptime(t['data_hora'], "%Y-%m-%d %H:%M")
    
    # Se já passou da hora E faz mais de 5 minutos que não cobramos
    tempo_desde_ultima = (agora - st.session_state.ultima_cobranca).total_seconds()
    
    if agora > data_tarefa and tempo_desde_ultima > 300: # 300 segundos = 5 min
        mensagem_cobranca = f"Atenção! Já passou das {t['data_hora'].split(' ')[1]} e você não marcou como feita: {t['descricao']}. Vai fazer agora?"
        st.session_state.ultima_cobranca = agora # Marca que cobrou agora
        break # Cobra uma por vez pra não virar bagunça

# Se o vigia detectou atraso, ele TOCA O TERROR (Gera áudio sozinho)
if mensagem_cobranca:
    # Adiciona no chat visualmente
    st.session_state.memoria_v3.append({"role": "assistant", "content": "🔔 " + mensagem_cobranca})
    
    # Gera o áudio da bronca
    arquivo_bronca = asyncio.run(falar(mensagem_cobranca))
    st.audio(arquivo_bronca, format="audio/mp3", autoplay=True)
    st.toast("🔔 TAREFA ATRASADA! AUMENTA O SOM!", icon="📢")


# --- EXIBIÇÃO NORMAL ---
with col_agenda:
    st.subheader("📌 Agenda")
    if tarefas:
        for i, t in enumerate(tarefas):
            st.warning(f"{t['data_hora']}\n{t['descricao']}")
            if st.button("✅ Feito", key=i):
                tarefas.pop(i)
                salvar_tarefas(tarefas)
                st.rerun()
    else: st.info("Livre!")

with col_main:
    # Chat
    container = st.container()
    with container:
        for m in st.session_state.memoria_v3:
            with st.chat_message(m["role"]): st.markdown(m["content"])

    # Inputs
    st.divider()
    c1, c2 = st.columns([0.15, 0.85])
    texto = None
    usou_voz = False
    
    with c2: 
        if t := st.chat_input("Digitar..."): texto = t
    with c1:
        if a := st.audio_input("🎙️"):
            if a != st.session_state.ultimo_audio:
                st.session_state.ultimo_audio = a
                with st.spinner("."): 
                    texto = ouvir_audio(a)
                    usou_voz = True

    # Processamento
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
                    resp = f"Agendado: {d['descricao']} para {d['data_hora']}."
                    st.rerun() # Atualiza a agenda na hora
            
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
                    st.audio(mp3, format="audio/mp3", autoplay=True)

# --- O CORAÇÃO DO SISTEMA (AUTO-REFRESH) ---
# Isso faz a página recarregar sozinha a cada 30 segundos para checar tarefas
time.sleep(30)
st.rerun()

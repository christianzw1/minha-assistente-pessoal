import streamlit as st
from groq import Groq
from tavily import TavilyClient
import edge_tts
import asyncio
import json
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

# --- 1. Configuração ---
st.set_page_config(page_title="Jarvis Proativo", page_icon="⏰")
st.title("Assistente Pessoal (Vigília 2.0)")

# --- 2. Conexão ---
try:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
    tavily = TavilyClient(api_key=st.secrets["TAVILY_API_KEY"])
except:
    st.error("⚠️ Erro nas Chaves API. Verifique os Secrets.")
    st.stop()

MODEL_ID = "llama-3.3-70b-versatile"
ARQUIVO_TAREFAS = "tarefas.json"
FUSO_BR = ZoneInfo("America/Sao_Paulo")

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

# --- CÉREBRO CORRIGIDO (Detector Flexível) ---
def identificar_intencao(texto):
    texto = texto.lower()
    # AGORA PEGA TUDO: "lembr" pega (lembre, lembrar, lembrete)...
    palavras_chave = ["lembr", "agend", "anot", "marc", "cobr", "avis"]
    
    if any(p in texto for p in palavras_chave):
        return "AGENDAR"
    if any(x in texto for x in ["hoje", "preço", "notícia", "valor", "dólar", "tempo"]):
        return "BUSCAR"
    return "RESPONDER"

def extrair_dados_tarefa(texto):
    agora_br = datetime.now(FUSO_BR).strftime("%Y-%m-%d %H:%M")
    prompt = f"""
    Estamos no Brasil. Agora é: {agora_br}.
    User: "{texto}".
    Extraia a tarefa e a data/hora limite (formato YYYY-MM-DD HH:MM).
    Se não houver hora explícita, defina para as 18:00 de hoje.
    Responda APENAS o JSON: {{"descricao": "...", "data_hora": "..."}}
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

# --- O VIGIA (Frequência de Cobrança: 30s) ---
tarefas = carregar_tarefas()
agora = datetime.now(FUSO_BR)
mensagem_cobranca = None

for t in tarefas:
    try:
        data_tarefa = datetime.strptime(t['data_hora'], "%Y-%m-%d %H:%M").replace(tzinfo=FUSO_BR)
        tempo_desde_ultima = (agora - st.session_state.ultima_cobranca).total_seconds()
        
        # Se passou da hora E faz mais de 30 segundos da última bronca
        if agora > data_tarefa and tempo_desde_ultima > 30:
            mensagem_cobranca = f"Ei! Já são {agora.strftime('%H:%M')} e você não fez: {t['descricao']}!"
            st.session_state.ultima_cobranca = agora
            break
    except: pass

if mensagem_cobranca:
    st.error(mensagem_cobranca, icon="🔔")
    st.session_state.memoria_v3.append({"role": "assistant", "content": "🔔 " + mensagem_cobranca})
    arquivo_bronca = asyncio.run(falar(mensagem_cobranca))
    st.audio(arquivo_bronca, format="audio/mp3", autoplay=True)

# --- SIDEBAR AGENDA ---
with col_agenda:
    st.subheader("📌 Agenda")
    st.caption(f"🕒 {agora.strftime('%H:%M:%S')}")
    
    if tarefas:
        for i, t in enumerate(tarefas):
            atrasada = agora > datetime.strptime(t['data_hora'], "%Y-%m-%d %H:%M").replace(tzinfo=FUSO_BR)
            icone = "🔥" if atrasada else "📅"
            st.warning(f"{icone} {t['data_hora'].split(' ')[1]}\n{t['descricao']}")
            if st.button("Feito", key=f"d{i}"):
                tarefas.pop(i)
                salvar_tarefas(tarefas)
                st.rerun()
    else: st.success("Livre!")

# --- CHAT PRINCIPAL ---
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
            
            # FEEDBACK VISUAL (Para você saber se funcionou)
            if intencao == "AGENDAR":
                st.toast("📅 Entendi: Vou agendar!", icon="✅")
                d = extrair_dados_tarefa(texto)
                if d:
                    tarefas.append(d)
                    salvar_tarefas(tarefas)
                    st.success(f"Agendado: **{d['descricao']}** às {d['data_hora']}")
                    st.rerun()
            
            elif intencao == "BUSCAR":
                st.toast("🌍 Buscando na web...", icon="🔍")
                web = buscar_tavily(texto)
                resp = client.chat.completions.create(model=MODEL_ID, messages=[{"role":"user","content":f"Dados: {web}. Pergunta: {texto}"}]).choices[0].message.content
                st.markdown(resp)
                st.session_state.memoria_v3.append({"role": "assistant", "content": resp})
            
            else:
                st.toast("💬 Apenas conversando...", icon="🤖")
                # Instrução para NÃO mentir sobre agendamentos
                msgs = [{"role":"system","content":"Você é uma assistente. Se o usuário pedir para agendar algo e você caiu aqui, diga: 'Por favor, use a palavra AGENDAR ou LEMBRAR'."}] 
                for m in st.session_state.memoria_v3:
                    if m.get("content"): msgs.append({"role":m["role"],"content":str(m["content"])})
                
                resp = client.chat.completions.create(model=MODEL_ID, messages=msgs).choices[0].message.content
                st.markdown(resp)
                st.session_state.memoria_v3.append({"role": "assistant", "content": resp})
                
                if usou_voz:
                    mp3 = asyncio.run(falar(resp))
                    st.audio(mp3, format="audio/mp3", autoplay=True)

time.sleep(10)
st.rerun()

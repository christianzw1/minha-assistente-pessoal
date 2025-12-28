import streamlit as st
from groq import Groq
import edge_tts
import asyncio
import os

# --- 1. Configuração ---
st.set_page_config(page_title="Jarvis Neural", page_icon="🎙️")
st.title("Assistente Pessoal (Voz Neural)")

# --- 2. Conexão ---
try:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
except Exception as e:
    st.error("⚠️ Erro na Chave API.")
    st.stop()

MODEL_ID = "llama-3.3-70b-versatile"

# --- 3. Memória Blindada ---
if "memoria_v3" not in st.session_state:
    st.session_state.memoria_v3 = []

# Botão de limpeza discreto na sidebar
if st.sidebar.button("🗑️ Limpar Memória"):
    st.session_state.memoria_v3 = []
    st.rerun()

# --- FUNÇÕES DE ÁUDIO ---

def ouvir_audio_whisper(audio_bytes):
    """Ouvidos: Transcreve o áudio usando Groq Whisper (Rápido)"""
    try:
        return client.audio.transcriptions.create(
            file=("temp.wav", audio_bytes, "audio/wav"),
            model="whisper-large-v3",
            response_format="text",
            language="pt"
        )
    except Exception as e:
        st.error(f"Erro ao ouvir: {e}")
        return None

async def gerar_audio_neural(texto):
    """Boca: Gera áudio neural usando Edge-TTS (Microsoft Azure Free)"""
    OUTPUT_FILE = "resposta_neural.mp3"
    # Vozes PT-BR disponíveis: 'pt-BR-FranciscaNeural' (Mulher) ou 'pt-BR-AntonioNeural' (Homem)
    VOICE = "pt-BR-FranciscaNeural" 
    
    communicate = edge_tts.Communicate(texto, VOICE)
    await communicate.save(OUTPUT_FILE)
    return OUTPUT_FILE

# --- 4. Interface de Chat ---
# Container para o histórico (deixa espaço para os inputs embaixo)
chat_container = st.container()

with chat_container:
    for message in st.session_state.memoria_v3:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

# --- 5. Área de Input (Híbrida) ---
# Usamos um container fixo ou a parte inferior para organizar
st.divider() # Linha separadora
col_audio, col_texto = st.columns([0.2, 0.8]) # Layout lado a lado (aprox)

prompt_final = None
usou_audio = False

# Input de Áudio (Novo Widget Compacto)
with col_audio:
    audio_gravado = st.audio_input("🎙️") # Ícone minimalista

# Input de Texto
with col_texto:
    prompt_texto = st.chat_input("Digite ou grave ao lado...")

# Lógica de Prioridade (Quem mandar primeiro, ganha)
if audio_gravado:
    with st.spinner("Processando voz..."):
        prompt_final = ouvir_audio_whisper(audio_gravado)
        usou_audio = True
elif prompt_texto:
    prompt_final = prompt_texto

# --- 6. Processamento Inteligente ---
if prompt_final:
    # Mostra mensagem do usuário (se for texto, o chat input já mostra, se for áudio forçamos)
    if usou_audio:
        with chat_container.chat_message("user"):
            st.markdown(prompt_final)
    
    st.session_state.memoria_v3.append({"role": "user", "content": prompt_final})

    # Resposta da IA
    with chat_container.chat_message("assistant"):
        placeholder_texto = st.empty()
        placeholder_audio = st.empty()
        
        try:
            # 1. Filtro de Segurança
            msgs_api = [{"role": "system", "content": "Você é uma assistente útil, carismática e direta. Responda em Português."}]
            for m in st.session_state.memoria_v3:
                if m.get("content"):
                    msgs_api.append({"role": m["role"], "content": str(m["content"])})

            # 2. Gera Texto (Llama 3)
            with st.spinner("Pensando..."):
                completion = client.chat.completions.create(
                    model=MODEL_ID,
                    messages=msgs_api,
                    stream=False
                )
                resposta_texto = completion.choices[0].message.content
                placeholder_texto.markdown(resposta_texto)

            # 3. Gera Áudio Neural (Se o usuário falou por voz)
            if usou_audio:
                with st.spinner("Gerando voz natural..."):
                    # Roda o Edge-TTS (Assíncrono)
                    arquivo_audio = asyncio.run(gerar_audio_neural(resposta_texto))
                    
                    # Toca o áudio automaticamente
                    if arquivo_audio:
                        placeholder_audio.audio(arquivo_audio, format="audio/mp3", autoplay=True)

            # 4. Salva Memória
            st.session_state.memoria_v3.append({"role": "assistant", "content": resposta_texto})

        except Exception as e:
            st.error(f"Ocorreu um erro: {e}")

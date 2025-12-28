import streamlit as st
from groq import Groq
import edge_tts
import asyncio
import os

# --- 1. Configuração ---
st.set_page_config(page_title="Jarvis 2.0", page_icon="🤖")
st.title("Assistente Pessoal")

# --- 2. Conexão ---
try:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
except Exception as e:
    st.error("⚠️ Erro na Chave API.")
    st.stop()

MODEL_ID = "llama-3.3-70b-versatile"

# --- 3. Memória ---
if "memoria_v3" not in st.session_state:
    st.session_state.memoria_v3 = []

# Variável para controlar áudio repetido (O CORRETOR DO BUG)
if "ultimo_audio_processado" not in st.session_state:
    st.session_state.ultimo_audio_processado = None

if st.sidebar.button("🗑️ Limpar Memória"):
    st.session_state.memoria_v3 = []
    st.session_state.ultimo_audio_processado = None
    st.rerun()

# --- FUNÇÕES ---
def ouvir_audio_whisper(audio_bytes):
    try:
        return client.audio.transcriptions.create(
            file=("temp.wav", audio_bytes, "audio/wav"),
            model="whisper-large-v3",
            response_format="text",
            language="pt"
        )
    except Exception as e:
        return None

async def gerar_audio_neural(texto):
    OUTPUT_FILE = "resposta_neural.mp3"
    VOICE = "pt-BR-FranciscaNeural" 
    communicate = edge_tts.Communicate(texto, VOICE)
    await communicate.save(OUTPUT_FILE)
    return OUTPUT_FILE

# --- 4. Interface ---
chat_container = st.container()

with chat_container:
    for message in st.session_state.memoria_v3:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

# --- 5. Inputs Inteligentes ---
st.divider()
col_audio, col_texto = st.columns([0.2, 0.8])

prompt_final = None
# Esta flag define se a IA vai falar ou só escrever
vai_responder_com_audio = False 

# Input Texto
with col_texto:
    prompt_texto = st.chat_input("Digite sua mensagem...")

# Input Áudio
with col_audio:
    audio_gravado = st.audio_input("🎙️")

# --- 6. Lógica de Decisão (CORREÇÃO DO BUG) ---

# REGRA 1: O Texto tem prioridade absoluta. Se digitou, é texto.
if prompt_texto:
    prompt_final = prompt_texto
    vai_responder_com_audio = False # Garante que NÃO vai falar

# REGRA 2: Só processa áudio se não tiver texto E se o áudio for NOVO
elif audio_gravado:
    # Compara se esse áudio é igual ao último que já processamos
    if audio_gravado != st.session_state.ultimo_audio_processado:
        with st.spinner("Ouvindo..."):
            texto_transcrito = ouvir_audio_whisper(audio_gravado)
            
            if texto_transcrito:
                prompt_final = texto_transcrito
                vai_responder_com_audio = True # Aqui sim ativamos a voz
                
                # Marca este áudio como processado para não repetir
                st.session_state.ultimo_audio_processado = audio_gravado

# --- 7. Processamento ---
if prompt_final:
    # Mostra mensagem do usuário
    st.session_state.memoria_v3.append({"role": "user", "content": prompt_final})
    with chat_container.chat_message("user"):
        st.markdown(prompt_final)

    # Resposta da IA
    with chat_container.chat_message("assistant"):
        placeholder_texto = st.empty()
        
        try:
            # Filtro de Segurança
            msgs_api = [{"role": "system", "content": "Você é uma assistente útil e direta. Responda em Português."}]
            for m in st.session_state.memoria_v3:
                if m.get("content"):
                    msgs_api.append({"role": m["role"], "content": str(m["content"])})

            # Gera Texto
            with st.spinner("Pensando..."):
                completion = client.chat.completions.create(
                    model=MODEL_ID,
                    messages=msgs_api,
                    stream=False
                )
                resposta_texto = completion.choices[0].message.content
                placeholder_texto.markdown(resposta_texto)

            # Gera Áudio (SÓ SE O USUÁRIO MANDOU ÁUDIO)
            if vai_responder_com_audio:
                with st.spinner("Gerando voz..."):
                    arquivo_audio = asyncio.run(gerar_audio_neural(resposta_texto))
                    if arquivo_audio:
                        st.audio(arquivo_audio, format="audio/mp3", autoplay=True)

            # Salva
            st.session_state.memoria_v3.append({"role": "assistant", "content": resposta_texto})

        except Exception as e:
            st.error(f"Erro: {e}")

import streamlit as st
from groq import Groq
from gtts import gTTS
import os

# --- 1. Configuração ---
st.set_page_config(page_title="Jarvis Pessoal", page_icon="🎙️")
st.title("Assistente Pessoal (Modo Voz)")

# --- 2. Conexão ---
try:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
except Exception as e:
    st.error("⚠️ Erro na Chave API.")
    st.stop()

MODEL_ID = "llama-3.3-70b-versatile"

# --- 3. Gerenciamento de Memória (Blindado) ---
if "memoria_v3" not in st.session_state:
    st.session_state.memoria_v3 = []

# Botão para limpar
if st.sidebar.button("🗑️ Nova Conversa"):
    st.session_state.memoria_v3 = []
    st.rerun()

# --- FUNÇÕES DE VOZ ---

def ouvir_audio(audio_bytes):
    """Usa o Whisper da Groq para transcrever áudio em texto"""
    try:
        transcription = client.audio.transcriptions.create(
            file=("temp.wav", audio_bytes, "audio/wav"),
            model="whisper-large-v3", # Modelo de ouvido da Groq
            response_format="text",
            language="pt"
        )
        return transcription
    except Exception as e:
        st.error(f"Erro ao ouvir: {e}")
        return None

def falar_texto(texto):
    """Transforma texto em áudio usando Google TTS"""
    try:
        tts = gTTS(text=texto, lang='pt', slow=False)
        filename = "resposta_audio.mp3"
        tts.save(filename)
        return filename
    except Exception as e:
        st.warning(f"Não consegui gerar o áudio: {e}")
        return None

# --- 4. Interface ---

# Mostra o histórico visual
for message in st.session_state.memoria_v3:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# --- 5. Entradas (Voz ou Texto) ---
col1, col2 = st.columns([0.8, 0.2])

# Variável para guardar o prompt final
prompt_usuario = None
usou_audio = False

# A. Entrada de Áudio (Novo!)
audio_gravado = st.audio_input("🎙️ Clique para gravar")

if audio_gravado:
    with st.spinner("Ouvindo..."):
        texto_transcrito = ouvir_audio(audio_gravado)
        if texto_transcrito:
            prompt_usuario = texto_transcrito
            usou_audio = True

# B. Entrada de Texto (Backup)
prompt_texto = st.chat_input("Ou digite aqui...")
if prompt_texto:
    prompt_usuario = prompt_texto

# --- 6. Processamento ---
if prompt_usuario:
    # Mostra mensagem do usuário
    if not usou_audio: # Se for áudio, o player já aparece, não duplicamos texto
        with st.chat_message("user"):
            st.markdown(prompt_usuario)
    
    st.session_state.memoria_v3.append({"role": "user", "content": prompt_usuario})

    # Gera resposta da IA
    with st.chat_message("assistant"):
        with st.spinner("Pensando..."):
            try:
                # Prepara histórico limpo
                messages_api = [{"role": "system", "content": "Você é uma assistente útil. Responda de forma direta e amigável em Português."}]
                for m in st.session_state.memoria_v3:
                    if m.get("content"):
                        messages_api.append({"role": m["role"], "content": str(m["content"])})

                # Chama Llama 3
                completion = client.chat.completions.create(
                    model=MODEL_ID,
                    messages=messages_api,
                    stream=False
                )
                
                resposta = completion.choices[0].message.content
                st.markdown(resposta)
                
                # Gera o Áudio da resposta
                if usou_audio: # Só fala se o usuário falou com ela (para não ser chato no chat de texto)
                    arquivo_audio = falar_texto(resposta)
                    if arquivo_audio:
                        st.audio(arquivo_audio, format="audio/mp3", autoplay=True)

                # Salva na memória
                st.session_state.memoria_v3.append({"role": "assistant", "content": resposta})

            except Exception as e:
                st.error(f"Erro: {e}")

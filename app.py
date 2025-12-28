import streamlit as st
from groq import Groq
import edge_tts
import asyncio
from duckduckgo_search import DDGS # O Mecanismo de Busca

# --- 1. Configuração ---
st.set_page_config(page_title="Jarvis Supremo", page_icon="🌐")
st.title("Assistente Pessoal (Conectado)")

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
if "ultimo_audio_processado" not in st.session_state:
    st.session_state.ultimo_audio_processado = None

# --- SIDEBAR DE CONTROLE ---
with st.sidebar:
    st.header("Configurações")
    # O Interruptor da Internet
    modo_internet = st.toggle("🌍 Modo Internet", value=False, help="Ative para a IA pesquisar dados atuais na web.")
    
    if st.button("🗑️ Limpar Memória"):
        st.session_state.memoria_v3 = []
        st.session_state.ultimo_audio_processado = None
        st.rerun()

# --- FUNÇÕES ---

def pesquisar_web(termo):
    """Busca no DuckDuckGo e retorna os primeiros resultados"""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(termo, max_results=3))
            if results:
                # Formata os resultados para a IA ler
                contexto = "\n".join([f"- {r['title']}: {r['body']}" for r in results])
                return contexto
    except Exception as e:
        return None
    return None

def ouvir_audio_whisper(audio_bytes):
    try:
        return client.audio.transcriptions.create(
            file=("temp.wav", audio_bytes, "audio/wav"),
            model="whisper-large-v3",
            response_format="text",
            language="pt"
        )
    except:
        return None

async def gerar_audio_neural(texto):
    OUTPUT_FILE = "resposta_neural.mp3"
    VOICE = "pt-BR-FranciscaNeural" 
    communicate = edge_tts.Communicate(texto, VOICE)
    await communicate.save(OUTPUT_FILE)
    return OUTPUT_FILE

# --- 4. Interface Visual ---
chat_container = st.container()

with chat_container:
    for message in st.session_state.memoria_v3:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

# --- 5. Inputs ---
st.divider()
col_audio, col_texto = st.columns([0.2, 0.8])

prompt_final = None
vai_responder_com_audio = False 

with col_texto:
    prompt_texto = st.chat_input("Digite sua mensagem...")

with col_audio:
    audio_gravado = st.audio_input("🎙️")

# --- 6. Lógica de Decisão ---
if prompt_texto:
    prompt_final = prompt_texto
    vai_responder_com_audio = False

elif audio_gravado:
    if audio_gravado != st.session_state.ultimo_audio_processado:
        with st.spinner("Ouvindo..."):
            texto_transcrito = ouvir_audio_whisper(audio_gravado)
            if texto_transcrito:
                prompt_final = texto_transcrito
                vai_responder_com_audio = True 
                st.session_state.ultimo_audio_processado = audio_gravado

# --- 7. Processamento e Resposta ---
if prompt_final:
    # Mostra mensagem do usuário
    st.session_state.memoria_v3.append({"role": "user", "content": prompt_final})
    with chat_container.chat_message("user"):
        st.markdown(prompt_final)

    # Resposta da IA
    with chat_container.chat_message("assistant"):
        placeholder_texto = st.empty()
        
        try:
            # A. PESQUISA NA WEB (Se o modo estiver ligado)
            contexto_extra = ""
            if modo_internet:
                with st.spinner("🔍 Pesquisando na web..."):
                    dados_web = pesquisar_web(prompt_final)
                    if dados_web:
                        contexto_extra = f"\n\n[DADOS DA WEB EM TEMPO REAL]:\n{dados_web}\nUse esses dados para responder se for relevante."
            
            # B. Prepara Mensagens
            msgs_api = [{"role": "system", "content": "Você é uma assistente útil e direta. Responda em Português."}]
            
            # Injeta o contexto da web na última mensagem
            msgs_api.append({"role": "user", "content": prompt_final + contexto_extra})

            # Adiciona histórico anterior (opcional, para manter contexto da conversa)
            # Para economizar tokens na busca, as vezes enviamos só a última com contexto, 
            # mas vamos manter simples aqui enviando só a atual turbinada.
            
            # C. Gera Texto
            with st.spinner("Pensando..."):
                completion = client.chat.completions.create(
                    model=MODEL_ID,
                    messages=msgs_api,
                    stream=False
                )
                resposta_texto = completion.choices[0].message.content
                placeholder_texto.markdown(resposta_texto)

            # D. Gera Áudio (Se necessário)
            if vai_responder_com_audio:
                with st.spinner("Gerando voz..."):
                    arquivo_audio = asyncio.run(gerar_audio_neural(resposta_texto))
                    if arquivo_audio:
                        st.audio(arquivo_audio, format="audio/mp3", autoplay=True)

            # Salva
            st.session_state.memoria_v3.append({"role": "assistant", "content": resposta_texto})

        except Exception as e:
            st.error(f"Erro: {e}")

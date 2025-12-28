import streamlit as st
from groq import Groq
from tavily import TavilyClient # O Buscador Profissional
import edge_tts
import asyncio

# --- 1. Configuração ---
st.set_page_config(page_title="Jarvis Conectado", page_icon="🌐")
st.title("Assistente Pessoal (Tavily + Llama)")

# --- 2. Conexão e Chaves ---
try:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
    tavily = TavilyClient(api_key=st.secrets["TAVILY_API_KEY"])
except Exception as e:
    st.error("⚠️ Erro nas Chaves API. Verifique os Secrets (Groq e Tavily).")
    st.stop()

MODEL_ID = "llama-3.3-70b-versatile"

# --- 3. Memória ---
if "memoria_v3" not in st.session_state:
    st.session_state.memoria_v3 = []
if "ultimo_audio" not in st.session_state:
    st.session_state.ultimo_audio = None

# --- SIDEBAR ---
with st.sidebar:
    # Interruptor da Internet (Ligado por padrão para testar)
    modo_internet = st.toggle("🌍 Acesso à Internet", value=True)
    if st.button("🗑️ Limpar Tudo"):
        st.session_state.memoria_v3 = []
        st.session_state.ultimo_audio = None
        st.rerun()

# --- FUNÇÕES ---
def buscar_na_web_tavily(pergunta):
    """Usa a IA da Tavily para ler a internet e resumir a resposta"""
    try:
        # search_depth="basic" gasta 1 crédito. "advanced" gasta 2.
        response = tavily.search(query=pergunta, search_depth="basic", max_results=2)
        
        # Monta um resumo do que achou
        contexto = []
        for r in response.get('results', []):
            contexto.append(f"- Fonte: {r['title']}\n  Resumo: {r['content']}")
        
        return "\n\n".join(contexto)
    except Exception as e:
        return f"Erro na busca: {e}"

def ouvir_audio(audio_bytes):
    try:
        return client.audio.transcriptions.create(
            file=("temp.wav", audio_bytes, "audio/wav"),
            model="whisper-large-v3",
            response_format="text",
            language="pt"
        )
    except: return None

async def falar(texto):
    OUTPUT = "resposta.mp3"
    VOICE = "pt-BR-FranciscaNeural"
    await edge_tts.Communicate(texto, VOICE).save(OUTPUT)
    return OUTPUT

# --- 4. Interface ---
chat_container = st.container()
with chat_container:
    for m in st.session_state.memoria_v3:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

st.divider()
col1, col2 = st.columns([0.2, 0.8])

texto_input = None
falar_resposta = False

with col2:
    if txt := st.chat_input("Mensagem..."):
        texto_input = txt

with col1:
    if audio := st.audio_input("🎙️"):
        if audio != st.session_state.ultimo_audio:
            st.session_state.ultimo_audio = audio
            with st.spinner("Ouvindo..."):
                if transcricao := ouvir_audio(audio):
                    texto_input = transcricao
                    falar_resposta = True

# --- 5. Cérebro ---
if texto_input:
    # Mostra User
    st.session_state.memoria_v3.append({"role": "user", "content": texto_input})
    with chat_container.chat_message("user"):
        st.markdown(texto_input)

    # Gera Resposta
    with chat_container.chat_message("assistant"):
        placeholder = st.empty()
        
        # A. Busca na Web (Se necessário)
        dados_web = ""
        if modo_internet:
            with st.spinner("🌍 Lendo a internet com Tavily..."):
                # A Tavily é inteligente, ela lê e resume o conteúdo real das páginas
                resultado_busca = buscar_na_web_tavily(texto_input)
                if resultado_busca and "Erro" not in resultado_busca:
                    dados_web = f"\n\n[DADOS ATUALIZADOS DA WEB]:\n{resultado_busca}\nUse esses dados para responder."
        
        # B. Prompt Final
        msgs = [{"role": "system", "content": "Você é uma assistente útil e atualizada. Use os dados da web fornecidos para responder perguntas factuais."}]
        # Adiciona histórico recente + contexto da web na última mensagem
        for m in st.session_state.memoria_v3[:-1]: # Histórico anterior
             if m.get("content"): msgs.append({"role": m["role"], "content": str(m["content"])})
        
        # Última mensagem com o "superpoder" da web
        msgs.append({"role": "user", "content": texto_input + dados_web})

        # C. Chama Llama 3
        try:
            with st.spinner("Pensando..."):
                stream = client.chat.completions.create(model=MODEL_ID, messages=msgs, stream=False)
                resp = stream.choices[0].message.content
                placeholder.markdown(resp)
                
                if falar_resposta:
                    with st.spinner("Falando..."):
                        audio_file = asyncio.run(falar(resp))
                        st.audio(audio_file, format="audio/mp3", autoplay=True)
                
                st.session_state.memoria_v3.append({"role": "assistant", "content": resp})
        
        except Exception as e:
            st.error(f"Erro: {e}")

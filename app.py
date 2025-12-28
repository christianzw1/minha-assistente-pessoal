import streamlit as st
from groq import Groq
from tavily import TavilyClient
import edge_tts
import asyncio

# --- 1. Configuração ---
st.set_page_config(page_title="IA Personalizável", page_icon="🎭")

# --- 2. Conexão ---
try:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
    tavily = TavilyClient(api_key=st.secrets["TAVILY_API_KEY"])
except Exception as e:
    st.error("⚠️ Erro nas Chaves API. Verifique os Secrets.")
    st.stop()

MODEL_ID = "llama-3.3-70b-versatile"

# --- 3. Memória ---
if "memoria_v3" not in st.session_state:
    st.session_state.memoria_v3 = []
if "ultimo_audio" not in st.session_state:
    st.session_state.ultimo_audio = None

# --- 4. SIDEBAR (O Centro de Comando) ---
with st.sidebar:
    st.title("⚙️ Configurações")
    
    # A. Identidade
    st.subheader("Identidade")
    nome_ia = st.text_input("Nome da IA:", value="Jarvis")
    personalidade = st.text_area("Personalidade / Instruções:", 
                                 value="Você é um assistente extremamente inteligente, sarcástico e direto. Você não gosta de enrolação.",
                                 height=100)
    
    # B. Recursos
    st.subheader("Recursos")
    modo_internet = st.toggle("🌍 Acesso à Internet", value=True)
    
    # C. Limpeza
    st.divider()
    if st.button("🗑️ Resetar Memória"):
        st.session_state.memoria_v3 = []
        st.session_state.ultimo_audio = None
        st.rerun()

st.title(f"Chat com {nome_ia}")

# --- FUNÇÕES ---

def cerebro_decisor(pergunta):
    """Decide se busca ou responde (Mantido para eficiência)"""
    termos_obrigatorios = ["hoje", "agora", "cotação", "preço", "valor", "notícia", "tempo", "dólar", "quem ganhou"]
    if any(termo in pergunta.lower() for termo in termos_obrigatorios): return True

    system_prompt = "Você é um classificador. Se a pergunta precisa de dados recentes/reais, responda 'BUSCAR'. Se não, 'RESPONDER'."
    try:
        completion = client.chat.completions.create(
            model=MODEL_ID,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": pergunta}],
            temperature=0
        )
        return "BUSCAR" in completion.choices[0].message.content.strip().upper()
    except: return False

def buscar_tavily(pergunta):
    try:
        response = tavily.search(query=pergunta, search_depth="basic", max_results=3)
        contexto = []
        for r in response.get('results', []):
            contexto.append(f"- {r['title']}: {r['content']}")
        return "\n".join(contexto)
    except: return None

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

# --- INTERFACE ---
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
    if txt := st.chat_input(f"Fale com {nome_ia}..."):
        texto_input = txt

with col1:
    if audio := st.audio_input("🎙️"):
        if audio != st.session_state.ultimo_audio:
            st.session_state.ultimo_audio = audio
            with st.spinner("Ouvindo..."):
                if transcricao := ouvir_audio(audio):
                    texto_input = transcricao
                    falar_resposta = True

# --- PROCESSAMENTO ---
if texto_input:
    st.session_state.memoria_v3.append({"role": "user", "content": texto_input})
    with chat_container.chat_message("user"):
        st.markdown(texto_input)

    with chat_container.chat_message("assistant"):
        placeholder = st.empty()
        dados_web = ""
        
        # Decisão de Busca
        if modo_internet:
            with st.status(f"🧠 {nome_ia} está pensando...", expanded=True) as status:
                if cerebro_decisor(texto_input):
                    status.write("🌍 Buscando informações...")
                    raw_data = buscar_tavily(texto_input)
                    if raw_data:
                        dados_web = f"\n\n[DADOS DA WEB]:\n{raw_data}\n"
                        status.update(label="✅ Informação encontrada!", state="complete", expanded=False)
                    else:
                        status.update(label="❌ Nada encontrado.", state="error")
                else:
                    status.update(label="📚 Memória interna.", state="complete", expanded=False)

        # Prompt com PERSONALIDADE DINÂMICA
        try:
            with st.spinner("Digitando..."):
                # AQUI É O PULO DO GATO: Injetamos o nome e personalidade escolhidos
                system_instruction = f"""
                Seu nome é {nome_ia}.
                Sua personalidade/instruções são: {personalidade}
                Responda sempre em Português do Brasil.
                Use os dados da web fornecidos se houver.
                """
                
                msgs = [{"role": "system", "content": system_instruction}]
                for m in st.session_state.memoria_v3[:-1]:
                    if m.get("content"): msgs.append({"role": m["role"], "content": str(m["content"])})
                
                msgs.append({"role": "user", "content": texto_input + dados_web})

                stream = client.chat.completions.create(model=MODEL_ID, messages=msgs, stream=False)
                resp = stream.choices[0].message.content
                placeholder.markdown(resp)
                
                if falar_resposta:
                    audio_file = asyncio.run(falar(resp))
                    st.audio(audio_file, format="audio/mp3", autoplay=True)
                
                st.session_state.memoria_v3.append({"role": "assistant", "content": resp})
        
        except Exception as e:
            st.error(f"Erro: {e}")

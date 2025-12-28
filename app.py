import streamlit as st
from groq import Groq
from tavily import TavilyClient
import edge_tts
import asyncio

# --- 1. Configuração ---
st.set_page_config(page_title="Jarvis Autônomo", page_icon="🧠")
st.title("Assistente Autônomo (Auto-Internet)")

# --- 2. Conexão ---
try:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
    tavily = TavilyClient(api_key=st.secrets["TAVILY_API_KEY"])
except Exception as e:
    st.error("⚠️ Erro nas Chaves API. Verifique os Secrets.")
    st.stop()

# Modelo Principal (Cérebro)
MODEL_ID = "llama-3.3-70b-versatile"
# Modelo Rápido (Para decidir se busca ou não - economiza tempo)
ROUTER_MODEL = "llama3-8b-8192" 

# --- 3. Memória ---
if "memoria_v3" not in st.session_state:
    st.session_state.memoria_v3 = []
if "ultimo_audio" not in st.session_state:
    st.session_state.ultimo_audio = None

# Sidebar Limpa (Sem botão de internet, agora é automático)
if st.sidebar.button("🗑️ Limpar Tudo"):
    st.session_state.memoria_v3 = []
    st.session_state.ultimo_audio = None
    st.rerun()

# --- FUNÇÕES INTELIGENTES ---

def cerebro_decisor(pergunta):
    """
    Esta função é o 'Router'. Ela decide SE precisa buscar na web.
    Retorna: True (Buscar) ou False (Responder direto)
    """
    system_prompt = """
    Você é um classificador de intenção. Analise a pergunta do usuário.
    - Se a pergunta pedir dados em tempo real (cotações, clima, notícias, jogos, eventos recentes), responda 'BUSCAR'.
    - Se for conversa fiada, ajuda técnica, código, resumo ou conhecimento geral atemporal, responda 'RESPONDER'.
    Responda APENAS uma palavra: 'BUSCAR' ou 'RESPONDER'.
    """
    
    try:
        completion = client.chat.completions.create(
            model=ROUTER_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": pergunta}
            ],
            temperature=0
        )
        decisao = completion.choices[0].message.content.strip().upper()
        return "BUSCAR" in decisao
    except:
        return False # Na dúvida, não busca

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
    if txt := st.chat_input("Pergunte algo..."):
        texto_input = txt

with col1:
    if audio := st.audio_input("🎙️"):
        if audio != st.session_state.ultimo_audio:
            st.session_state.ultimo_audio = audio
            with st.spinner("Ouvindo..."):
                if transcricao := ouvir_audio(audio):
                    texto_input = transcricao
                    falar_resposta = True

# --- 5. Fluxo Principal (O AGENTE) ---
if texto_input:
    # Mostra User
    st.session_state.memoria_v3.append({"role": "user", "content": texto_input})
    with chat_container.chat_message("user"):
        st.markdown(texto_input)

    with chat_container.chat_message("assistant"):
        placeholder = st.empty()
        dados_web = ""
        
        # --- PASSO 1: O CÉREBRO DECIDE ---
        with st.status("🧠 Analisando sua pergunta...", expanded=True) as status:
            precisa_busca = cerebro_decisor(texto_input)
            
            if precisa_busca:
                status.write("🌍 Decidi pesquisar na web!")
                raw_data = buscar_tavily(texto_input)
                if raw_data:
                    dados_web = f"\n\n[DADOS DA INTERNET]:\n{raw_data}\n"
                    status.update(label="✅ Dados encontrados!", state="complete", expanded=False)
                else:
                    status.update(label="❌ Falha na busca (seguindo sem dados)", state="error")
            else:
                status.write("📚 Usando conhecimento interno.")
                status.update(label="✅ Respondendo direto", state="complete", expanded=False)

        # --- PASSO 2: GERA RESPOSTA ---
        try:
            with st.spinner("Formulando resposta..."):
                # Monta o prompt com ou sem dados da web
                msgs = [{"role": "system", "content": "Você é uma assistente prestativa. Se receber dados da internet, use-os. Se não, use seu conhecimento."}]
                for m in st.session_state.memoria_v3[:-1]:
                    if m.get("content"): msgs.append({"role": m["role"], "content": str(m["content"])})
                
                msgs.append({"role": "user", "content": texto_input + dados_web})

                stream = client.chat.completions.create(model=MODEL_ID, messages=msgs, stream=False)
                resp = stream.choices[0].message.content
                placeholder.markdown(resp)
                
                # --- PASSO 3: FALA (Se necessário) ---
                if falar_resposta:
                    audio_file = asyncio.run(falar(resp))
                    st.audio(audio_file, format="audio/mp3", autoplay=True)
                
                st.session_state.memoria_v3.append({"role": "assistant", "content": resp})
        
        except Exception as e:
            st.error(f"Erro: {e}")

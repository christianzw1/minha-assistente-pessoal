import streamlit as st
from groq import Groq
from tavily import TavilyClient
import edge_tts
import asyncio

# --- 1. Configuração ---
st.set_page_config(page_title="Jarvis Pro", page_icon="🧠")
st.title("Assistente Autônomo (V2 Blindada)")

# --- 2. Conexão ---
try:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
    tavily = TavilyClient(api_key=st.secrets["TAVILY_API_KEY"])
except Exception as e:
    st.error("⚠️ Erro nas Chaves API.")
    st.stop()

# Usaremos o modelo POTENTE para tudo agora, para evitar erros de julgamento
MODEL_ID = "llama-3.3-70b-versatile"

# --- 3. Memória ---
if "memoria_v3" not in st.session_state:
    st.session_state.memoria_v3 = []
if "ultimo_audio" not in st.session_state:
    st.session_state.ultimo_audio = None

if st.sidebar.button("🗑️ Limpar Tudo"):
    st.session_state.memoria_v3 = []
    st.session_state.ultimo_audio = None
    st.rerun()

# --- FUNÇÕES INTELIGENTES ---

def cerebro_decisor(pergunta):
    """
    Decide se busca ou não. Agora com 3 camadas de segurança.
    """
    # 1. REDE DE SEGURANÇA (Palavras-chave que OBRIGAM a busca)
    termos_obrigatorios = ["hoje", "agora", "cotação", "preço", "valor", "notícia", 
                          "clima", "tempo", "dólar", "euro", "bitcoin", "jogo", "resultado", 
                          "lançamento", "último", "atual", "quem ganhou"]
    
    if any(termo in pergunta.lower() for termo in termos_obrigatorios):
        return True # Força a busca sem nem perguntar pra IA

    # 2. DECISÃO DA IA (Com prompt reforçado)
    system_prompt = """
    Você é um Supervisor de Busca. Sua única função é dizer 'BUSCAR' ou 'RESPONDER'.
    
    Regras RÍGIDAS:
    - Perguntas sobre fatos atuais, preços, eventos recentes, clima ou pessoas vivas -> DIGA 'BUSCAR'.
    - Perguntas teóricas, ajuda com código, traduções, poemas ou papo furado -> DIGA 'RESPONDER'.
    
    Exemplos:
    User: "Quanto tá o dólar?" -> Assistant: BUSCAR
    User: "Quem é o presidente do Brasil?" -> Assistant: BUSCAR
    User: "Crie um poema." -> Assistant: RESPONDER
    User: "O que é Python?" -> Assistant: RESPONDER
    """
    
    try:
        completion = client.chat.completions.create(
            model=MODEL_ID, # Usando o 70b para ser mais esperto
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": pergunta}
            ],
            temperature=0
        )
        decisao = completion.choices[0].message.content.strip().upper()
        return "BUSCAR" in decisao
    except:
        return False

def buscar_tavily(pergunta):
    try:
        # Aumentei para 'advanced' se quiser mais precisão, mas 'basic' é mais rápido
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

# --- 5. Fluxo Principal ---
if texto_input:
    st.session_state.memoria_v3.append({"role": "user", "content": texto_input})
    with chat_container.chat_message("user"):
        st.markdown(texto_input)

    with chat_container.chat_message("assistant"):
        placeholder = st.empty()
        dados_web = ""
        
        # DECISÃO
        with st.status("🧠 Pensando...", expanded=True) as status:
            precisa_busca = cerebro_decisor(texto_input)
            
            if precisa_busca:
                status.write("🌍 Buscando informações atualizadas...")
                raw_data = buscar_tavily(texto_input)
                if raw_data:
                    dados_web = f"\n\n[DADOS DA INTERNET]:\n{raw_data}\n"
                    status.update(label="✅ Encontrei dados na rede!", state="complete", expanded=False)
                else:
                    status.update(label="❌ Erro na busca (tentando sem dados)", state="error", expanded=False)
            else:
                status.update(label="📚 Usando conhecimento interno", state="complete", expanded=False)

        # RESPOSTA
        try:
            with st.spinner("Formulando resposta..."):
                msgs = [{"role": "system", "content": "Você é uma assistente prestativa. Use os dados da web se fornecidos. Responda em Português."}]
                for m in st.session_state.memoria_v3[:-1]:
                    if m.get("content"): msgs.append({"role": m["role"], "content": str(m["content"])})
                
                msgs.append({"role": "user", "content": texto_input + dados_web})

                stream = client.chat.completions.create(model=MODEL_ID, messages=msgs, stream=False)
                resp = stream.choices[0].message.content
                placeholder.markdown(resp)
                
                if falar_resposta:
                    with st.spinner("Gerando áudio..."):
                        audio_file = asyncio.run(falar(resp))
                        st.audio(audio_file, format="audio/mp3", autoplay=True)
                
                st.session_state.memoria_v3.append({"role": "assistant", "content": resp})
        
        except Exception as e:
            st.error(f"Erro: {e}")

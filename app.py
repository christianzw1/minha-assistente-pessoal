import streamlit as st
from groq import Groq

# Configuração da Página
st.set_page_config(page_title="Minha Assistente Suprema", page_icon="🧠")

st.title("Assistente Pessoal - Gemma 2")

# Botão para limpar a memória se der erro
if st.sidebar.button("🗑️ Limpar Memória"):
    st.session_state.messages = []
    st.rerun()

# Inicializa o cliente Groq
try:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
except Exception as e:
    st.error(f"Erro na chave da API. Verifique os Secrets. Detalhe: {e}")
    st.stop()

# Modelo Llama 3.3 (O mais inteligente e grátis)
MODEL_ID = "llama-3.3-70b-versatile"

# Inicializa o histórico de chat
if "messages" not in st.session_state:
    st.session_state.messages = []

# Mostra as mensagens antigas na tela
for message in st.session_state.messages:
    if message["content"]: # Só mostra se tiver conteúdo
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

# Caixa de entrada do usuário
if prompt := st.chat_input("No que posso ajudar hoje, Christian?"):
    # 1. Mostra a mensagem do usuário
    st.chat_message("user").markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})

    # 2. Prepara o histórico BLINDADO (Remove mensagens vazias ou com erro)
    safe_history = [
        {"role": m["role"], "content": str(m["content"])} 
        for m in st.session_state.messages 
        if m["content"] is not None
    ]

    # 3. Chama a IA para responder
    with st.chat_message("assistant"):
        try:
            stream = client.chat.completions.create(
                model=MODEL_ID,
                messages=[
                    {"role": "system", "content": "Você é uma assistente pessoal suprema, inteligente e útil. Seu nome é Gemma. Responda sempre em Português do Brasil."},
                    *safe_history
                ],
                stream=True,
            )
            response = st.write_stream(stream)
            
            # Só salva se a resposta for válida
            if response:
                st.session_state.messages.append({"role": "assistant", "content": response})
                
        except Exception as e:
            st.error(f"Erro ao gerar resposta: {e}")

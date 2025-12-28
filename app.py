import streamlit as st
from groq import Groq

# Configuração da Página
st.set_page_config(page_title="Minha Assistente Suprema", page_icon="🧠")

st.title("Assistente Pessoal - Gemma 2")

# Inicializa o cliente Groq usando a chave secreta (vamos configurar isso já já)
# O Streamlit busca automaticamente em st.secrets["GROQ_API_KEY"]
try:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
except:
    st.error("A chave da API não foi encontrada. Configure os 'Secrets' no Streamlit Cloud.")
    st.stop()

# Inicializa o modelo (Gemma 2 9b)
MODEL_ID = "llama-3.3-70b-versatile"

# Inicializa o histórico de chat se não existir
if "messages" not in st.session_state:
    st.session_state.messages = []

# Mostra as mensagens antigas na tela
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# Caixa de entrada do usuário
if prompt := st.chat_input("No que posso ajudar hoje, Christian?"):
    # 1. Mostra a mensagem do usuário
    st.chat_message("user").markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})

    # 2. Chama a IA para responder
    with st.chat_message("assistant"):
        stream = client.chat.completions.create(
            model=MODEL_ID,
            messages=[
                {"role": "system", "content": "Você é uma assistente pessoal suprema, inteligente e útil. Seu nome é Gemma. Responda sempre em Português do Brasil."},
                *st.session_state.messages # Passa todo o histórico para ela ter memória curta
            ],
            stream=True,
        )
        response = st.write_stream(stream)
    
    # 3. Salva a resposta da IA no histórico
    st.session_state.messages.append({"role": "assistant", "content": response})

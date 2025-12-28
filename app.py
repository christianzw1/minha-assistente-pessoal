import streamlit as st
from groq import Groq

# Configuração da Página
st.set_page_config(page_title="Minha Assistente", page_icon="🤖")

st.title("Assistente Pessoal (Llama 3.3)")

# --- TRANSPLANTE DE MEMÓRIA ---
# Mudamos o nome da chave para 'historico_blindado' para ignorar qualquer erro antigo
if "historico_blindado" not in st.session_state:
    st.session_state.historico_blindado = []

# Conexão com a API
try:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
except Exception as e:
    st.error("⚠️ Erro na Chave API. Verifique os Secrets.")
    st.stop()

MODEL_ID = "llama-3.3-70b-versatile"

# 1. Mostra o histórico (Agora usando a nova memória limpa)
for message in st.session_state.historico_blindado:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# 2. Caixa de Texto
if prompt := st.chat_input("Digite aqui..."):
    # Mostra e salva a mensagem do usuário
    st.chat_message("user").markdown(prompt)
    st.session_state.historico_blindado.append({"role": "user", "content": prompt})

    # 3. Gera a resposta
    with st.chat_message("assistant"):
        try:
            # Prepara as mensagens para envio (Garante que tudo seja texto)
            messages_api = [
                {"role": "system", "content": "Você é uma assistente útil. Responda em Português."}
            ]
            for m in st.session_state.historico_blindado:
                messages_api.append({"role": m["role"], "content": str(m["content"])})

            # Chama o Llama 3.3
            stream = client.chat.completions.create(
                model=MODEL_ID,
                messages=messages_api,
                stream=True
            )
            
            # Escreve na tela
            response = st.write_stream(stream)
            
            # Salva na nova memória
            st.session_state.historico_blindado.append({"role": "assistant", "content": response})

        except Exception as e:
            st.error(f"Erro: {e}")

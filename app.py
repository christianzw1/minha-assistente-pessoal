import streamlit as st
from groq import Groq

# --- 1. Configuração da Página ---
st.set_page_config(page_title="Minha Assistente", page_icon="🤖")
st.title("Assistente Pessoal (Llama 3.3)")

# --- 2. Auto-Reparo da Memória (O Segredo) ---
# Isso roda antes de tudo. Se houver sujeira na memória, ele limpa.
if "messages" not in st.session_state:
    st.session_state.messages = []

# Filtra mensagens inválidas (Remove Nones ou lixo que causam erro 400)
st.session_state.messages = [
    msg for msg in st.session_state.messages 
    if msg.get("content") is not None and str(msg.get("content")).strip() != ""
]

# --- 3. Conexão com o Cérebro ---
try:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
except Exception as e:
    st.error("⚠️ Erro na Chave API. Verifique os Secrets.")
    st.stop()

MODEL_ID = "llama-3.3-70b-versatile"

# --- 4. Interface de Chat ---
# Mostra o histórico limpo
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# --- 5. O Cérebro em Ação ---
if prompt := st.chat_input("Digite aqui..."):
    # Salva a pergunta do usuário
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Gera a resposta
    with st.chat_message("assistant"):
        try:
            # Prepara as mensagens garantindo que são todas strings
            safe_messages = [
                {"role": "system", "content": "Você é uma assistente útil. Responda em Português."}
            ]
            for m in st.session_state.messages:
                safe_messages.append({"role": m["role"], "content": str(m["content"])})

            # Chama a IA
            stream = client.chat.completions.create(
                model=MODEL_ID,
                messages=safe_messages,
                stream=True,
                temperature=0.7
            )
            
            # Escreve na tela (efeito digitação)
            response = st.write_stream(stream)
            
            # Salva na memória APENAS se a resposta for válida
            if response:
                st.session_state.messages.append({"role": "assistant", "content": response})
            else:
                # Se a resposta vier vazia, forçamos um recarregamento para não sujar a memória
                st.rerun()

        except Exception as e:
            st.error(f"Erro na comunicação: {e}")
            # Se der erro, limpamos a última mensagem para tentar de novo
            if st.session_state.messages and st.session_state.messages[-1]["role"] == "user":
                st.session_state.messages.pop()

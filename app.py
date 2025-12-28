import streamlit as st
from groq import Groq

# --- Configuração Inicial ---
st.set_page_config(page_title="Assistente Pessoal", page_icon="🤖")

st.title("Minha Assistente Pessoal (Llama 3.3)")

# --- Configuração da API ---
try:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
except Exception as e:
    st.error("⚠️ Erro: Chave da API não encontrada. Verifique os 'Secrets'.")
    st.stop()

# Modelo atualizado e funcional
MODEL_ID = "llama-3.3-70b-versatile"

# --- Gerenciamento de Memória ---
if "messages" not in st.session_state:
    st.session_state.messages = []

# Botão para limpar histórico (caso trave)
if st.sidebar.button("🗑️ Limpar Conversa"):
    st.session_state.messages = []
    st.rerun()

# --- 1. Mostrar Histórico na Tela ---
# Aqui a gente protege para não tentar mostrar mensagens vazias
for message in st.session_state.messages:
    if message.get("content"):
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

# --- 2. Processar Nova Mensagem ---
if prompt := st.chat_input("Digite sua mensagem..."):
    # Mostra mensagem do usuário
    st.chat_message("user").markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})

    # --- BLINDAGEM: Prepara histórico limpo para a IA ---
    # Removemos qualquer mensagem que não tenha texto (None) para evitar erro 400
    safe_messages = [
        {"role": "system", "content": "Você é uma assistente pessoal útil e inteligente. Responda em Português do Brasil."}
    ]
    
    for m in st.session_state.messages:
        if m.get("content") and isinstance(m["content"], str):
            safe_messages.append({"role": m["role"], "content": m["content"]})

    # Chama a IA
    with st.chat_message("assistant"):
        try:
            stream = client.chat.completions.create(
                model=MODEL_ID,
                messages=safe_messages,
                stream=True,
                temperature=0.7
            )
            
            # Escreve a resposta na tela em tempo real
            response = st.write_stream(stream)
            
            # --- SALVAMENTO SEGURO ---
            # Só salvamos no histórico se a resposta não for vazia
            if response:
                st.session_state.messages.append({"role": "assistant", "content": response})
                
        except Exception as e:
            st.error(f"Erro ao gerar resposta: {e}")
            # Se der erro, não salvamos nada corrompido no histórico

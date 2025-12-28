import streamlit as st
from groq import Groq

# --- 1. Configuração da Página ---
st.set_page_config(page_title="Minha Assistente", page_icon="🤖")
st.title("Assistente Pessoal (Llama 3.3)")

# --- 2. Correção Automática de Memória (A CIRURGIA) ---
if "messages" not in st.session_state:
    st.session_state.messages = []

# Função para garantir que a conversa seja sempre: User -> Assistant -> User
def limpar_historico_corrompido():
    if not st.session_state.messages:
        return
    
    lista_limpa = []
    ultimo_role = None
    
    for msg in st.session_state.messages:
        # Pula mensagens vazias ou nulas
        if not msg.get("content"):
            continue
            
        # Pula mensagens repetidas (Ex: User depois de User)
        if msg["role"] == ultimo_role:
            continue
            
        lista_limpa.append(msg)
        ultimo_role = msg["role"]
    
    st.session_state.messages = lista_limpa

# Executa a limpeza antes de qualquer coisa
limpar_historico_corrompido()

# Botão de emergência (Caso tudo falhe)
if st.sidebar.button("🗑️ Resetar Cérebro"):
    st.session_state.messages = []
    st.rerun()

# --- 3. Conexão ---
try:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
except Exception as e:
    st.error("⚠️ Configure a chave da API nos Secrets!")
    st.stop()

MODEL_ID = "llama-3.3-70b-versatile"

# --- 4. Mostra o Chat ---
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# --- 5. Processa Nova Mensagem ---
if prompt := st.chat_input("Fale comigo..."):
    # Verifica se a última mensagem foi do usuário (para evitar erro de duplo usuário)
    if st.session_state.messages and st.session_state.messages[-1]["role"] == "user":
        st.warning("Aguarde a IA responder antes de mandar outra mensagem.")
        st.stop()

    # Mostra e salva mensagem do usuário
    st.chat_message("user").markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})

    # Gera resposta
    with st.chat_message("assistant"):
        try:
            # Prepara histórico sanitizado (Garante strings)
            historico_api = [
                {"role": "system", "content": "Você é uma assistente útil e amigável. Responda em Português do Brasil."}
            ]
            for m in st.session_state.messages:
                historico_api.append({"role": m["role"], "content": str(m["content"])})

            stream = client.chat.completions.create(
                model=MODEL_ID,
                messages=historico_api,
                stream=True
            )
            
            response = st.write_stream(stream)
            
            # Só salva se deu certo
            if response:
                st.session_state.messages.append({"role": "assistant", "content": response})
            else:
                # Se a resposta veio vazia, removemos a pergunta do usuário para não travar na próxima
                st.session_state.messages.pop()
                st.rerun()

        except Exception as e:
            st.error(f"Erro: {e}")
            # Se a API falhar, removemos a última pergunta do usuário para evitar o erro de 'duplo usuário'
            if st.session_state.messages and st.session_state.messages[-1]["role"] == "user":
                st.session_state.messages.pop()

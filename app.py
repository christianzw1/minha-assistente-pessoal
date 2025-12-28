import streamlit as st
from groq import Groq

# --- 1. Configuração da Página ---
st.set_page_config(page_title="Minha Assistente", page_icon="🤖")
st.title("Assistente Pessoal (Llama 3.3)")

# --- 2. Conexão com a API ---
try:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
except Exception as e:
    st.error("⚠️ Erro na Chave API. Verifique os Secrets.")
    st.stop()

MODEL_ID = "llama-3.3-70b-versatile"

# --- 3. Memória Nova (Reset Forçado) ---
# Mudamos o nome para 'memoria_v3' para ignorar qualquer lixo das tentativas anteriores
if "memoria_v3" not in st.session_state:
    st.session_state.memoria_v3 = []

# Botão de emergência na barra lateral
if st.sidebar.button("🗑️ Limpar Tudo"):
    st.session_state.memoria_v3 = []
    st.rerun()

# --- 4. Mostra o Histórico na Tela ---
for message in st.session_state.memoria_v3:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# --- 5. Processamento da Mensagem ---
if prompt := st.chat_input("Escreva aqui..."):
    
    # Salva e mostra a mensagem do usuário
    st.session_state.memoria_v3.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Gera a resposta da IA
    with st.chat_message("assistant"):
        try:
            # --- O GRANDE SEGREDO (FILTRO DE SEGURANÇA) ---
            # Criamos uma lista limpa apenas para enviar para a API (não tocamos na memória visual)
            messages_para_api = [
                {"role": "system", "content": "Você é uma assistente útil e amigável. Responda sempre em Português do Brasil."}
            ]
            
            # Só adicionamos mensagens que TEM CONTEÚDO REAL
            for m in st.session_state.memoria_v3:
                conteudo = str(m.get("content", "")) # Garante que é string
                if len(conteudo.strip()) > 0:       # Só aceita se não for vazio
                    messages_para_api.append({"role": m["role"], "content": conteudo})

            # Chama a IA com a lista limpa
            stream = client.chat.completions.create(
                model=MODEL_ID,
                messages=messages_para_api,
                stream=True
            )
            
            # Escreve na tela
            response = st.write_stream(stream)
            
            # Só salva no histórico se a resposta não for vazia
            if response and len(str(response).strip()) > 0:
                st.session_state.memoria_v3.append({"role": "assistant", "content": response})
            else:
                # Se veio vazio, recarrega para não travar
                st.rerun()

        except Exception as e:
            st.error(f"Erro de conexão: {e}")
            # Se deu erro, remove a última pergunta para não travar o fluxo
            if st.session_state.memoria_v3 and st.session_state.memoria_v3[-1]["role"] == "user":
                st.session_state.memoria_v3.pop()

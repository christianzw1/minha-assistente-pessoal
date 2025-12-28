import streamlit as st
from groq import Groq

st.set_page_config(page_title="Minha Assistente", page_icon="🤖")
st.title("Assistente Pessoal (Llama 3.3)")

try:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
except Exception:
    st.error("⚠️ Erro na Chave API. Verifique os Secrets.")
    st.stop()

MODEL_ID = "llama-3.3-70b-versatile"

if "memoria_v3" not in st.session_state:
    st.session_state.memoria_v3 = []

if st.sidebar.button("🗑️ Limpar Tudo"):
    st.session_state.memoria_v3 = []
    st.rerun()

for message in st.session_state.memoria_v3:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

def groq_text_stream(stream):
    for chunk in stream:
        try:
            delta = chunk.choices[0].delta
            text = getattr(delta, "content", "") or ""
        except Exception:
            text = ""
        if text:
            yield text

if prompt := st.chat_input("Escreva aqui..."):
    st.session_state.memoria_v3.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        try:
            messages_para_api = [
                {"role": "system", "content": "Você é uma assistente útil e amigável. Responda sempre em Português do Brasil."}
            ]

            # só passa strings válidas
            for m in st.session_state.memoria_v3:
                content = m.get("content", "")
                if isinstance(content, str) and content.strip():
                    messages_para_api.append({"role": m["role"], "content": content})

            stream = client.chat.completions.create(
                model=MODEL_ID,
                messages=messages_para_api,
                stream=True
            )

            response = st.write_stream(groq_text_stream(stream))

            if isinstance(response, str) and response.strip():
                st.session_state.memoria_v3.append({"role": "assistant", "content": response})
            else:
                st.rerun()

        except Exception as e:
            st.error(f"Erro de conexão: {e}")
            if st.session_state.memoria_v3 and st.session_state.memoria_v3[-1]["role"] == "user":
                st.session_state.memoria_v3.pop()

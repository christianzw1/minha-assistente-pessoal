import streamlit as st
from groq import Groq
from tavily import TavilyClient
import edge_tts
import asyncio
import json
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

# --- 1. Configuração ---
st.set_page_config(page_title="Jarvis V3", page_icon="🤖")
st.title("Assistente Pessoal (Full Control)")

# --- 2. Conexão ---
try:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
    tavily = TavilyClient(api_key=st.secrets["TAVILY_API_KEY"])
except:
    st.error("⚠️ Erro nas Chaves API. Verifique os Secrets.")
    st.stop()

MODEL_ID = "llama-3.3-70b-versatile"
ARQUIVO_TAREFAS = "tarefas.json"
FUSO_BR = ZoneInfo("America/Sao_Paulo")

# --- 3. Memória ---
if "memoria_v3" not in st.session_state:
    st.session_state.memoria_v3 = []
if "ultimo_audio" not in st.session_state:
    st.session_state.ultimo_audio = None
if "ultima_cobranca" not in st.session_state:
    st.session_state.ultima_cobranca = datetime.min.replace(tzinfo=FUSO_BR)

def carregar_tarefas():
    if not os.path.exists(ARQUIVO_TAREFAS): return []
    try:
        with open(ARQUIVO_TAREFAS, "r") as f: return json.load(f)
    except: return []

def salvar_tarefas(lista):
    with open(ARQUIVO_TAREFAS, "w") as f: json.dump(lista, f)

# --- CÉREBRO: Detector de Intenção (Agora com CONCLUIR) ---
def identificar_intencao(texto):
    texto = texto.lower()
    
    # 1. CONCLUIR TAREFA (O segredo para ela apagar)
    termos_conclusao = ["já fiz", "já fechei", "feito", "conclu", "termin", "apaga", "remove", "tá pronto", "ta pronto"]
    if any(t in texto for t in termos_conclusao):
        return "CONCLUIR"
        
    # 2. AGENDAR
    termos_agenda = ["lembr", "agend", "anot", "marc", "cobr", "avis"]
    if any(t in texto for t in termos_agenda):
        return "AGENDAR"
        
    # 3. BUSCAR WEB
    if any(x in texto for x in ["hoje", "preço", "notícia", "valor", "dólar", "tempo", "quem ganhou"]):
        return "BUSCAR"
        
    return "RESPONDER"

# --- INTELIGÊNCIA: Cruzar Texto com Lista de Tarefas ---
def encontrar_tarefa_para_remover(texto_usuario, lista_tarefas):
    """
    A IA olha a lista e decide qual item o usuário está querendo apagar.
    Retorna o ÍNDICE da tarefa na lista (0, 1, 2...) ou -1 se não achar.
    """
    descricao_tarefas = [f"ID {i}: {t['descricao']}" for i, t in enumerate(lista_tarefas)]
    lista_texto = "\n".join(descricao_tarefas)
    
    prompt = f"""
    Lista de tarefas:
    {lista_texto}
    
    O usuário disse: "{texto_usuario}"
    
    Qual é o ID da tarefa que ele completou/quer apagar?
    Responda APENAS o número (ex: 0). Se nenhuma bater, responda -1.
    """
    
    try:
        resp = client.chat.completions.create(
            model=MODEL_ID, 
            messages=[{"role":"user","content":prompt}],
            temperature=0
        )
        resultado = resp.choices[0].message.content.strip()
        # Tenta extrair só o número caso a IA fale demais
        import re
        numero = re.search(r'-?\d+', resultado)
        return int(numero.group()) if numero else -1
    except: return -1

def extrair_dados_tarefa(texto):
    agora_br = datetime.now(FUSO_BR).strftime("%Y-%m-%d %H:%M")
    prompt = f"""Hoje é {agora_br}. User: "{texto}". Extraia tarefa e data/hora (YYYY-MM-DD HH:MM). Se sem hora, use 18:00 de hoje. JSON: {{"descricao": "...", "data_hora": "..."}}"""
    try:
        resp = client.chat.completions.create(model=MODEL_ID, messages=[{"role":"user","content":prompt}], response_format={"type":"json_object"})
        return json.loads(resp.choices[0].message.content)
    except: return None

def buscar_tavily(q):
    try: return tavily.search(query=q, max_results=2)['results'][0]['content']
    except: return None

def ouvir_audio(b):
    try: return client.audio.transcriptions.create(file=("t.wav", b, "audio/wav"), model="whisper-large-v3", response_format="text", language="pt")
    except: return None

async def falar(t):
    await edge_tts.Communicate(t, "pt-BR-FranciscaNeural").save("alerta.mp3")
    return "alerta.mp3"

# --- INTERFACE ---
col_main, col_agenda = st.columns([0.7, 0.3])

# --- O VIGIA (Automático) ---
tarefas = carregar_tarefas()
agora = datetime.now(FUSO_BR)
mensagem_cobranca = None

for t in tarefas:
    try:
        data_tarefa = datetime.strptime(t['data_hora'], "%Y-%m-%d %H:%M").replace(tzinfo=FUSO_BR)
        tempo_desde_ultima = (agora - st.session_state.ultima_cobranca).total_seconds()
        
        # Cobra se passou da hora E faz mais de 60 segundos da última bronca
        if agora > data_tarefa and tempo_desde_ultima > 60:
            mensagem_cobranca = f"Ei! Já são {agora.strftime('%H:%M')} e a tarefa '{t['descricao']}' venceu! Já fez?"
            st.session_state.ultima_cobranca = agora
            break
    except: pass

if mensagem_cobranca:
    st.error(mensagem_cobranca, icon="🔔")
    st.session_state.memoria_v3.append({"role": "assistant", "content": "🔔 " + mensagem_cobranca})
    arquivo_bronca = asyncio.run(falar(mensagem_cobranca))
    st.audio(arquivo_bronca, format="audio/mp3", autoplay=True)

# --- SIDEBAR ---
with col_agenda:
    st.subheader("📌 Agenda")
    if tarefas:
        for i, t in enumerate(tarefas):
            atrasada = agora > datetime.strptime(t['data_hora'], "%Y-%m-%d %H:%M").replace(tzinfo=FUSO_BR)
            icone = "🔥" if atrasada else "📅"
            st.warning(f"{icone} {t['data_hora'].split(' ')[1]}\n{t['descricao']}")
            if st.button("Feito", key=f"d{i}"):
                tarefas.pop(i)
                salvar_tarefas(tarefas)
                st.rerun()
    else: st.success("Livre!")

# --- CHAT ---
with col_main:
    container = st.container()
    with container:
        for m in st.session_state.memoria_v3:
            with st.chat_message(m["role"]): st.markdown(m["content"])

    st.divider()
    c1, c2 = st.columns([0.15, 0.85])
    texto = None
    usou_voz = False
    
    with c2: 
        if t := st.chat_input("Mensagem..."): texto = t
    with c1:
        if a := st.audio_input("🎙️"):
            if a != st.session_state.ultimo_audio:
                st.session_state.ultimo_audio = a
                with st.spinner("."): 
                    texto = ouvir_audio(a)
                    usou_voz = True

    if texto:
        st.session_state.memoria_v3.append({"role": "user", "content": texto})
        with container.chat_message("user"): st.markdown(texto)
        
        with container.chat_message("assistant"):
            intencao = identificar_intencao(texto)
            
            # --- ROTA: CONCLUIR (NOVA!) ---
            if intencao == "CONCLUIR":
                if not tarefas:
                    st.info("Sua agenda já está vazia!")
                else:
                    # Usa a IA para descobrir qual tarefa apagar
                    indice = encontrar_tarefa_para_remover(texto, tarefas)
                    if indice != -1:
                        removida = tarefas.pop(indice)
                        salvar_tarefas(tarefas)
                        
                        msg_confirmacao = f"Maravilha! Marquei como feito: '{removida['descricao']}'."
                        st.success(msg_confirmacao)
                        st.session_state.memoria_v3.append({"role": "assistant", "content": "✅ " + msg_confirmacao})
                        
                        if usou_voz:
                            mp3 = asyncio.run(falar("Maravilha! Tarefa concluída."))
                            st.audio(mp3, format="audio/mp3", autoplay=True)
                        
                        time.sleep(2) # Pausa dramática antes de limpar
                        st.rerun()
                    else:
                        st.warning("Não encontrei essa tarefa na sua lista.")

            # --- ROTA: AGENDAR ---
            elif intencao == "AGENDAR":
                d = extrair_dados_tarefa(texto)
                if d:
                    tarefas.append(d)
                    salvar_tarefas(tarefas)
                    st.success(f"Agendado: **{d['descricao']}**")
                    st.rerun()
            
            # --- ROTA: BUSCAR ---
            elif intencao == "BUSCAR":
                if web := buscar_tavily(texto):
                    resp = client.chat.completions.create(model=MODEL_ID, messages=[{"role":"user","content":f"Dados: {web}. Pergunta: {texto}"}]).choices[0].message.content
                    st.markdown(resp)
                    st.session_state.memoria_v3.append({"role": "assistant", "content": resp})
            
            # --- ROTA: CONVERSA ---
            else:
                msgs = [{"role":"system","content":"Assistente útil."}] + [{"role":m["role"],"content":str(m["content"])} for m in st.session_state.memoria_v3]
                resp = client.chat.completions.create(model=MODEL_ID, messages=msgs).choices[0].message.content
                st.markdown(resp)
                st.session_state.memoria_v3.append({"role": "assistant", "content": resp})
                if usou_voz:
                    mp3 = asyncio.run(falar(resp))
                    st.audio(mp3, format="audio/mp3", autoplay=True)

# Loop de Vigília
time.sleep(10)
st.rerun()

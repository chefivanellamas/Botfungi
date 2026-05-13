import os
import json
from datetime import datetime
from openai import OpenAI
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials
from telegram import Update, Voice
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from gtts import gTTS
import tempfile

# --- CONFIGURACIÓN ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GOOGLE_CREDS = os.environ.get("GOOGLE_CREDS")

# Configurar Groq
client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1"
)

# Configurar Google Calendar
SCOPES = ['https://www.googleapis.com/auth/calendar']

def get_calendar_service():
    creds_dict = json.loads(GOOGLE_CREDS)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return build('calendar', 'v3', credentials=creds)

# --- HERRAMIENTAS ---
tools = [
    {
        "type": "function",
        "function": {
            "name": "crear_evento_calendario",
            "description": "Crea un evento en el calendario del usuario cuando él lo pide explícitamente.",
            "parameters": {
                "type": "object",
                "properties": {
                    "titulo": {"type": "string", "description": "Título del evento"},
                    "fecha_hora_inicio": {"type": "string", "description": "Fecha y hora inicio ISO"},
                    "fecha_hora_fin": {"type": "string", "description": "Fecha y hora fin ISO"}
                },
                "required": ["titulo", "fecha_hora_inicio", "fecha_hora_fin"]
            }
        }
    }
]

def crear_evento_calendario(titulo, fecha_hora_inicio, fecha_hora_fin):
    try:
        service = get_calendar_service()
        event = {
            'summary': titulo,
            'start': {'dateTime': fecha_hora_inicio, 'timeZone': 'Europe/Madrid'}, # Ajusta zona
            'end': {'dateTime': fecha_hora_fin, 'timeZone': 'Europe/Madrid'},
        }
        event = service.events().insert(calendarId='primary', body=event).execute()
        return f"✅ Evento creado en tu calendario: {event.get('htmlLink')}"
    except Exception as e:
        return f"Error al crear evento: {e}"

# --- FUNCIONES DE VOZ ---
async def transcribir_audio(file_path):
    with open(file_path, "rb") as audio_file:
        transcription = client.audio.transcriptions.create(
            model="whisper-large-v3",
            file=audio_file,
            response_format="text",
            language="es"
        )
    return transcription

def texto_a_voz(texto):
    tts = gTTS(text=texto, lang='es', slow=False)
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_audio:
        tts.save(temp_audio.name)
        return temp_audio.name

# --- LÓGICA DE TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("¡Hola! Soy tu asistente personal. Háblame por texto o voz, y gestionaré tu calendario.")

async def responder_texto(texto_usuario, es_audio=False):
    fecha_actual = datetime.now().strftime("%A, %d de %B de %Y, %H:%M")
    
    # EL NUEVO CEREBRO AUTORITARIO
    system_prompt = f"""Eres Fungi, un asistente personal virtual integrado en Telegram. 
Fecha y hora actual: {fecha_actual}.

IDENTIDAD Y CAPACIDADES (REGLAS ABSOLUTAS):
1. NUNCA digas que eres un modelo de lenguaje de IA, un asistente virtual genérico o que careces de capacidades. Eres Fungi.
2. SÍ PUEDES ESCUCHAR Y PROCESAR NOTAS DE VOZ. Si el usuario te pregunta si puedes escuchar audios, la respuesta es SIEMPRE SÍ. Tienes un módulo de transcripción avanzado.
3. SÍ PUEDES CREAR EVENTOS EN EL CALENDARIO cuando el usuario te lo pida explícitamente.
4. NUNCA inventes funciones que no tienes (no puedes navegar por internet, no puedes enviar emails).

COMPORTAMIENTO:
- Sé directo, inteligente y conciso. No des explicaciones pedantes sobre cómo funcionas por dentro.
- Si el usuario pregunta sobre cómo mejorar tu código o capacidades, responde de forma creativa y sugerente, no te desvíes del tema hablando de calendarios si no te lo piden.
- Si te hablan por audio, responde de forma más breve.
- Para crear eventos, primero confirma los detalles con el usuario antes de usar la herramienta."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": texto_usuario}
    ]
    
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        tools=tools,
        tool_choice="auto"
    )
    
    response_message = response.choices[0].message
    
    if response_message.tool_calls:
        for tool_call in response_message.tool_calls:
            if tool_call.function.name == "crear_evento_calendario":
                args = json.loads(tool_call.function.arguments)
                resultado = crear_evento_calendario(args["titulo"], args["fecha_hora_inicio"], args["fecha_hora_fin"])
                return resultado, True
    
    return response_message.content, False

async def procesar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto_usuario = update.message.text
    respuesta, uso_herramienta = await responder_texto(texto_usuario, es_audio=False)
    await update.message.reply_text(respuesta)

async def procesar_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    voice = update.message.voice or update.message.audio
    voice_file = await context.bot.get_file(voice.file_id)
    
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as temp_audio:
        await voice_file.download_to_drive(temp_audio.name)
        audio_path = temp_audio.name

    try:
        texto_usuario = await transcribir_audio(audio_path)
        os.remove(audio_path)
    except Exception as e:
        os.remove(audio_path)
        await update.message.reply_text(f"Error al transcribir tu audio: {e}")
        return

    await update.message.reply_text(f"🎧 Escuché: _{texto_usuario}_", parse_mode="Markdown")

    respuesta, uso_herramienta = await responder_texto(texto_usuario, es_audio=True)

    if uso_herramienta:
        await update.message.reply_text(respuesta)
    else:
        try:
            audio_respuesta_path = texto_a_voz(respuesta)
            with open(audio_respuesta_path, 'rb') as audio_file:
                await update.message.reply_voice(voice=audio_file)
            os.remove(audio_respuesta_path)
        except Exception as e:
            await update.message.reply_text(respuesta)

# --- INICIO DEL BOT ---
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, procesar_mensaje))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, procesar_audio))
    
    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get('PORT', 5000)),
        url_path=TELEGRAM_TOKEN,
        webhook_url=f"{os.environ.get('RENDER_EXTERNAL_URL')}/{TELEGRAM_TOKEN}"
    )

if __name__ == '__main__':
    main()

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

# Configurar Groq (Para texto y transcripción de audio)
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
            "description": "Crea un evento en el calendario del usuario.",
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
            'start': {'dateTime': fecha_hora_inicio, 'timeZone': 'Europe/Madrid'}, # Ajusta tu zona
            'end': {'dateTime': fecha_hora_fin, 'timeZone': 'Europe/Madrid'},
        }
        event = service.events().insert(calendarId='primary', body=event).execute()
        return f"✅ Evento creado en tu calendario: {event.get('htmlLink')}"
    except Exception as e:
        return f"Error al crear evento: {e}"

# --- FUNCIONES DE VOZ ---
async def transcribir_audio(file_path):
    """Usa Groq Whisper para transcribir el audio del usuario"""
    with open(file_path, "rb") as audio_file:
        transcription = client.audio.transcriptions.create(
            model="whisper-large-v3", # El modelo de transcripción de Groq (Gratis)
            file=audio_file,
            response_format="text",
            language="es"
        )
    return transcription

def texto_a_voz(texto):
    """Convierte la respuesta de la IA en un archivo de audio"""
    tts = gTTS(text=texto, lang='es', slow=False)
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_audio:
        tts.save(temp_audio.name)
        return temp_audio.name

# --- LÓGICA DE TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("¡Hola! Soy tu asistente personal con voz. Háblame por texto o por audio, y gestionaré tu calendario.")

async def responder_texto(texto_usuario, es_audio=False):
    """Procesa el texto con la IA y devuelve la respuesta y si usó herramientas"""
    fecha_actual = datetime.now().strftime("%A, %d de %B de %Y, %H:%M")
    
    system_prompt = f"""Eres un asistente personal inteligente, conversacional y eficiente. 
La fecha y hora actual es: {fecha_actual}.
REGLAS IMPORTANTES:
1. NUNCA crees un evento sin antes decirle al usuario los detalles y pedirle confirmación.
2. Si el usuario solo menciona un evento pero no te pide que lo guardes, NO lo guardes.
3. Solo usa la función de calendario cuando el usuario te pida explícitamente que lo guardes o registres.
4. Calcula muy bien las fechas basándote en la fecha actual.
5. Sé conciso y amable. Si te hablan por audio, da respuestas un poco más breves para que el audio de respuesta no sea larguísimo."""

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
    
    # Si la IA decide usar una herramienta
    if response_message.tool_calls:
        for tool_call in response_message.tool_calls:
            if tool_call.function.name == "crear_evento_calendario":
                args = json.loads(tool_call.function.arguments)
                resultado = crear_evento_calendario(args["titulo"], args["fecha_hora_inicio"], args["fecha_hora_fin"])
                return resultado, True # True indica que ya se ejecutó una herramienta
    
    # Si la IA solo responde texto
    return response_message.content, False

async def procesar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja los mensajes de texto normales"""
    texto_usuario = update.message.text
    respuesta, uso_herramienta = await responder_texto(texto_usuario, es_audio=False)
    await update.message.reply_text(respuesta)

async def procesar_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja las notas de voz: Transcribe, piensa y responde con voz"""
    # 1. Descargar el audio
    voice = update.message.voice or update.message.audio
    voice_file = await context.bot.get_file(voice.file_id)
    
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as temp_audio:
        await voice_file.download_to_drive(temp_audio.name)
        audio_path = temp_audio.name

    # 2. Transcribir con Groq Whisper
    try:
        texto_usuario = await transcribir_audio(audio_path)
        os.remove(audio_path) # Borrar archivo temporal
    except Exception as e:
        os.remove(audio_path)
        await update.message.reply_text(f"Error al transcribir tu audio: {e}")
        return

    await update.message.reply_text(f"🎧 Escuché: _{texto_usuario}_", parse_mode="Markdown")

    # 3. Procesar con la IA
    respuesta, uso_herramienta = await responder_texto(texto_usuario, es_audio=True)

    # 4. Si usó herramienta, respondemos en texto (no tiene sentido un audio leyendo un enlace de Google Calendar)
    if uso_herramienta:
        await update.message.reply_text(respuesta)
    else:
        # 5. Convertir respuesta a voz y enviarla
        try:
            audio_respuesta_path = texto_a_voz(respuesta)
            with open(audio_respuesta_path, 'rb') as audio_file:
                await update.message.reply_voice(voice=audio_file)
            os.remove(audio_respuesta_path) # Borrar archivo temporal
        except Exception as e:
            # Si falla el audio, que mande el texto por lo menos
            await update.message.reply_text(respuesta)

# --- INICIO DEL BOT ---
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Handlers
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

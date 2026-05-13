import os
import json
from datetime import datetime
from openai import OpenAI
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import edge_tts
from duckduckgo_search import DDGS
import tempfile
import asyncio

# --- CONFIGURACIÓN ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GOOGLE_CREDS = os.environ.get("GOOGLE_CREDS")

client = OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")

# Google Calendar
SCOPES = ['https://www.googleapis.com/auth/calendar']
def get_calendar_service():
    creds_dict = json.loads(GOOGLE_CREDS)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return build('calendar', 'v3', credentials=creds)

# --- HERRAMIENTAS (Ahora sabe buscar en internet) ---
tools = [
    {
        "type": "function",
        "function": {
            "name": "crear_evento_calendario",
            "description": "Crea un evento en el calendario. REGLA: Solo úsala DESPUÉS de que el usuario confirme explícitamente.",
            "parameters": {
                "type": "object",
                "properties": {
                    "titulo": {"type": "string", "description": "Título del evento"},
                    "fecha_hora_inicio": {"type": "string", "description": "Fecha y hora inicio ISO (ej. 2024-05-16T17:00:00)"},
                    "fecha_hora_fin": {"type": "string", "description": "Fecha y hora fin ISO"}
                },
                "required": ["titulo", "fecha_hora_inicio", "fecha_hora_fin"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_en_internet",
            "description": "Busca información actual en internet cuando no sabes algo o el usuario te lo pide.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Término de búsqueda exacto"}
                },
                "required": ["query"]
            }
        }
    }
]

def crear_evento_calendario(titulo, fecha_hora_inicio, fecha_hora_fin):
    try:
        service = get_calendar_service()
        # ¡IMPORTANTE! Asegúrate de que tu zona horaria sea la correcta aquí abajo:
        zona = 'America/Bogota' # Cambia a 'Europe/Madrid', 'America/Mexico_City', etc.
        event = {
            'summary': titulo,
            'start': {'dateTime': fecha_hora_inicio, 'timeZone': zona},
            'end': {'dateTime': fecha_hora_fin, 'timeZone': zona},
        }
        event = service.events().insert(calendarId='primary', body=event).execute()
        return f"✅ Evento creado correctamente en tu calendario."
    except Exception as e:
        return f"Error al crear evento. Dile al usuario que la fecha puede estar mal formatuada: {e}"

def buscar_en_internet(query):
    try:
        with DDGS() as ddgs:
            results = [r["body"] for r in ddgs.text(query, max_results=3)]
            if results:
                return "Resultados encontrados:\n" + "\n".join(results)
            return "No encontré nada en internet sobre eso."
    except Exception as e:
        return f"Error buscando en internet: {e}"

# --- VOZ HUMANA (Edge-TTS) ---
async def texto_a_voz(texto):
    # Puedes cambiar la voz: es-ES-AlvaroNeural (España), es-MX-JorgeNeural (México)
    communicate = edge_tts.Communicate(texto, "es-ES-AlvaroNeural") 
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_audio:
        await communicate.save(temp_audio.name)
        return temp_audio.name

async def transcribir_audio(file_path):
    with open(file_path, "rb") as audio_file:
        transcription = client.audio.transcriptions.create(
            model="whisper-large-v3",
            file=audio_file,
            response_format="text",
            language="es"
        )
    return transcription

# --- LÓGICA DE TELEGRAM ---
async def responder_texto(texto_usuario, es_audio=False):
    fecha_actual = datetime.now().strftime("%A, %d de %B de %Y, %H:%M")
    
    system_prompt = f"""Eres Fungi, un asistente personal avanzado integrado en Telegram. 
Fecha y hora actual: {fecha_actual}.

CAPACIDADES REALES (Lo que SÍ puedes hacer):
1. Escuchar y procesar notas de voz.
2. Buscar información en internet en tiempo real usando la herramienta de búsqueda.
3. Crear eventos en el calendario.

REGLAS ESTRICTAS DE CALENDARIO (MUY IMPORTANTE):
- NUNCA uses la herramienta crear_evento_calendario inmediatamente.
- Si el usuario pide un evento, DILE los detalles que entendiste (Título, Fecha y hora) y pregúntale: "¿Confirmo la creación de este evento?".
- SOLO usa la herramienta crear_evento_calendario si el usuario responde "sí", "confirmo", "dale", etc.
- Asegúrate siempre de incluir el año correcto en la fecha ISO (ej. 2024 o 2025).

COMPORTAMIENTO:
- Sé directo, inteligente, útil y con buena onda. No actúes como una IA genérica.
- Si no sabes algo, usa la herramienta de búsqueda de internet."""

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
            elif tool_call.function.name == "buscar_en_internet":
                args = json.loads(tool_call.function.arguments)
                resultado_busqueda = buscar_en_internet(args["query"])
                # Le pasamos el resultado a la IA para que lo resuma bonito
                messages.append(response_message)
                messages.append({"role": "tool", "content": resultado_busqueda, "tool_call_id": tool_call.id})
                
                response_2 = client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=messages
                )
                return response_2.choices[0].message.content, False
    
    return response_message.content, False

async def procesar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    respuesta, _ = await responder_texto(update.message.text, es_audio=False)
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
        await update.message.reply_text(f"Error transcribiendo: {e}")
        return

    await update.message.reply_text(f"🎧 Te escuché: _{texto_usuario}_", parse_mode="Markdown")

    respuesta, uso_herramienta = await responder_texto(texto_usuario, es_audio=True)

    if uso_herramienta:
        await update.message.reply_text(respuesta)
    else:
        try:
            audio_respuesta_path = await texto_a_voz(respuesta)
            with open(audio_respuesta_path, 'rb') as audio_file:
                await update.message.reply_voice(voice=audio_file)
            os.remove(audio_respuesta_path)
        except Exception as e:
            await update.message.reply_text(respuesta)

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", procesar_mensaje))
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

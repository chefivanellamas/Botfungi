import os
import json
import google.generativeai as genai
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- CONFIGURACIÓN SEGURA CON VARIABLES DE ENTORNO ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_CREDS = os.environ.get("GOOGLE_CREDS")

# Configurar Gemini
genai.configure(api_key=GEMINI_API_KEY)

# Configurar Google Calendar
SCOPES = ['https://www.googleapis.com/auth/calendar']

def get_calendar_service():
    creds_dict = json.loads(GOOGLE_CREDS)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return build('calendar', 'v3', credentials=creds)

# --- HERRAMIENTAS DE LA IA (Function Calling) ---
cal_tool = {
    "function_declarations": [
        {
            "name": "crear_evento_calendario",
            "description": "Crea un evento en el calendario del usuario.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "titulo": {"type": "STRING", "description": "Título del evento"},
                    "fecha_hora_inicio": {"type": "STRING", "description": "Fecha y hora inicio ISO (ej. 2024-12-31T10:00:00)"},
                    "fecha_hora_fin": {"type": "STRING", "description": "Fecha y hora fin ISO"}
                },
                "required": ["titulo", "fecha_hora_inicio", "fecha_hora_fin"]
            }
        }
    ]
}

def crear_evento_calendario(titulo, fecha_hora_inicio, fecha_hora_fin):
    try:
        service = get_calendar_service()
        event = {
            'summary': titulo,
            'start': {'dateTime': fecha_hora_inicio, 'timeZone': 'America/Madrid'}, # Cambia tu zona horaria si no eres de España
            'end': {'dateTime': fecha_hora_fin, 'timeZone': 'America/Madrid'},
        }
        event = service.events().insert(calendarId='primary', body=event).execute()
        return f"Evento creado: {event.get('htmlLink')}"
    except Exception as e:
        return f"Error al crear evento: {e}"

# Configurar el modelo (CAMBIO A gemini-pro)
model = genai.GenerativeModel(
    model_name='gemini-1.5-flash-latest',
    tools=[cal_tool],
    system_instruction="Eres un asistente personal experto y organizado. Ayudas al usuario a ordenar sus ideas y gestionar su calendario. Hablas en español de forma amable y concisa."
)

# --- LÓGICA DE TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("¡Hola! Soy tu asistente personal. Puedo chatear contigo o agregar eventos a tu calendario. ¿En qué te ayudo?")

async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mensaje_usuario = update.message.text
    try:
        # Iniciamos un chat nuevo por cada mensaje para evitar errores de estado
        chat = model.start_chat(enable_automatic_function_calling=True)
        respuesta = chat.send_message(mensaje_usuario)
        await update.message.reply_text(respuesta.text)
    except Exception as e:
        await update.message.reply_text(f"Ocurrió un error procesando tu mensaje: {e}")

# --- INICIO DEL BOT ---
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, responder))
    
    # Configurar Webhook para Render
    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get('PORT', 5000)),
        url_path=TELEGRAM_TOKEN,
        webhook_url=f"{os.environ.get('RENDER_EXTERNAL_URL')}/{TELEGRAM_TOKEN}"
    )

if __name__ == '__main__':
    main()

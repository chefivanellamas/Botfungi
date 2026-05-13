import os
import json
from datetime import datetime
from openai import OpenAI
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

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
            "description": "Crea un evento en el calendario del usuario.",
            "parameters": {
                "type": "object",
                "properties": {
                    "titulo": {"type": "string", "description": "Título del evento"},
                    "fecha_hora_inicio": {"type": "string", "description": "Fecha y hora inicio ISO (ej. 2026-05-16T12:00:00)"},
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
            'start': {'dateTime': fecha_hora_inicio, 'timeZone': 'Europe/Madrid'}, # Cambia si estás en Latinoamérica
            'end': {'dateTime': fecha_hora_fin, 'timeZone': 'Europe/Madrid'},
        }
        event = service.events().insert(calendarId='primary', body=event).execute()
        return f"✅ Evento creado exitosamente en tu calendario: {event.get('htmlLink')}"
    except Exception as e:
        return f"Error al crear evento: {e}"

# --- LÓGICA DE TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("¡Hola! Soy tu asistente personal. Puedo chatear o agregar eventos a tu calendario. ¿En qué te ayudo?")

async def responder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mensaje_usuario = update.message.text
    try:
        # LE DAMOS LA FECHA DE HOY PARA QUE NO SE CONFUNDA
        fecha_actual = datetime.now().strftime("%A, %d de %B de %Y, %H:%M")
        
        messages = [
            {"role": "system", "content": f"""Eres un asistente personal inteligente y conversacional. 
La fecha y hora actual es: {fecha_actual}.
REGLAS IMPORTANTES:
1. NUNCA crees un evento sin antes decirle al usuario los detalles y pedirle confirmación.
2. Si el usuario solo menciona un evento pero no te pide que lo guardes, NO lo guardes. Solo conversa.
3. Solo usa la función de calendario cuando el usuario te pida explícitamente que guardes, agregues o registres el evento.
4. Calcula muy bien las fechas basándote en la fecha actual que te di.
5. Responde siempre en español de forma amable y con inteligencia."""},
            {"role": "user", "content": mensaje_usuario}
        ]
        
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            tools=tools,
            tool_choice="auto"
        )
        
        response_message = response.choices[0].message
        
        # Si la IA decide crear un evento (después de haberlo confirmado en mensajes previos)
        if response_message.tool_calls:
            for tool_call in response_message.tool_calls:
                if tool_call.function.name == "crear_evento_calendario":
                    args = json.loads(tool_call.function.arguments)
                    resultado = crear_evento_calendario(args["titulo"], args["fecha_hora_inicio"], args["fecha_hora_fin"])
                    await update.message.reply_text(resultado)
                    return
        
        # Si la IA solo quiere hablar
        await update.message.reply_text(response_message.content)
        
    except Exception as e:
        await update.message.reply_text(f"Ocurrió un error: {e}")

# --- INICIO DEL BOT ---
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, responder))
    
    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get('PORT', 5000)),
        url_path=TELEGRAM_TOKEN,
        webhook_url=f"{os.environ.get('RENDER_EXTERNAL_URL')}/{TELEGRAM_TOKEN}"
    )

if __name__ == '__main__':
    main()

import asyncio
from html import escape
import logging
import os
import requests
import time
from datetime import datetime
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# ============================================================
# CONFIGURACIÓN
# ============================================================

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
logging.basicConfig(
    filename="bot.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

# Usuarios autorizados
AUTHORIZED_USERS = {
    int(user_id.strip())
    for user_id in os.getenv("AUTHORIZED_USERS", "5946808601").split(",")
    if user_id.strip().isdigit()
}
CONSULTATION_COOLDOWN = 30
last_consultations = {}

# Endpoint RUI
API_URL = "https://ventanillasocial.dnp.gov.co/Home/ObtenerDatosRUI"
SISBEN_API_URL = "https://ventanillasocial.dnp.gov.co/Home/ConsultarGrupoSisben"


# ============================================================
# AUTORIZACIÓN
# ============================================================

def usuario_autorizado(user_id: int) -> bool:
    return user_id in AUTHORIZED_USERS


def valor_seguro(datos, clave, defecto="No disponible"):
    try:
        if not isinstance(datos, dict):
            return escape(str(defecto))

        valor = datos.get(clave, defecto)
        if valor in (None, ""):
            valor = defecto

        return escape(str(valor))
    except Exception:
        return escape(str(defecto))


# ============================================================
# /start
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    if not usuario_autorizado(user.id):
        await update.message.reply_text(
            "⛔ No tienes autorización para utilizar este bot."
        )
        return

    await update.message.reply_text(
        "🤖 <b>Consulta RUI</b>\n\n"
        "Escribe:\n\n"
        "<code>/consulta DOCUMENTO</code>\n\n"
        "Usa /ayuda para ver todos los comandos.",
        parse_mode="HTML",
    )


async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 <b>Comandos disponibles</b>\n\n"
        "<code>/consulta NUMERO</code> - Consulta RUI y Sisbén IV.\n"
        "<code>/estado</code> - Comprueba el estado del servicio.\n"
        "<code>/id</code> - Muestra tu ID de Telegram.\n"
        "<code>/ayuda</code> - Muestra esta ayuda.",
        parse_mode="HTML",
    )


async def estado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        response = await asyncio.to_thread(
            requests.get,
            "https://ventanillasocial.dnp.gov.co/Home/Index",
            timeout=10,
        )
        servicio = "✅ disponible" if response.ok else f"⚠️ HTTP {response.status_code}"
    except requests.RequestException:
        servicio = "❌ no disponible"

    await update.message.reply_text(
        f"🩺 <b>Estado del servicio</b>\n\nVentanilla Social: {servicio}",
        parse_mode="HTML",
    )


# ============================================================
# /id
# ============================================================

async def mi_id(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    await update.message.reply_text(
        f"🆔 Tu Telegram ID es:\n\n"
        f"<code>{user.id}</code>",
        parse_mode="HTML",
    )


# ============================================================
# /consulta
# ============================================================

async def consulta(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    # --------------------------------------------------------
    # COMPROBAR AUTORIZACIÓN
    # --------------------------------------------------------

    if not usuario_autorizado(user.id):
        await update.message.reply_text(
            "⛔ No tienes autorización para realizar consultas."
        )
        return

    ahora = time.monotonic()
    ultimo_intento = last_consultations.get(user.id, 0)
    if ahora - ultimo_intento < CONSULTATION_COOLDOWN:
        restante = int(CONSULTATION_COOLDOWN - (ahora - ultimo_intento)) + 1
        await update.message.reply_text(
            f"⏳ Espera {restante} segundos antes de realizar otra consulta."
        )
        return
    last_consultations[user.id] = ahora

    # --------------------------------------------------------
    # COMPROBAR DOCUMENTO
    # --------------------------------------------------------

    if not context.args:
        await update.message.reply_text(
            "❌ Debes escribir un número de documento.\n\n"
            "<code>/consulta NUMERO</code>",
            parse_mode="HTML",
        )
        return

    documento = context.args[0].strip()

    # --------------------------------------------------------
    # VALIDAR DOCUMENTO
    # --------------------------------------------------------

    if not documento.isdigit():
        await update.message.reply_text(
            "❌ El documento debe contener solamente números."
        )
        return

    if len(documento) < 5 or len(documento) > 20:
        await update.message.reply_text(
            "❌ El número de documento no tiene un formato válido."
        )
        return

    mensaje = await update.message.reply_text(
        "🔎 Consultando RUI..."
    )

    # --------------------------------------------------------
    # DATOS DEL POST
    # --------------------------------------------------------

    payload = {
        "pNumDoc": documento,
        "pTipDoc": "3",
    }

    try:

        response, sisben_response = await asyncio.gather(
            asyncio.to_thread(
                requests.post,
                API_URL,
                data=payload,
                timeout=15,
            ),
            asyncio.to_thread(
                requests.post,
                SISBEN_API_URL,
                data=payload,
                timeout=15,
            ),
        )

        response.raise_for_status()
        sisben_response.raise_for_status()

        datos = response.json()
        datos_sisben = sisben_response.json()

    except requests.exceptions.Timeout:
        logging.warning("Tiempo de espera agotado para usuario %s", user.id)

        await mensaje.edit_text(
            "⏱️ La consulta tardó demasiado.\n"
            "Intenta nuevamente."
        )
        return

    except requests.exceptions.RequestException as error:

        logging.exception("Error de conexión para usuario %s: %s", user.id, error)

        await mensaje.edit_text(
            "❌ No fue posible comunicarse con el servicio RUI."
        )
        return

    except ValueError:

        logging.exception("Respuesta JSON inválida para usuario %s", user.id)

        await mensaje.edit_text(
            "❌ El servicio devolvió una respuesta "
            "que no se pudo interpretar."
        )
        return

    except Exception as error:
        logging.exception("Error inesperado en la consulta de usuario %s: %s", user.id, error)
        await mensaje.edit_text(
            "⚠️ La consulta falló, pero el bot continuó sin romper el flujo."
        )
        return

    if not isinstance(datos, dict):
        datos = {}
    if not isinstance(datos_sisben, dict):
        datos_sisben = {}

    # --------------------------------------------------------
    # COMPROBAR RESULTADO
    # --------------------------------------------------------

    if not datos.get("ok") and not datos_sisben.get("ok"):

        await mensaje.edit_text(
            "ℹ️ No se encontraron datos para esta consulta."
        )
        return

    # --------------------------------------------------------
    # EXTRAER DATOS
    # --------------------------------------------------------

    nombre = valor_seguro(datos, "nombre")
    sexo = valor_seguro(datos, "sexo")
    edad = valor_seguro(datos, "edad")
    municipio = valor_seguro(datos, "municipio")
    departamento = valor_seguro(datos, "departamento")
    cod_mun = valor_seguro(datos, "codMun")
    nivel_rui = valor_seguro(datos, "nivelRui")
    grupo_rui = valor_seguro(datos, "grupoRui")
    grupo_ingresos = valor_seguro(datos, "grupoIngresos")
    direccion = valor_seguro(datos, "direccion", "Sin dirección registrada")
    grupo_sisben = valor_seguro(datos_sisben, "grupo")
    descripcion_sisben = valor_seguro(
        datos_sisben,
        "descripcion",
        "Sin clasificación registrada",
    )

    if datos_sisben.get("ok"):
        sisben_texto = (
            f"📌 Grupo: {grupo_sisben}\n"
            f"📝 Clasificación: {descripcion_sisben}"
        )
    else:
        sisben_texto = (
            "ℹ️ No se encontró clasificación Sisbén IV para este documento."
        )

    # --------------------------------------------------------
    # CREAR RESPUESTA
    # --------------------------------------------------------

    texto = (
        "📋 <b>CONSULTA RUI</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"

        f"👤 <b>Nombre:</b>\n"
        f"{nombre}\n\n"

        f"🪪 <b>Documento:</b> {documento}\n"
        f"⚧ <b>Sexo:</b> {sexo}\n"
        f"🎂 <b>Edad:</b> {edad}\n\n"

        "📍 <b>UBICACIÓN</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🏙️ Municipio: {municipio}\n"
        f"🗺️ Departamento: {departamento}\n"
        f"� Dirección: {direccion}\n"
        f"�🔢 Código municipio: {cod_mun}\n\n"

        "📊 <b>CLASIFICACIÓN RUI</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 Nivel RUI: {nivel_rui}\n"
        f"👥 Grupo RUI: {grupo_rui}\n"
        f"💰 Grupo ingresos: {grupo_ingresos}\n\n"

        "🧾 <b>SISBÉN IV</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{sisben_texto}"
    )

    # --------------------------------------------------------
    # ENVIAR RESULTADO
    # --------------------------------------------------------

    await mensaje.edit_text(
        texto,
        parse_mode="HTML",
    )

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.exception("Error no controlado en Telegram", exc_info=context.error)


# ============================================================
# MAIN
# ============================================================

def main():

    if not BOT_TOKEN or BOT_TOKEN == "TU_TOKEN_DE_BOTFATHER":
        raise RuntimeError(
            "❌ TELEGRAM_BOT_TOKEN no está configurado.\n\n"
            "Abre el archivo .env y reemplaza el valor de ejemplo "
            "por el token real entregado por @BotFather."
        )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Comandos
    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("ayuda", ayuda)
    )

    application.add_handler(
        CommandHandler("estado", estado)
    )

    application.add_handler(
        CommandHandler("id", mi_id)
    )

    application.add_handler(
        CommandHandler("consulta", consulta)
    )

    application.add_error_handler(error_handler)

    print("======================================")
    print("🤖 BOT CONSULTA RUI")
    print("======================================")
    print("Bot iniciado correctamente.")
    print("Usuarios autorizados:", ", ".join(map(str, AUTHORIZED_USERS)))
    print("Presiona Ctrl+C para detenerlo.")
    print("======================================")

    application.run_polling()


# ============================================================
# EJECUTAR
# ============================================================

if __name__ == "__main__":
    main()
import csv
import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


TELEGRAM_API_BASE = "https://api.telegram.org"
BLUELYTICS_LATEST_URL = "https://api.bluelytics.com.ar/v2/latest"
BLUELYTICS_EVOLUTION_URL = "https://api.bluelytics.com.ar/v2/evolution.json"
DOUBLE_PROMPT = "Enviame un numero y te devuelvo el doble."
DEFAULT_BOT_PHOTO_URL = "https://robohash.org/loro-tdi2-bot.png?size=512x512&set=set1"
DEFAULT_CHAT_HISTORY_CSV = "/tmp/chat_history.csv"
HISTORY_FIELDS = [
    "timestamp",
    "direction",
    "user_id",
    "username",
    "first_name",
    "chat_id",
    "text",
]


def send_json(handler, status_code, payload):
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def telegram_request(bot_token, method, payload):
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/{method}"
    data = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        print(f"Telegram {method} failed: {error.code} {error_body}")
    except (URLError, TimeoutError) as error:
        print(f"Telegram {method} network error: {error}")

    return None


def telegram_multipart_request(bot_token, method, fields, files):
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/{method}"
    boundary = f"----tdi2bot{uuid.uuid4().hex}"
    body_parts = []

    for name, value in fields.items():
        body_parts.extend([
            f"--{boundary}\r\n".encode("utf-8"),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
            str(value).encode("utf-8"),
            b"\r\n",
        ])

    for name, file_info in files.items():
        filename, content_type, content = file_info
        body_parts.extend([
            f"--{boundary}\r\n".encode("utf-8"),
            (
                f'Content-Disposition: form-data; name="{name}"; '
                f'filename="{filename}"\r\n'
            ).encode("utf-8"),
            f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
            content,
            b"\r\n",
        ])

    body_parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(body_parts)

    request = Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        print(f"Telegram {method} multipart failed: {error.code} {error_body}")
    except (URLError, TimeoutError) as error:
        print(f"Telegram {method} multipart network error: {error}")

    return None


def get_history_path():
    return Path(os.environ.get("CHAT_HISTORY_CSV", DEFAULT_CHAT_HISTORY_CSV))


def user_info_from_message(message):
    sender = message.get("from") or {}
    chat = message.get("chat") or {}
    return {
        "user_id": str(sender.get("id") or ""),
        "username": sender.get("username") or "",
        "first_name": sender.get("first_name") or "",
        "chat_id": str(chat.get("id") or ""),
    }


def append_history(direction, text, user_info):
    history_path = get_history_path()
    history_path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = history_path.exists()

    with history_path.open("a", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=HISTORY_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "direction": direction,
            "user_id": user_info.get("user_id", ""),
            "username": user_info.get("username", ""),
            "first_name": user_info.get("first_name", ""),
            "chat_id": user_info.get("chat_id", ""),
            "text": text,
        })


def send_message(bot_token, chat_id, text, reply_markup=None, user_info=None):
    payload = {
        "chat_id": chat_id,
        "text": text,
    }

    if reply_markup:
        payload["reply_markup"] = reply_markup

    response = telegram_request(bot_token, "sendMessage", payload)

    if user_info:
        append_history("sent", text, user_info)

    return response


def send_photo_url(bot_token, chat_id, photo_url, caption, user_info=None):
    response = telegram_request(bot_token, "sendPhoto", {
        "chat_id": chat_id,
        "photo": photo_url,
        "caption": caption,
    })

    if user_info:
        append_history("sent", f"[foto] {caption}", user_info)

    return response


def send_photo_file(bot_token, chat_id, image_path, caption, user_info=None):
    image_content = Path(image_path).read_bytes()
    response = telegram_multipart_request(
        bot_token,
        "sendPhoto",
        fields={
            "chat_id": chat_id,
            "caption": caption,
        },
        files={
            "photo": (Path(image_path).name, "image/png", image_content),
        },
    )

    if user_info:
        append_history("sent", f"[grafico] {caption}", user_info)

    return response


def normalize_command(text):
    command = text.strip().split()[0].lower()
    return command.split("@", 1)[0]


def parse_decimal(value):
    normalized = value.strip().replace(",", ".")
    return Decimal(normalized)


def format_decimal(value):
    if value == value.to_integral():
        return str(value.to_integral())
    return format(value.normalize(), "f")


def get_sender_name(message):
    sender = message.get("from") or {}
    first_name = sender.get("first_name")
    username = sender.get("username")

    if first_name:
        return first_name
    if username:
        return f"@{username}"
    return "ahi"


def is_reply_to_double_prompt(message):
    reply = message.get("reply_to_message") or {}
    return (reply.get("text") or "").strip() == DOUBLE_PROMPT


def allowed_user_ids():
    raw_ids = os.environ.get("ALLOWED_TELEGRAM_USER_IDS", "")
    return {value.strip() for value in raw_ids.split(",") if value.strip()}


def is_allowed_user(message):
    allowed_ids = allowed_user_ids()
    user_id = str((message.get("from") or {}).get("id") or "")
    return bool(allowed_ids) and user_id in allowed_ids


def fetch_dollar_text():
    request = Request(BLUELYTICS_LATEST_URL, headers={"Accept": "application/json"})

    try:
        with urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        print(f"Bluelytics error: {error}")
        return "No pude consultar Bluelytics en este momento. Proba de nuevo en unos minutos."

    oficial = data.get("oficial") or {}
    blue = data.get("blue") or {}
    last_update = data.get("last_update", "sin fecha")

    return (
        "Cotizacion Bluelytics\n"
        f"Oficial compra: ${oficial.get('value_buy', 's/d')}\n"
        f"Oficial venta: ${oficial.get('value_sell', 's/d')}\n"
        f"Blue compra: ${blue.get('value_buy', 's/d')}\n"
        f"Blue venta: ${blue.get('value_sell', 's/d')}\n"
        f"Actualizado: {last_update}"
    )


def fetch_dollar_chart(days):
    import matplotlib
    import pandas as pd

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    request = Request(BLUELYTICS_EVOLUTION_URL, headers={"Accept": "application/json"})

    try:
        with urlopen(request, timeout=10) as response:
            raw_data = response.read().decode("utf-8")
        data = json.loads(raw_data)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        print(f"Bluelytics evolution error: {error}")
        return None, "No pude consultar la evolucion de Bluelytics en este momento."

    df = pd.DataFrame(data)

    required_columns = {"date", "source", "value_sell"}
    if df.empty or not required_columns.issubset(df.columns):
        return None, "Bluelytics respondio con un formato inesperado para la evolucion."

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["value_sell"] = pd.to_numeric(df["value_sell"], errors="coerce")
    df = df.dropna(subset=["date", "source", "value_sell"])
    df["source_normalized"] = df["source"].astype(str).str.lower()
    df = df[df["source_normalized"].str.contains("blue|oficial", regex=True)]

    if df.empty:
        return None, "No encontre datos de dolar blue/oficial para graficar."

    df = df.sort_values("date")
    max_date = df["date"].max()
    min_date = max_date - pd.Timedelta(days=days)
    df = df[df["date"] >= min_date]

    chart_path = Path("/tmp/dolar_evolucion.png")
    plt.figure(figsize=(9, 5))

    for source, group in df.groupby("source"):
        group = group.sort_values("date")
        plt.plot(group["date"], group["value_sell"], marker="o", linewidth=2, label=source)

    plt.title(f"Evolucion dolar - ultimos {days} dias")
    plt.xlabel("Fecha")
    plt.ylabel("Venta")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(chart_path, dpi=140)
    plt.close()

    return chart_path, f"Evolucion del dolar segun Bluelytics, ultimos {days} dias."


def read_user_history(message, limit=10):
    history_path = get_history_path()

    if not history_path.exists():
        return "Todavia no hay historial guardado para consultar."

    user_id = str((message.get("from") or {}).get("id") or "")
    rows = []

    with history_path.open("r", newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            if row.get("user_id") == user_id:
                rows.append(row)

    if not rows:
        return "No encontre historial para tu usuario."

    last_rows = rows[-limit:]
    lines = ["Tus ultimos chats registrados:"]

    for row in last_rows:
        timestamp = row.get("timestamp", "")
        direction = row.get("direction", "")
        text = row.get("text", "")
        lines.append(f"- {timestamp} [{direction}] {text}")

    return "\n".join(lines)


def help_text():
    return (
        "Comandos disponibles:\n"
        "/saludar - te saludo con una foto\n"
        "/doble - te pido un numero y devuelvo el doble\n"
        "/doble 21 - calculo el doble directo\n"
        "/dolar - consulto dolar oficial y blue\n"
        "/dolar_grafico - grafico la evolucion del dolar\n"
        "/historial - muestro tus ultimos 10 chats si estas autorizado\n"
        "/mi_id - muestro tu ID de Telegram\n"
        "/ping - prueba rapida\n\n"
        "Si escribis otro texto, lo repito."
    )


def handle_double(bot_token, chat_id, text, user_info):
    parts = text.strip().split(maxsplit=1)

    if len(parts) == 1:
        send_message(
            bot_token,
            chat_id,
            DOUBLE_PROMPT,
            reply_markup={
                "force_reply": True,
                "input_field_placeholder": "Ejemplo: 21",
            },
            user_info=user_info,
        )
        return

    send_double_result(bot_token, chat_id, parts[1], user_info)


def send_double_result(bot_token, chat_id, raw_number, user_info):
    try:
        number = parse_decimal(raw_number)
    except InvalidOperation:
        send_message(
            bot_token,
            chat_id,
            "Eso no parece un numero. Proba con algo como 21 o 3,5.",
            user_info=user_info,
        )
        return

    result = number * 2
    send_message(
        bot_token,
        chat_id,
        f"El doble de {format_decimal(number)} es {format_decimal(result)}.",
        user_info=user_info,
    )


def parse_days(text, default_days=30):
    parts = text.strip().split(maxsplit=1)
    if len(parts) == 1:
        return default_days

    try:
        days = int(parts[1])
    except ValueError:
        return default_days

    return max(7, min(days, 365))


def handle_message(bot_token, message):
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = message.get("text")
    user_info = user_info_from_message(message)

    if not chat_id or not text:
        return

    append_history("received", text, user_info)

    if is_reply_to_double_prompt(message):
        send_double_result(bot_token, chat_id, text, user_info)
        return

    if not text.startswith("/"):
        send_message(bot_token, chat_id, text, user_info=user_info)
        return

    command = normalize_command(text)

    if command in {"/start", "/ayuda", "/help"}:
        send_message(bot_token, chat_id, help_text(), user_info=user_info)
    elif command == "/ping":
        send_message(bot_token, chat_id, "pong", user_info=user_info)
    elif command == "/mi_id":
        send_message(
            bot_token,
            chat_id,
            f"Tu ID de Telegram es: {user_info.get('user_id')}",
            user_info=user_info,
        )
    elif command == "/saludar":
        name = get_sender_name(message)
        photo_url = os.environ.get("BOT_PHOTO_URL", DEFAULT_BOT_PHOTO_URL)
        send_photo_url(
            bot_token,
            chat_id,
            photo_url,
            f"Hola, {name}. Soy el bot loro de TDI2.",
            user_info=user_info,
        )
    elif command == "/doble":
        handle_double(bot_token, chat_id, text, user_info)
    elif command == "/dolar":
        send_message(bot_token, chat_id, fetch_dollar_text(), user_info=user_info)
    elif command == "/dolar_grafico":
        days = parse_days(text)
        chart_path, caption = fetch_dollar_chart(days)
        if chart_path:
            send_photo_file(bot_token, chat_id, chart_path, caption, user_info=user_info)
        else:
            send_message(bot_token, chat_id, caption, user_info=user_info)
    elif command == "/historial":
        if not is_allowed_user(message):
            send_message(
                bot_token,
                chat_id,
                "No estas autorizado para consultar historial. Usa /mi_id y agregalo en ALLOWED_TELEGRAM_USER_IDS.",
                user_info=user_info,
            )
            return
        send_message(bot_token, chat_id, read_user_history(message), user_info=user_info)
    else:
        send_message(
            bot_token,
            chat_id,
            "No conozco ese comando. Escribi /ayuda para ver opciones.",
            user_info=user_info,
        )


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        send_json(self, 200, {
            "ok": True,
            "service": "bot-loro-vercel-python",
        })

    def do_POST(self):
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        webhook_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET")

        if not bot_token or not webhook_secret:
            print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_WEBHOOK_SECRET")
            send_json(self, 500, {"ok": False})
            return

        received_secret = self.headers.get("X-Telegram-Bot-Api-Secret-Token")

        if received_secret != webhook_secret:
            send_json(self, 403, {"ok": False})
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length)

        try:
            update = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            send_json(self, 400, {"ok": False})
            return

        message = update.get("message")

        if message:
            handle_message(bot_token, message)

        send_json(self, 200, {"ok": True})

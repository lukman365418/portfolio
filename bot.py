import os
import datetime
import logging
import html

from flask import Flask, request, jsonify, redirect, send_from_directory, abort
import requests

# Basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables (set these in Render / production)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ADMIN_ID:
    logger.warning("TELEGRAM_BOT_TOKEN and/or TELEGRAM_ADMIN_ID not set. Requests will fail until configured.")

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"

app = Flask(__name__)


def _html_escape(value):
    if value is None:
        return ""
    return html.escape(str(value))


def _format_message(source, fields, remote_addr=None):
    """Construct an HTML-formatted message for Telegram."""
    lines = []
    lines.append(f"<b>New Submission</b>")
    lines.append(f"<b>Form:</b> {_html_escape(source)}")
    lines.append(f"<b>Time (UTC):</b> {_html_escape(datetime.datetime.utcnow().isoformat() + 'Z')}")

    # Add the fields in a consistent order
    for key, val in fields.items():
        lines.append(f"<b>{_html_escape(key)}:</b> {_html_escape(val)}")

    if remote_addr:
        lines.append(f"<b>IP:</b> {_html_escape(remote_addr)}")

    return "\n".join(lines)


def _send_telegram_message(text):
    """Send message text to the configured admin via Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ADMIN_ID:
        logger.error("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_ADMIN_ID environment variables.")
        return False, "server-misconfigured"

    url = TELEGRAM_API_URL.format(token=TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": TELEGRAM_ADMIN_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            logger.error("Telegram API returned not ok: %s", data)
            return False, data
        return True, data
    except Exception as exc:
        logger.exception("Failed to send message to Telegram: %s", exc)
        return False, str(exc)


def _extract_fields(form, expected_keys=None):
    """Extract a dictionary of fields from form-like data preserving order of expected_keys when provided."""
    out = {}
    if expected_keys:
        for k in expected_keys:
            if k in form and form.get(k) not in (None, ""):
                out[k] = form.get(k)
    # Add any remaining keys
    for k in form.keys():
        if k not in out:
            out[k] = form.get(k)
    return out


    # Removed the health function as per the patch request
@app.route("/", methods=["GET"]) 
def serve_index():
    # Serve the local `index.html` so the frontend and backend run together.
    index_path = os.path.join(os.getcwd(), "index.html")
    if os.path.exists(index_path):
        return send_from_directory(os.getcwd(), "index.html")
    return "OK\n", 200


# Serve other static files (CSS, JS, images, HTML pages)
@app.route('/<path:filename>', methods=["GET"])
def serve_file(filename):
    # Prevent accidental exposure of sensitive files
    forbidden = {'.env', 'bot.py', 'requirements.txt'}
    if os.path.basename(filename) in forbidden:
        abort(403)
    full_path = os.path.join(os.getcwd(), filename)
    if os.path.exists(full_path) and os.path.isfile(full_path):
        return send_from_directory(os.getcwd(), filename)
    # Fallback: try to serve from assets/ (CSS/JS/fonts/images)
    assets_path = os.path.join(os.getcwd(), 'assets', filename)
    if os.path.exists(assets_path):
        return send_from_directory(os.path.join(os.getcwd(), 'assets'), filename)
    abort(404)


@app.route("/submit/appointment", methods=["POST"]) 
def submit_appointment():
    """Endpoint specifically for the appointment form.

    Expected form fields (flexible):
      - name or text
      - email
      - message (or textarea named 'message')

    This route returns a JSON response and will redirect back to the referer for
    browser form submissions (non-AJAX) with a query flag `?sent=1`.
    """
    form = request.form or request.get_json(silent=True) or {}
    # Accept both name styles
    mapping = {
        "Name": form.get("name") or form.get("text") or "",
        "Email": form.get("email") or "",
        "Message": form.get("message") or form.get("msg") or form.get("feedback") or "",
    }
    fields = {k: v for k, v in mapping.items()}

    # Build message and send
    msg = _format_message("Appointment", fields, remote_addr=request.remote_addr)
    ok, info = _send_telegram_message(msg)
    if not ok:
        return jsonify({"ok": False, "error": info}), 500

    # If browser-navigation form submission, redirect back politely
    if request.headers.get("Accept", "").find("text/html") != -1:
        ref = request.headers.get("Referer") or "/"
        if "?" in ref:
            return redirect(ref + "&sent=1")
        return redirect(ref + "?sent=1")

    return jsonify({"ok": True, "result": info})


@app.route("/submit/contact", methods=["POST"]) 
def submit_contact():
    """Endpoint specifically for the contact form.

    Expected fields:
      - name
      - email
      - phone_number
      - subject
      - message
    """
    form = request.form or request.get_json(silent=True) or {}
    expected = ["name", "email", "phone_number", "subject", "message"]
    fields = _extract_fields(form, expected_keys=expected)

    # Normalize keys to prettier names
    nice = {}
    if "name" in fields:
        nice["Name"] = fields.pop("name")
    if "email" in fields:
        nice["Email"] = fields.pop("email")
    if "phone_number" in fields:
        nice["Phone"] = fields.pop("phone_number")
    if "subject" in fields:
        nice["Subject"] = fields.pop("subject")
    if "message" in fields:
        nice["Message"] = fields.pop("message")
    # Any remaining fields
    for k, v in fields.items():
        nice[k] = v

    msg = _format_message("Contact", nice, remote_addr=request.remote_addr)
    ok, info = _send_telegram_message(msg)
    if not ok:
        return jsonify({"ok": False, "error": info}), 500

    if request.headers.get("Accept", "").find("text/html") != -1:
        ref = request.headers.get("Referer") or "/"
        if "?" in ref:
            return redirect(ref + "&sent=1")
        return redirect(ref + "?sent=1")

    return jsonify({"ok": True, "result": info})


@app.route("/submit", methods=["POST"]) 
def submit_generic():
    """Generic endpoint that accepts either form and attempts to auto-detect the source.

    Detection heuristics:
      - presence of `phone_number` or `subject` -> contact
      - presence of `text` or `name` and no phone_number -> appointment
    """
    form = request.form or request.get_json(silent=True) or {}
    # Convert ImmutableMultiDict to plain dict-like access
    keys = set(form.keys())

    if "phone_number" in keys or "subject" in keys:
        return submit_contact()
    # appointment fallback
    return submit_appointment()


# Minimal CORS to accept cross-origin form posts (adjust for production)
@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

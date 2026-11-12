import os
from flask import request, jsonify

def require_api_key():
    # Allow OPTIONS for CORS pre-flight without checking auth
    if request.method == "OPTIONS":
        return ("", 204)

    # Allow static files and root UI page
    if request.path == "/" or request.path.startswith("/static") or request.path == "/favicon.ico":
        return None

    expected_key = os.environ.get("API_KEY", "koya-clinic-api-key-2026-x9f3m7")
    provided_key = request.headers.get("X-API-Key")

    if not provided_key or provided_key != expected_key:
        return jsonify({"error": "Missing or invalid API key"}), 401

    return None

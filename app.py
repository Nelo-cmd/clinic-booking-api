import os
from flask import Flask, jsonify, send_from_directory
from dotenv import load_dotenv

load_dotenv()

from db import init_db
from middleware.auth import require_api_key
from routes.reset import reset_bp
from routes.slots import slots_bp
from routes.bookings import bookings_bp

def create_app():
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    
    # Initialize DB schemas
    init_db()

    # Authentication middleware
    app.before_request(require_api_key)

    # Register API blueprints
    app.register_blueprint(reset_bp)
    app.register_blueprint(slots_bp)
    app.register_blueprint(bookings_bp)

    # Serve simple frontend at root
    @app.route("/")
    def index():
        return send_from_directory("static", "index.html")

    # Global error handlers to guarantee {"error": "..."} format
    @app.errorhandler(400)
    def bad_request(e):
        return jsonify({"error": str(e.description if hasattr(e, "description") else "Bad request")}), 400

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        return jsonify({"error": "Method not allowed"}), 405

    @app.errorhandler(500)
    def internal_error(e):
        return jsonify({"error": "Internal server error"}), 500

    # Add CORS headers so web clients or external callers can access the API
    @app.after_request
    def add_cors_headers(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key, Idempotency-Key"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
        return response

    return app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

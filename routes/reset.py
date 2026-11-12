from flask import Blueprint, request, jsonify
from db import get_db_connection, is_postgres, parse_iso

reset_bp = Blueprint("reset", __name__)

@reset_bp.route("/reset", methods=["POST"])
def reset():
    data = request.get_json(silent=True) or {}
    slots = data.get("slots", [])

    with get_db_connection() as conn:
        if is_postgres():
            with conn.cursor() as cur:
                # Clear all previous data
                cur.execute("DELETE FROM bookings;")
                cur.execute("DELETE FROM slots;")
                cur.execute("DELETE FROM idempotency_keys;")
                cur.execute("DELETE FROM rate_limits;")

                insert_sql = """
                INSERT INTO slots (id, provider, start_time, end_time)
                VALUES (%s, %s, %s, %s);
                """
                for s in slots:
                    cur.execute(insert_sql, (
                        s["id"],
                        s["provider"],
                        s["start"],
                        s["end"]
                    ))
            conn.commit()
        else:
            cur = conn.cursor()
            cur.execute("DELETE FROM bookings;")
            cur.execute("DELETE FROM slots;")
            cur.execute("DELETE FROM idempotency_keys;")
            cur.execute("DELETE FROM rate_limits;")

            insert_sql = """
            INSERT INTO slots (id, provider, start_time, end_time)
            VALUES (?, ?, ?, ?);
            """
            for s in slots:
                cur.execute(insert_sql, (
                    s["id"],
                    s["provider"],
                    s["start"],
                    s["end"]
                ))
            conn.commit()

    return jsonify({"message": "Done"}), 200

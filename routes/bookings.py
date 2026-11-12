import re
import uuid
import json
import base64
import hashlib
import math
from datetime import datetime, timezone, timedelta
from flask import Blueprint, request, jsonify, make_response
from db import get_db_connection, is_postgres, format_iso, parse_iso

bookings_bp = Blueprint("bookings", __name__)

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")

def encode_cursor(created_at, booking_id):
    iso = format_iso(created_at)
    raw = f"{iso}|{booking_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("utf-8")

def decode_cursor(cursor_str):
    try:
        raw = base64.urlsafe_b64decode(cursor_str.encode("utf-8")).decode("utf-8")
        parts = raw.split("|", 1)
        if len(parts) == 2:
            return parse_iso(parts[0]), parts[1]
    except Exception:
        pass
    return None, None

@bookings_bp.route("/bookings", methods=["POST"])
def create_booking():
    raw_body = request.get_data()
    data = request.get_json(silent=True) or {}
    
    idempotency_key = request.headers.get("Idempotency-Key")
    canonical_body = json.dumps(data, sort_keys=True)
    body_hash = hashlib.sha256(canonical_body.encode("utf-8")).hexdigest()

    # Level 4: Check Idempotency Key
    if idempotency_key:
        with get_db_connection() as conn:
            if is_postgres():
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT body_hash, status_code, response_body FROM idempotency_keys WHERE key = %s;",
                        (idempotency_key,)
                    )
                    row = cur.fetchone()
                    if row:
                        stored_hash, status_code, response_body = row
                        if stored_hash == body_hash:
                            return make_response(response_body, status_code, {"Content-Type": "application/json"})
                        else:
                            return jsonify({"error": "Idempotency key reused with different body"}), 422
            else:
                cur = conn.cursor()
                cur.execute(
                    "SELECT body_hash, status_code, response_body FROM idempotency_keys WHERE key = ?;",
                    (idempotency_key,)
                )
                row = cur.fetchone()
                if row:
                    stored_hash = row["body_hash"]
                    status_code = row["status_code"]
                    response_body = row["response_body"]
                    if stored_hash == body_hash:
                        return make_response(response_body, status_code, {"Content-Type": "application/json"})
                    else:
                        return jsonify({"error": "Idempotency key reused with different body"}), 422

    # Level 2: Validate required fields
    slot_id = data.get("slot_id")
    name = data.get("name")
    email = data.get("email")

    if not slot_id or not isinstance(slot_id, str) or not slot_id.strip():
        return jsonify({"error": "slot_id is required"}), 400
    if not name or not isinstance(name, str) or not name.strip():
        return jsonify({"error": "name is required"}), 400
    if not email or not isinstance(email, str) or not email.strip():
        return jsonify({"error": "email is required"}), 400

    slot_id = slot_id.strip()
    name = name.strip()
    email = email.strip().lower()

    if not EMAIL_REGEX.match(email):
        return jsonify({"error": "Invalid email address"}), 400

    # Level 5: Rate Limiting (per email, 5 attempts per minute)
    # Failed attempts included. The 6th returns 429 with Retry-After header.
    with get_db_connection() as conn:
        now_utc = datetime.now(timezone.utc)
        one_min_ago = now_utc - timedelta(seconds=60)

        if is_postgres():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT attempted_at 
                    FROM rate_limits 
                    WHERE email = %s AND attempted_at >= %s 
                    ORDER BY attempted_at ASC;
                    """,
                    (email, one_min_ago)
                )
                attempts = cur.fetchall()
                if len(attempts) >= 5:
                    # 6th attempt within window -> 429
                    # Retry-After is whole seconds until the oldest of the 5 falls outside the 60s window
                    oldest_time = attempts[0][0]
                    diff = (oldest_time + timedelta(seconds=60)) - now_utc
                    retry_after = max(1, math.ceil(diff.total_seconds()))
                    resp = make_response(jsonify({"error": "Rate limit exceeded. Try again later."}), 429)
                    resp.headers["Retry-After"] = str(retry_after)
                    return resp

                # Record this attempt
                cur.execute(
                    "INSERT INTO rate_limits (email, attempted_at) VALUES (%s, %s);",
                    (email, now_utc)
                )
            conn.commit()
        else:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT attempted_at 
                FROM rate_limits 
                WHERE email = ? AND attempted_at >= ? 
                ORDER BY attempted_at ASC;
                """,
                (email, format_iso(one_min_ago))
            )
            attempts = cur.fetchall()
            if len(attempts) >= 5:
                oldest_str = attempts[0]["attempted_at"]
                oldest_time = parse_iso(oldest_str)
                diff = (oldest_time + timedelta(seconds=60)) - now_utc
                retry_after = max(1, math.ceil(diff.total_seconds()))
                resp = make_response(jsonify({"error": "Rate limit exceeded. Try again later."}), 429)
                resp.headers["Retry-After"] = str(retry_after)
                return resp

            cur.execute(
                "INSERT INTO rate_limits (email, attempted_at) VALUES (?, ?);",
                (email, format_iso(now_utc))
            )
            conn.commit()

    # Now verify slot exists and hasn't started yet
    with get_db_connection() as conn:
        now_utc = datetime.now(timezone.utc)

        if is_postgres():
            try:
                with conn.cursor() as cur:
                    # Level 3: Concurrency protection using FOR UPDATE
                    cur.execute("SELECT id, start_time FROM slots WHERE id = %s FOR UPDATE;", (slot_id,))
                    slot_row = cur.fetchone()
                    if not slot_row:
                        return jsonify({"error": "Slot not found"}), 404

                    start_time = slot_row[1]
                    if isinstance(start_time, str):
                        start_time = parse_iso(start_time)
                    if start_time <= now_utc:
                        return jsonify({"error": "Slot has already started"}), 400

                    # Check if already booked
                    cur.execute(
                        "SELECT id FROM bookings WHERE slot_id = %s AND status = 'confirmed' FOR UPDATE;",
                        (slot_id,)
                    )
                    existing_booking = cur.fetchone()
                    if existing_booking:
                        return jsonify({"error": "Slot already booked"}), 409

                    # Create booking
                    booking_id = str(uuid.uuid4())
                    cur.execute(
                        """
                        INSERT INTO bookings (id, slot_id, name, email, status, created_at)
                        VALUES (%s, %s, %s, %s, 'confirmed', %s)
                        RETURNING id, slot_id, name, email, status, created_at;
                        """,
                        (booking_id, slot_id, name, email, now_utc)
                    )
                    b_row = cur.fetchone()
                    booking = {
                        "id": b_row[0],
                        "slot_id": b_row[1],
                        "name": b_row[2],
                        "email": b_row[3],
                        "status": b_row[4],
                        "created_at": format_iso(b_row[5])
                    }

                    # Store idempotency key if provided
                    if idempotency_key:
                        cur.execute(
                            """
                            INSERT INTO idempotency_keys (key, body_hash, status_code, response_body, created_at)
                            VALUES (%s, %s, %s, %s, %s);
                            """,
                            (idempotency_key, body_hash, 201, json.dumps(booking), now_utc)
                        )
                conn.commit()
                return jsonify(booking), 201

            except psycopg2.IntegrityError:
                conn.rollback()
                return jsonify({"error": "Slot already booked"}), 409
            except Exception as e:
                conn.rollback()
                raise e

        else:
            # SQLite mode
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE;")
            try:
                cur.execute("SELECT id, start_time FROM slots WHERE id = ?;", (slot_id,))
                slot_row = cur.fetchone()
                if not slot_row:
                    cur.execute("ROLLBACK;")
                    return jsonify({"error": "Slot not found"}), 404

                start_time = parse_iso(slot_row["start_time"])
                if start_time <= now_utc:
                    cur.execute("ROLLBACK;")
                    return jsonify({"error": "Slot has already started"}), 400

                cur.execute("SELECT id FROM bookings WHERE slot_id = ? AND status = 'confirmed';", (slot_id,))
                if cur.fetchone():
                    cur.execute("ROLLBACK;")
                    return jsonify({"error": "Slot already booked"}), 409

                booking_id = str(uuid.uuid4())
                created_iso = format_iso(now_utc)
                cur.execute(
                    """
                    INSERT INTO bookings (id, slot_id, name, email, status, created_at)
                    VALUES (?, ?, ?, ?, 'confirmed', ?);
                    """,
                    (booking_id, slot_id, name, email, created_iso)
                )

                booking = {
                    "id": booking_id,
                    "slot_id": slot_id,
                    "name": name,
                    "email": email,
                    "status": "confirmed",
                    "created_at": created_iso
                }

                if idempotency_key:
                    cur.execute(
                        """
                        INSERT INTO idempotency_keys (key, body_hash, status_code, response_body, created_at)
                        VALUES (?, ?, ?, ?, ?);
                        """,
                        (idempotency_key, body_hash, 201, json.dumps(booking), created_iso)
                    )

                cur.execute("COMMIT;")
                return jsonify(booking), 201
            except Exception as err:
                cur.execute("ROLLBACK;")
                if "UNIQUE constraint failed" in str(err):
                    return jsonify({"error": "Slot already booked"}), 409
                raise err

@bookings_bp.route("/bookings/<string:booking_id>", methods=["GET"])
def get_booking(booking_id):
    with get_db_connection() as conn:
        if is_postgres():
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, slot_id, name, email, status, created_at FROM bookings WHERE id = %s;",
                    (booking_id,)
                )
                row = cur.fetchone()
                if not row:
                    return jsonify({"error": "Booking not found"}), 404
                return jsonify({
                    "id": row[0],
                    "slot_id": row[1],
                    "name": row[2],
                    "email": row[3],
                    "status": row[4],
                    "created_at": format_iso(row[5])
                }), 200
        else:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, slot_id, name, email, status, created_at FROM bookings WHERE id = ?;",
                (booking_id,)
            )
            row = cur.fetchone()
            if not row:
                return jsonify({"error": "Booking not found"}), 404
            return jsonify({
                "id": row["id"],
                "slot_id": row["slot_id"],
                "name": row["name"],
                "email": row["email"],
                "status": row["status"],
                "created_at": format_iso(row["created_at"])
            }), 200

@bookings_bp.route("/bookings/<string:booking_id>", methods=["DELETE"])
def cancel_booking(booking_id):
    with get_db_connection() as conn:
        if is_postgres():
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, slot_id, name, email, status, created_at FROM bookings WHERE id = %s FOR UPDATE;",
                    (booking_id,)
                )
                row = cur.fetchone()
                if not row:
                    return jsonify({"error": "Booking not found"}), 404
                if row[4] == "cancelled":
                    return jsonify({"error": "Booking already cancelled"}), 409

                cur.execute(
                    "UPDATE bookings SET status = 'cancelled' WHERE id = %s RETURNING id, slot_id, name, email, status, created_at;",
                    (booking_id,)
                )
                updated = cur.fetchone()
            conn.commit()
            return jsonify({
                "id": updated[0],
                "slot_id": updated[1],
                "name": updated[2],
                "email": updated[3],
                "status": updated[4],
                "created_at": format_iso(updated[5])
            }), 200
        else:
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE;")
            cur.execute(
                "SELECT id, slot_id, name, email, status, created_at FROM bookings WHERE id = ?;",
                (booking_id,)
            )
            row = cur.fetchone()
            if not row:
                cur.execute("ROLLBACK;")
                return jsonify({"error": "Booking not found"}), 404
            if row["status"] == "cancelled":
                cur.execute("ROLLBACK;")
                return jsonify({"error": "Booking already cancelled"}), 409

            cur.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?;", (booking_id,))
            cur.execute(
                "SELECT id, slot_id, name, email, status, created_at FROM bookings WHERE id = ?;",
                (booking_id,)
            )
            updated = cur.fetchone()
            cur.execute("COMMIT;")
            return jsonify({
                "id": updated["id"],
                "slot_id": updated["slot_id"],
                "name": updated["name"],
                "email": updated["email"],
                "status": updated["status"],
                "created_at": format_iso(updated["created_at"])
            }), 200

@bookings_bp.route("/bookings", methods=["GET"])
def list_bookings():
    # Level 4: List a person's bookings with cursor pagination
    email = request.args.get("email")
    if not email:
        return jsonify({"error": "email parameter is required"}), 400

    email = email.strip().lower()

    limit_str = request.args.get("limit", "10")
    try:
        limit = int(limit_str)
        if limit < 1 or limit > 50:
            return jsonify({"error": "limit must be between 1 and 50"}), 400
    except ValueError:
        return jsonify({"error": "limit must be an integer"}), 400

    cursor_str = request.args.get("cursor")
    cursor_time, cursor_id = None, None
    if cursor_str:
        cursor_time, cursor_id = decode_cursor(cursor_str)
        if not cursor_time or not cursor_id:
            return jsonify({"error": "invalid cursor"}), 400

    with get_db_connection() as conn:
        if is_postgres():
            with conn.cursor() as cur:
                if cursor_time and cursor_id:
                    query = """
                    SELECT id, slot_id, name, email, status, created_at
                    FROM bookings
                    WHERE LOWER(email) = %s
                      AND (created_at > %s OR (created_at = %s AND id > %s))
                    ORDER BY created_at ASC, id ASC
                    LIMIT %s;
                    """
                    cur.execute(query, (email, cursor_time, cursor_time, cursor_id, limit + 1))
                else:
                    query = """
                    SELECT id, slot_id, name, email, status, created_at
                    FROM bookings
                    WHERE LOWER(email) = %s
                    ORDER BY created_at ASC, id ASC
                    LIMIT %s;
                    """
                    cur.execute(query, (email, limit + 1))

                rows = cur.fetchall()
                has_next = len(rows) > limit
                paged_rows = rows[:limit]

                items = []
                for r in paged_rows:
                    items.append({
                        "id": r[0],
                        "slot_id": r[1],
                        "name": r[2],
                        "email": r[3],
                        "status": r[4],
                        "created_at": format_iso(r[5])
                    })

                next_cursor = None
                if has_next:
                    last = paged_rows[-1]
                    next_cursor = encode_cursor(last[5], last[0])

                return jsonify({
                    "bookings": items,
                    "next_cursor": next_cursor
                }), 200

        else:
            cur = conn.cursor()
            if cursor_time and cursor_id:
                query = """
                SELECT id, slot_id, name, email, status, created_at
                FROM bookings
                WHERE LOWER(email) = ?
                  AND (created_at > ? OR (created_at = ? AND id > ?))
                ORDER BY created_at ASC, id ASC
                LIMIT ?;
                """
                cur.execute(query, (email, format_iso(cursor_time), format_iso(cursor_time), cursor_id, limit + 1))
            else:
                query = """
                SELECT id, slot_id, name, email, status, created_at
                FROM bookings
                WHERE LOWER(email) = ?
                ORDER BY created_at ASC, id ASC
                LIMIT ?;
                """
                cur.execute(query, (email, limit + 1))

            rows = cur.fetchall()
            has_next = len(rows) > limit
            paged_rows = rows[:limit]

            items = []
            for r in paged_rows:
                items.append({
                    "id": r["id"],
                    "slot_id": r["slot_id"],
                    "name": r["name"],
                    "email": r["email"],
                    "status": r["status"],
                    "created_at": format_iso(r["created_at"])
                })

            next_cursor = None
            if has_next:
                last = paged_rows[-1]
                next_cursor = encode_cursor(last["created_at"], last["id"])

            return jsonify({
                "bookings": items,
                "next_cursor": next_cursor
            }), 200

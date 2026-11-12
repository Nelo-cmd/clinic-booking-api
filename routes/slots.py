from flask import Blueprint, request, jsonify
from db import get_db_connection, is_postgres, format_iso

slots_bp = Blueprint("slots", __name__)

@slots_bp.route("/slots", methods=["GET"])
def list_slots():
    provider = request.args.get("provider")

    with get_db_connection() as conn:
        if is_postgres():
            with conn.cursor() as cur:
                query = """
                SELECT 
                    s.id, 
                    s.provider, 
                    s.start_time, 
                    s.end_time,
                    CASE WHEN b.id IS NULL THEN TRUE ELSE FALSE END as available
                FROM slots s
                LEFT JOIN bookings b ON b.slot_id = s.id AND b.status = 'confirmed'
                """
                params = []
                if provider:
                    query += " WHERE s.provider = %s"
                    params.append(provider)
                
                query += " ORDER BY s.start_time ASC, s.id ASC;"
                cur.execute(query, params)
                rows = cur.fetchall()

                slots = []
                for row in rows:
                    slots.append({
                        "id": row[0],
                        "provider": row[1],
                        "start": format_iso(row[2]),
                        "end": format_iso(row[3]),
                        "available": bool(row[4])
                    })
        else:
            cur = conn.cursor()
            query = """
            SELECT 
                s.id, 
                s.provider, 
                s.start_time, 
                s.end_time,
                CASE WHEN b.id IS NULL THEN 1 ELSE 0 END as available
            FROM slots s
            LEFT JOIN bookings b ON b.slot_id = s.id AND b.status = 'confirmed'
            """
            params = []
            if provider:
                query += " WHERE s.provider = ?"
                params.append(provider)

            query += " ORDER BY s.start_time ASC, s.id ASC;"
            cur.execute(query, params)
            rows = cur.fetchall()

            slots = []
            for row in rows:
                slots.append({
                    "id": row["id"],
                    "provider": row["provider"],
                    "start": format_iso(row["start_time"]),
                    "end": format_iso(row["end_time"]),
                    "available": bool(row["available"])
                })

    return jsonify({"slots": slots}), 200

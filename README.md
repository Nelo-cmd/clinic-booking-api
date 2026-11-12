# Clinic Booking API & Web Application

A robust, production-grade appointment booking API and responsive web frontend built with **Python (Flask)**, **PostgreSQL (Neon)**, and **Gunicorn**, built for the Koya Cohort 4 assessment.

Live Web Application: Serves the web interface directly at `/` and the REST API at `/slots`, `/bookings`, and `/reset`.

---

## 🚀 Overview of Levels & Implementation

### Level 1 — Core Booking Lifecycle
* `POST /reset`: Clears existing bookings, idempotency logs, and rate limit counters, and re-seeds appointment slots (including past slots if provided).
* `GET /slots`: Lists all slots ordered by earliest `start_time` first. Returns `available: true/false` indicating whether a slot is free or has an active confirmed booking. Supports `?provider=` exact-match filter.
* `POST /bookings`: Books an available slot and returns `201 Created` with status `confirmed` and an auto-generated unique booking `id`.
* `GET /bookings/{id}`: Looks up a booking by ID (`200 OK` or `404 Not Found`).

### Level 2 — Security & Validation
* **Authentication**: Enforces the `X-API-Key` header across all API endpoints. Missing or incorrect keys return `401 Unauthorized` with `{"error": "Missing or invalid API key"}`. (The web UI and static assets at `/` remain accessible to patients).
* **Validation**:
  * Missing or empty `slot_id`, `name`, or `email` returns `400 Bad Request`.
  * Invalid email formats (checked via regex) return `400 Bad Request`.
  * Name and email are trimmed of leading/trailing whitespace, and email is stored in lowercase.
  * Non-existent `slot_id` returns `404 Not Found`.
  * Slots that have already started (`start <= now()`) return `400 Bad Request`.

### Level 3 — Concurrency Safety & Cancellation
* **Zero Double-Booking Guarantee**:
  * Utilizes database row-level locking (`SELECT ... FOR UPDATE`) inside atomic transactions.
  * Enforced at the database layer with a partial unique index:
    ```sql
    CREATE UNIQUE INDEX idx_one_confirmed_slot ON bookings (slot_id) WHERE status = 'confirmed';
    ```
  * Even if two concurrent requests hit the server at the exact same millisecond, only one can succeed; the other receives `409 Conflict`.
* **Cancellation**:
  * `DELETE /bookings/{id}` sets booking status to `cancelled` and returns the cancelled booking.
  * Automatically frees the slot for another patient.
  * Attempting to cancel an already-cancelled booking returns `409 Conflict`.
  * Non-existent booking returns `404 Not Found`.

### Level 4 — Idempotency & Cursor Pagination
* **Idempotency Keys**:
  * Supports the optional `Idempotency-Key` header on `POST /bookings`.
  * Hashes canonical request payload (SHA-256).
  * Retrying with the same key and same body returns the original cached response (same status code, same booking `id`) without creating a duplicate booking.
  * Retrying with the same key and a *different* body returns `422 Unprocessable Entity`.
* **Cursor-Based Pagination**:
  * `GET /bookings?email={email}` lists a person's bookings oldest first (email match is case-insensitive).
  * Supports `limit` query param (1 to 50, default 10; invalid values return `400 Bad Request`).
  * Supports `cursor` query param using URL-safe base64 tokens. Returns `next_cursor: "<token>"` or `next_cursor: null` on the final page.

### Level 5 — Abuse Prevention & Rate Limiting
* **Per-Email Rate Limiting**:
  * Limits booking attempts to at most 5 per email per 60-second rolling window.
  * Failed attempts (e.g. invalid slot, already booked) count toward the limit.
  * The 6th attempt returns `429 Too Many Requests` with a `Retry-After: <seconds>` header specifying the exact whole number of seconds until a slot in the rolling window opens up.
  * Other email addresses are completely unaffected.

---

## 🛠 Tech Stack

* **Language**: Python 3.12+
* **Framework**: Flask 3.1+
* **Database**: PostgreSQL (Neon Serverless Postgres) with `psycopg2-binary` (and local SQLite fallback for testing)
* **WSGI Server**: Gunicorn 23+
* **Frontend**: Responsive Single-Page Application (HTML5, Vanilla CSS & JavaScript)

---

## 📋 Local Setup & Testing

### 1. Clone & Install Dependencies
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment
Create a `.env` file or export environment variables:
```bash
PORT=5000
API_KEY=koya-clinic-api-key-2026-x9f3m7
# Optional: connect to Neon Postgres, or omit to run in SQLite mode
DATABASE_URL=postgresql://user:password@host/dbname?sslmode=require
```

### 3. Run Automated Tests
```bash
python3 -m unittest test_all_levels.py -v
```
All 5 levels of automated tests will execute and verify each requirement.

### 4. Start the Application
```bash
gunicorn app:app --bind 0.0.0.0:5000
```
Open your browser at `http://localhost:5000` to interact with the clinic booking web page!

---

## ☁️ Deployment Guide (Render + Neon)

### 1. Database (Neon)
1. Sign up at [neon.tech](https://neon.tech) and click **Create Project**.
2. Copy the connection string (`postgresql://username:password@ep-xxxx.region.aws.neon.tech/neondb?sslmode=require`).

### 2. Web Service (Render)
1. Push this repository to GitHub.
2. In [Render Dashboard](https://dashboard.render.com), click **New +** -> **Web Service**.
3. Connect your GitHub repository.
4. Set:
   * **Runtime**: `Python 3`
   * **Build Command**: `pip install -r requirements.txt`
   * **Start Command**: `gunicorn app:app --bind 0.0.0.0:$PORT`
5. In **Environment Variables**, add:
   * `API_KEY`: `koya-clinic-api-key-2026-x9f3m7` (or your chosen key)
   * `DATABASE_URL`: Your Neon Postgres connection string.
6. Click **Deploy Web Service**.

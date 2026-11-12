import unittest
import json
import time
from datetime import datetime, timezone, timedelta
from app import app
from db import init_db

API_KEY = "koya-clinic-api-key-2026-x9f3m7"

class TestClinicApiLevels(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}
        
        now = datetime.now(timezone.utc)
        self.future1 = (now + timedelta(days=1)).strftime("%Y-%m-%dT09:00:00Z")
        self.future1_end = (now + timedelta(days=1)).strftime("%Y-%m-%dT09:30:00Z")
        self.future2 = (now + timedelta(days=2)).strftime("%Y-%m-%dT10:00:00Z")
        self.future2_end = (now + timedelta(days=2)).strftime("%Y-%m-%dT10:30:00Z")
        self.past = (now - timedelta(days=1)).strftime("%Y-%m-%dT09:00:00Z")
        self.past_end = (now - timedelta(days=1)).strftime("%Y-%m-%dT09:30:00Z")

        # Reset slots
        res = self.client.post("/reset", headers=self.headers, json={
            "slots": [
                {"id": "s-past", "provider": "Dr Past", "start": self.past, "end": self.past_end},
                {"id": "s1", "provider": "Dr Ade", "start": self.future1, "end": self.future1_end},
                {"id": "s2", "provider": "Dr Chioma", "start": self.future2, "end": self.future2_end}
            ]
        })
        self.assertEqual(res.status_code, 200)

    # --- LEVEL 1 TESTS ---
    def test_level_1_list_book_lookup(self):
        # 1. List slots
        res = self.client.get("/slots", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("slots", data)
        slots = data["slots"]
        self.assertEqual(len(slots), 3)
        self.assertTrue(slots[0]["available"])

        # 2. Book one slot
        res = self.client.post("/bookings", headers=self.headers, json={
            "slot_id": "s1",
            "name": "Ada Obi",
            "email": "ada@example.com"
        })
        self.assertEqual(res.status_code, 201)
        booking = res.get_json()
        self.assertEqual(booking["slot_id"], "s1")
        self.assertEqual(booking["name"], "Ada Obi")
        self.assertEqual(booking["email"], "ada@example.com")
        self.assertEqual(booking["status"], "confirmed")
        self.assertIn("id", booking)
        self.assertIn("created_at", booking)

        booking_id = booking["id"]

        # 3. Lookup booking
        res = self.client.get(f"/bookings/{booking_id}", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        lookup = res.get_json()
        self.assertEqual(lookup["id"], booking_id)
        self.assertEqual(lookup["status"], "confirmed")

    # --- LEVEL 2 TESTS ---
    def test_level_2_auth_and_validations(self):
        # Missing API Key -> 401
        res = self.client.get("/slots")
        self.assertEqual(res.status_code, 401)
        self.assertIn("error", res.get_json())

        # Wrong API Key -> 401
        res = self.client.get("/slots", headers={"X-API-Key": "wrong-key"})
        self.assertEqual(res.status_code, 401)

        # Missing field (name missing) -> 400
        res = self.client.post("/bookings", headers=self.headers, json={
            "slot_id": "s1",
            "email": "ada@example.com"
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn("error", res.get_json())

        # Invalid email -> 400
        res = self.client.post("/bookings", headers=self.headers, json={
            "slot_id": "s1",
            "name": "Ada",
            "email": "not-an-email"
        })
        self.assertEqual(res.status_code, 400)

        # Slot not found -> 404
        res = self.client.post("/bookings", headers=self.headers, json={
            "slot_id": "nonexistent-slot",
            "name": "Ada",
            "email": "ada@example.com"
        })
        self.assertEqual(res.status_code, 404)

        # Slot has already started -> 400
        res = self.client.post("/bookings", headers=self.headers, json={
            "slot_id": "s-past",
            "name": "Ada",
            "email": "ada@example.com"
        })
        self.assertEqual(res.status_code, 400)

        # Trims spaces and lowercases email
        res = self.client.post("/bookings", headers=self.headers, json={
            "slot_id": "s2",
            "name": "   Emeka Okafor   ",
            "email": "  EMEKA@Example.COM  "
        })
        self.assertEqual(res.status_code, 201)
        b = res.get_json()
        self.assertEqual(b["name"], "Emeka Okafor")
        self.assertEqual(b["email"], "emeka@example.com")

    # --- LEVEL 3 TESTS ---
    def test_level_3_prevent_double_booking_and_cancellation(self):
        # Book s1
        res = self.client.post("/bookings", headers=self.headers, json={
            "slot_id": "s1",
            "name": "Person One",
            "email": "p1@example.com"
        })
        self.assertEqual(res.status_code, 201)
        b1 = res.get_json()

        # Second person tries to book s1 -> 409
        res2 = self.client.post("/bookings", headers=self.headers, json={
            "slot_id": "s1",
            "name": "Person Two",
            "email": "p2@example.com"
        })
        self.assertEqual(res2.status_code, 409)
        self.assertIn("error", res2.get_json())

        # Cancel booking
        del_res = self.client.delete(f"/bookings/{b1['id']}", headers=self.headers)
        self.assertEqual(del_res.status_code, 200)
        self.assertEqual(del_res.get_json()["status"], "cancelled")

        # Second cancellation -> 409
        del_res_again = self.client.delete(f"/bookings/{b1['id']}", headers=self.headers)
        self.assertEqual(del_res_again.status_code, 409)

        # Now slot s1 is freed! Person Two can now book it
        res3 = self.client.post("/bookings", headers=self.headers, json={
            "slot_id": "s1",
            "name": "Person Two",
            "email": "p2@example.com"
        })
        self.assertEqual(res3.status_code, 201)

    # --- LEVEL 4 TESTS ---
    def test_level_4_idempotency_and_pagination(self):
        # Idempotent booking
        headers_with_idem = dict(self.headers)
        headers_with_idem["Idempotency-Key"] = "unique-key-123"

        body1 = {"slot_id": "s1", "name": "Ada", "email": "ada@example.com"}
        res1 = self.client.post("/bookings", headers=headers_with_idem, json=body1)
        self.assertEqual(res1.status_code, 201)
        data1 = res1.get_json()

        # Retry with same key & same body -> same response and same ID!
        res2 = self.client.post("/bookings", headers=headers_with_idem, json=body1)
        self.assertEqual(res2.status_code, 201)
        data2 = res2.get_json()
        self.assertEqual(data1["id"], data2["id"])

        # Same key with different body -> 422
        body2 = {"slot_id": "s2", "name": "Ada", "email": "ada@example.com"}
        res3 = self.client.post("/bookings", headers=headers_with_idem, json=body2)
        self.assertEqual(res3.status_code, 422)

        # Cursor pagination:
        # Book s2 for same email
        res_s2 = self.client.post("/bookings", headers=self.headers, json=body2)
        self.assertEqual(res_s2.status_code, 201)

        # List bookings with limit=1
        page1 = self.client.get("/bookings?email=ADA@EXAMPLE.COM&limit=1", headers=self.headers)
        self.assertEqual(page1.status_code, 200)
        p1_data = page1.get_json()
        self.assertEqual(len(p1_data["bookings"]), 1)
        self.assertIsNotNone(p1_data["next_cursor"])

        # Page 2 using cursor
        cursor = p1_data["next_cursor"]
        page2 = self.client.get(f"/bookings?email=ada@example.com&limit=1&cursor={cursor}", headers=self.headers)
        self.assertEqual(page2.status_code, 200)
        p2_data = page2.get_json()
        self.assertEqual(len(p2_data["bookings"]), 1)
        self.assertIsNone(p2_data["next_cursor"])

        # Invalid limit -> 400
        res_bad_limit = self.client.get("/bookings?email=ada@example.com&limit=0", headers=self.headers)
        self.assertEqual(res_bad_limit.status_code, 400)

    # --- LEVEL 5 TESTS ---
    def test_level_5_rate_limiting(self):
        # 5 attempts per email per minute, 6th returns 429
        rate_email = "spammer@example.com"
        for i in range(5):
            # Attempts can be failures or successes
            res = self.client.post("/bookings", headers=self.headers, json={
                "slot_id": "nonexistent",
                "name": "Spammer",
                "email": rate_email
            })
            # 404 because slot doesn't exist, but it counts as an attempt!
            self.assertEqual(res.status_code, 404)

        # 6th attempt should return 429
        res6 = self.client.post("/bookings", headers=self.headers, json={
            "slot_id": "s1",
            "name": "Spammer",
            "email": rate_email
        })
        self.assertEqual(res6.status_code, 429)
        self.assertIn("Retry-After", res6.headers)
        retry_after = int(res6.headers["Retry-After"])
        self.assertGreaterEqual(retry_after, 1)

        # Different email is unaffected!
        res_other = self.client.post("/bookings", headers=self.headers, json={
            "slot_id": "s1",
            "name": "Innocent",
            "email": "innocent@example.com"
        })
        self.assertEqual(res_other.status_code, 201)

if __name__ == "__main__":
    unittest.main()

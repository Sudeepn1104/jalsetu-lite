# JalSetu Lite — API Contract (LOCK BEFORE BUILDING)

Backend and frontend teams: do not change field names once work starts.
If something must change, post it in the team chat and get a thumbs-up first.

Base URL (local dev): `http://localhost:8000`

## Order states
```
SEARCHING -> OFFERED -> CONFIRMED -> DISPATCHED -> ARRIVED -> DELIVERED
                                                            -> DISPUTED
CONFIRMED -> CANCELLED (no-show only)
```

## 1. POST /search
Request:
```json
{ "capacity_l": 6000, "water_type": "fresh", "lat": 12.92, "lng": 74.85 }
```
Response (`200`):
```json
{
  "offers": [
    {
      "operator_id": "P",
      "name": "BWSSB Sanchari Cauvery",
      "type": "public",
      "price": 740,
      "price_per_1000l": 123,
      "eta_min": 22,
      "distance_km": 1.4,
      "rating": 4.2,
      "flagged": false
    },
    {
      "operator_id": "D",
      "name": "City Tanker Hub",
      "type": "private",
      "price": 1110,
      "price_per_1000l": 185,
      "eta_min": 18,
      "distance_km": 0.9,
      "rating": 3.9,
      "flagged": true
    }
  ]
}
```
Rules:
- `flagged = true` when `price > public tariff for that capacity` (fresh water only; treated water is never flagged against the public rate).
- Sort offers by `distance_km` ascending.
- `price = round(rate_per_1000l * capacity_l / 1000)` for private; `price = tariff_public[capacity_l]` for public.

## 2. POST /select
```json
{ "capacity_l": 6000, "water_type": "fresh", "operator_id": "D", "lat": 12.92, "lng": 74.85 }
```
Response: `{ "order_id": "JS-1042", "status": "OFFERED" }`

## 3. POST /confirm
```json
{ "order_id": "JS-1042" }
```
Response:
```json
{ "order_id": "JS-1042", "status": "CONFIRMED", "otp": "4821", "backup_phone": null }
```
- `otp` is a 4-digit string, generated server-side, stored hashed (HMAC) in the DB — never store plaintext.
- Optional field in request: `"backup_phone": "9900011122"` (gatekeeper number).

## 4. GET /status/{order_id}
Response:
```json
{ "order_id": "JS-1042", "status": "DISPATCHED", "operator_id": "D", "capacity_l": 6000, "price": 1110 }
```
Frontend polls this every 2 seconds while status is CONFIRMED/DISPATCHED/ARRIVED.

## 5. POST /driver/arrive/{order_id}
No body. Response: `{ "status": "ARRIVED" }`
- Rejects (409) if current status is not DISPATCHED.

## 6. POST /driver/deliver/{order_id}
```json
{ "otp": "4821", "meter_before": 1200, "meter_after": 7200 }
```
Response (success):
```json
{ "status": "DELIVERED", "litres_delivered": 6000 }
```
Response (wrong OTP, `422`):
```json
{ "error": "WRONG_OTP", "attempts_left": 2 }
```
Response (locked after 3 wrong attempts, `409`):
```json
{ "error": "OTP_LOCKED", "status": "DISPUTED" }
```
Response (volume mismatch, `409`):
```json
{ "error": "VOLUME_MISMATCH", "status": "DISPUTED", "litres_delivered": 4000 }
```
Rules:
- Compare OTP using constant-time comparison against the hashed value.
- Volume check: `(meter_after - meter_before) >= 0.95 * capacity_l`, else DISPUTED.
- Max 3 OTP attempts per order, tracked server-side (not trusted from client).

## 7. GET /operators/scores
Response:
```json
{ "operators": [ { "operator_id": "D", "rating": 3.9, "on_time_pct": 78, "deliveries": 158, "disputes": 0 } ] }
```

## Error shape (all endpoints)
```json
{ "error": "SOME_CODE", "message": "human readable" }
```

## Notes for both teams
- All money values are integers (rupees), never floats.
- All timestamps are ISO 8601 UTC strings, added server-side.
- CORS: allow `*` for the hackathon (tighten later, not now).
- No auth for the demo — acceptable per our risk log, flag it in the "Limits" slide.

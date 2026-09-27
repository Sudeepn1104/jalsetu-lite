# JalSetu Lite — API Contract (LOCK BEFORE BUILDING)

Backend and frontend teams: do not change field names once work starts.
If something must change, post it in the team chat and get a thumbs-up first.

Base URL (local dev): `http://localhost:8000`

## Order states
```
SEARCHING -> OFFERED -> CONFIRMED -> DISPATCHED -> ARRIVED -> DELIVERED
     |                   |                                    -> DISPUTED
     +------ CANCELLED <-+
```

Citizens may cancel an order in OFFERED or CONFIRMED before operator dispatch. Dispatch and cancellation use a conditional state transition; exactly one can succeed if they race. Any payment made directly to an operator is outside JalSetu's control and must be refunded by that operator.

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
- `otp` is a 4-digit string, generated server-side, stored hashed (HMAC) in the DB, and returned only to the owning citizen. The citizen UI keeps it hidden until the driver marks the order `ARRIVED`; operators and drivers must never receive it before citizen verification.
- Optional field in request: `"backup_phone": "9900011122"` (gatekeeper number).

## 4. GET /status/{order_id}
Response:
```json
{ "order_id": "JS-1042", "status": "DISPATCHED", "operator_id": "D", "capacity_l": 6000, "price": 1110 }
```
Frontend polls this every 2 seconds while status is CONFIRMED/DISPATCHED/ARRIVED.

## 4a. POST /orders/{order_id}/cancel

No body. Citizens may cancel only their own OFFERED or CONFIRMED orders, before dispatch. Response: `{ "order_id": "JS-1042", "status": "CANCELLED" }`. Returns 409 if dispatch has already started or another state change won the race. The cancellation is recorded in order history.

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

## 8. Authentication and account history

- `POST /auth/register` creates a citizen account and returns a bearer access token.
- `POST /auth/login` accepts `{ "email": "…", "password": "…", "role": "citizen|operator|driver" }` and returns `access_token`, `token_type`, `expires_at`, and a safe `user` object.
- `GET /auth/me` returns the signed-in user. `POST /auth/logout` revokes the current token.
- `GET /auth/history` returns that account’s stored orders and status events (maximum 100, newest first).
- `GET /auth/active-orders` returns the signed-in citizen’s active `OFFERED`, `CONFIRMED`, `DISPATCHED`, and `ARRIVED` orders so a browser session can recover after reload.
- `GET /operator/orders` returns the authenticated operator’s active orders in those same states, newest first (maximum 100), with order ID, status, capacity, water type, price, delivery coordinates, and creation time. Other operators’ orders are not returned.
- Operator and driver accounts cannot self-register. An administrator provisions them through `POST /auth/admin/users` with the `X-Admin-Token` header and an `operator_id` from the seed data.
- The protected administrator panel is available from the login screen. `GET /auth/admin/users` lists safe staff account details; `PUT /auth/admin/users/{user_id}/password` sets a new password and revokes that account’s existing sessions. Passwords are never returned by the API.
- `GET /operators` provides the configured operator IDs and display names for account assignment.
- Send protected requests with `Authorization: Bearer <access_token>`. Only citizens can create and confirm orders. Status, dispatch, arrival, delivery, and citizen order QR requests enforce account role and order/operator ownership. The operator dashboard lists its customer orders automatically and dispatches confirmed orders; it does not create or confirm orders. `/search` and `/operators/scores` remain public.
- `POST /orders/{order_id}/delivery-code` lets only the owning citizen regenerate the driver code while the order is `ARRIVED`. The new four-digit code replaces the previous code; failed-attempt counts are retained and the existing three-attempt dispute lock still applies.
- Passwords are salted PBKDF2 hashes; only a hash of each random session token is stored. Sessions expire after 12 hours by default and logout revokes them.
- Existing anonymous orders are migrated and retained, but cannot be assigned to a citizen retroactively because the legacy database has no citizen identity.

## Notes for both teams
- All money values are integers (rupees), never floats.
- All timestamps are ISO 8601 UTC strings, added server-side.
- CORS: `JALSETHU_CORS_ORIGINS` accepts a comma-separated allowlist; `*` remains the development default. Set the deployed frontend origin for production.
- Existing order request and response field names remain unchanged. Auth uses headers and separate endpoints.
- `GET /upi-qr` returns an authenticated demo QR for any confirmed citizen order and operator. It encodes the invalid demo payee `demo@invalid`, the order amount, and order reference. It is only a UI prototype and cannot route payments; production payment handling is not implemented.

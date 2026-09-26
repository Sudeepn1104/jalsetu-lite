"""
Endpoints implemented (see docs/api_contract.md for exact schemas):
    GET  /health
    POST /search
    POST /select
    POST /confirm
    GET  /status/{order_id}
    POST /operator/dispatch/{order_id}   <-- NOT in original contract, see note below
    POST /driver/arrive/{order_id}
    POST /driver/deliver/{order_id}
    GET  /operators/scores

NOTE ON SCOPE: /operator/dispatch is not in docs/api_contract.md. It was added
because operator.html needs a way to move CONFIRMED -> DISPATCHED and the
original contract had no such endpoint. Flag this to the team before Rachith
builds operator.html against it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import random
import sqlite3
import string
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "jalsetu.db"
SEED_PATH = BASE_DIR / "seed" / "operators.json"

# Hackathon secret — replace with an env var before any real deployment.
OTP_SECRET = b"team-diamonds-jalsetu-lite-hackathon-secret"

ALLOWED_CAPACITIES = {4000, 5000, 6000, 12000}
VOLUME_TOLERANCE = 0.95
MAX_OTP_ATTEMPTS = 3

VALID_TRANSITIONS: dict[str, set[str]] = {
    "OFFERED": {"CONFIRMED"},
    "CONFIRMED": {"DISPATCHED", "CANCELLED"},
    "DISPATCHED": {"ARRIVED"},
    "ARRIVED": {"DELIVERED", "DISPUTED"},
}

# --------------------------------------------------------------------------
# Seed data (operators + public tariff) — loaded once at startup
# --------------------------------------------------------------------------

with open(SEED_PATH, "r", encoding="utf-8") as f:
    SEED = json.load(f)

OPERATORS: dict[str, dict] = {op["id"]: op for op in SEED["operators"]}
TARIFF_PUBLIC: dict[int, int] = {int(k): v for k, v in SEED["tariff_public"].items()}


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------

def init_db() -> None:
    with get_conn() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id              TEXT PRIMARY KEY,
                capacity_l      INTEGER NOT NULL,
                water_type      TEXT NOT NULL,
                operator_id     TEXT NOT NULL,
                price           INTEGER NOT NULL,
                status          TEXT NOT NULL,
                otp_hash        TEXT,
                otp_attempts    INTEGER NOT NULL DEFAULT 3,
                backup_phone    TEXT,
                meter_before    INTEGER,
                meter_after     INTEGER,
                litres_delivered INTEGER,
                lat             REAL NOT NULL,
                lng             REAL NOT NULL,
                created_at      TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS order_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id    TEXT NOT NULL,
                from_status TEXT,
                to_status   TEXT NOT NULL,
                at          TEXT NOT NULL
            )
            """
        )


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def log_event(conn: sqlite3.Connection, order_id: str, from_status: Optional[str], to_status: str) -> None:
    conn.execute(
        "INSERT INTO order_events (order_id, from_status, to_status, at) VALUES (?, ?, ?, ?)",
        (order_id, from_status, to_status, now_iso()),
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_order_or_404(conn: sqlite3.Connection, order_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "ORDER_NOT_FOUND", "message": f"No order {order_id}"})
    return row


def transition(conn: sqlite3.Connection, order_id: str, current: str, new: str, extra_set: str = "", extra_params: tuple = ()) -> bool:
    """Atomic conditional transition. Returns False if the row wasn't in `current` state
    (caught a race or a stale client request) so callers can respond with 409."""
    if new not in VALID_TRANSITIONS.get(current, set()):
        raise HTTPException(
            status_code=409,
            detail={"error": "INVALID_TRANSITION", "message": f"Cannot go {current} -> {new}"},
        )
    cur = conn.execute(
        f"UPDATE orders SET status = ? {extra_set} WHERE id = ? AND status = ?",
        (new, *extra_params, order_id, current),
    )
    if cur.rowcount == 1:
        log_event(conn, order_id, current, new)
        return True
    return False


# --------------------------------------------------------------------------
# Helpers: pricing, distance, OTP, ids
# --------------------------------------------------------------------------

def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def price_for(operator: dict, capacity_l: int) -> int:
    if operator["type"] == "public":
        return TARIFF_PUBLIC[capacity_l]
    return round(operator["rate_per_1000l"] * capacity_l / 1000)


def is_flagged(operator: dict, water_type: str, price: int, capacity_l: int) -> bool:
    if operator["type"] != "private" or water_type != "fresh":
        return False
    return price > TARIFF_PUBLIC[capacity_l]


def hash_otp(otp: str, order_id: str) -> str:
    """HMAC-SHA256, salted with the order id so two orders never share a hash."""
    msg = f"{order_id}:{otp}".encode()
    return hmac.new(OTP_SECRET, msg, hashlib.sha256).hexdigest()


def verify_otp(otp: str, order_id: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_otp(otp, order_id), stored_hash)


def generate_otp() -> str:
    return "".join(random.choices(string.digits, k=4))


def generate_order_id(conn: sqlite3.Connection) -> str:
    for _ in range(10):
        candidate = f"JS-{random.randint(1000, 9999)}"
        exists = conn.execute("SELECT 1 FROM orders WHERE id = ?", (candidate,)).fetchone()
        if not exists:
            return candidate
    raise HTTPException(status_code=500, detail={"error": "ID_GEN_FAILED", "message": "Could not allocate order id"})


# --------------------------------------------------------------------------
# Pydantic schemas (request bodies — mirrors docs/api_contract.md exactly)
# --------------------------------------------------------------------------

WaterType = Literal["fresh", "treated"]


class SearchRequest(BaseModel):
    capacity_l: int
    water_type: WaterType
    lat: float
    lng: float

    @field_validator("capacity_l")
    @classmethod
    def check_capacity(cls, v: int) -> int:
        if v not in ALLOWED_CAPACITIES:
            raise ValueError(f"capacity_l must be one of {sorted(ALLOWED_CAPACITIES)}")
        return v

    @field_validator("lat")
    @classmethod
    def check_lat(cls, v: float) -> float:
        if not -90 <= v <= 90:
            raise ValueError("lat out of range")
        return v

    @field_validator("lng")
    @classmethod
    def check_lng(cls, v: float) -> float:
        if not -180 <= v <= 180:
            raise ValueError("lng out of range")
        return v


class SelectRequest(BaseModel):
    capacity_l: int
    water_type: WaterType
    operator_id: str
    lat: float
    lng: float


class ConfirmRequest(BaseModel):
    order_id: str
    backup_phone: Optional[str] = Field(default=None, max_length=15)


class DeliverRequest(BaseModel):
    otp: str = Field(min_length=4, max_length=4, pattern=r"^\d{4}$")
    meter_before: int = Field(ge=0)
    meter_after: int = Field(ge=0)


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------

app = FastAPI(title="JalSetu Lite API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    # Normalises every raised HTTPException to the {"error", "message"} shape
    # from the contract, whether `detail` is a plain string or already a dict.
    detail = exc.detail
    if isinstance(detail, dict):
        body = detail
    else:
        body = {"error": "ERROR", "message": str(detail)}
    return JSONResponse(status_code=exc.status_code, content=body)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Pydantic validation errors (bad capacity_l, out-of-range lat/lng, etc.)
    # normalised to the same {"error", "message"} shape as everything else.
    first = exc.errors()[0]
    field = ".".join(str(p) for p in first["loc"] if p != "body")
    return JSONResponse(
        status_code=422,
        content={"error": "VALIDATION_ERROR", "message": f"{field}: {first['msg']}"},
    )


@app.get("/health")
def health():
    return {"status": "ok"}


# --------------------------------------------------------------------------
# 1. POST /search
# --------------------------------------------------------------------------

@app.post("/search")
def search(req: SearchRequest):
    offers = []
    for op in OPERATORS.values():
        if op["water_type"] != req.water_type:
            continue
        price = price_for(op, req.capacity_l)
        distance_km = round(haversine_km(req.lat, req.lng, op["lat"], op["lng"]), 1)
        eta_min = round(10 + distance_km * 5)
        offers.append(
            {
                "operator_id": op["id"],
                "name": op["name"],
                "type": op["type"],
                "price": price,
                "price_per_1000l": round(price / req.capacity_l * 1000),
                "eta_min": eta_min,
                "distance_km": distance_km,
                "rating": op["rating"],
                "flagged": is_flagged(op, req.water_type, price, req.capacity_l),
            }
        )
    offers.sort(key=lambda o: o["distance_km"])
    return {"offers": offers}


# --------------------------------------------------------------------------
# 2. POST /select
# --------------------------------------------------------------------------

@app.post("/select")
def select(req: SelectRequest):
    if req.capacity_l not in ALLOWED_CAPACITIES:
        raise HTTPException(400, {"error": "BAD_CAPACITY", "message": "Invalid capacity_l"})
    op = OPERATORS.get(req.operator_id)
    if op is None:
        raise HTTPException(404, {"error": "OPERATOR_NOT_FOUND", "message": req.operator_id})
    if op["water_type"] != req.water_type:
        raise HTTPException(400, {"error": "WATER_TYPE_MISMATCH", "message": "Operator does not supply this water type"})

    price = price_for(op, req.capacity_l)
    with get_conn() as conn:
        order_id = generate_order_id(conn)
        conn.execute(
            """
            INSERT INTO orders (id, capacity_l, water_type, operator_id, price, status,
                                 otp_attempts, lat, lng, created_at)
            VALUES (?, ?, ?, ?, ?, 'OFFERED', ?, ?, ?, ?)
            """,
            (order_id, req.capacity_l, req.water_type, req.operator_id, price,
             MAX_OTP_ATTEMPTS, req.lat, req.lng, now_iso()),
        )
        log_event(conn, order_id, None, "OFFERED")
    return {"order_id": order_id, "status": "OFFERED"}


# --------------------------------------------------------------------------
# 3. POST /confirm
# --------------------------------------------------------------------------

@app.post("/confirm")
def confirm(req: ConfirmRequest):
    otp = generate_otp()
    with get_conn() as conn:
        row = get_order_or_404(conn, req.order_id)
        otp_hash = hash_otp(otp, req.order_id)
        ok = transition(
            conn, req.order_id, row["status"], "CONFIRMED",
            extra_set=", otp_hash = ?, backup_phone = ?",
            extra_params=(otp_hash, req.backup_phone),
        )
        if not ok:
            raise HTTPException(409, {"error": "INVALID_TRANSITION", "message": f"Order is {row['status']}, not OFFERED"})
    return {"order_id": req.order_id, "status": "CONFIRMED", "otp": otp, "backup_phone": req.backup_phone}


# --------------------------------------------------------------------------
# 4. GET /status/{order_id}
# --------------------------------------------------------------------------

@app.get("/status/{order_id}")
def status(order_id: str):
    with get_conn() as conn:
        row = get_order_or_404(conn, order_id)
    return {
        "order_id": row["id"],
        "status": row["status"],
        "operator_id": row["operator_id"],
        "capacity_l": row["capacity_l"],
        "price": row["price"],
    }


# --------------------------------------------------------------------------
# Operator dispatch — ADDED, not in original api_contract.md.
# Needed so operator.html has a way to move CONFIRMED -> DISPATCHED.
# --------------------------------------------------------------------------

@app.post("/operator/dispatch/{order_id}")
def operator_dispatch(order_id: str):
    with get_conn() as conn:
        row = get_order_or_404(conn, order_id)
        ok = transition(conn, order_id, row["status"], "DISPATCHED")
        if not ok:
            raise HTTPException(409, {"error": "INVALID_TRANSITION", "message": f"Order is {row['status']}, not CONFIRMED"})
    return {"status": "DISPATCHED"}


# --------------------------------------------------------------------------
# 5. POST /driver/arrive/{order_id}
# --------------------------------------------------------------------------

@app.post("/driver/arrive/{order_id}")
def driver_arrive(order_id: str):
    with get_conn() as conn:
        row = get_order_or_404(conn, order_id)
        ok = transition(conn, order_id, row["status"], "ARRIVED")
        if not ok:
            raise HTTPException(409, {"error": "INVALID_TRANSITION", "message": f"Order is {row['status']}, not DISPATCHED"})
    return {"status": "ARRIVED"}


# --------------------------------------------------------------------------
# 6. POST /driver/deliver/{order_id}
# --------------------------------------------------------------------------

@app.post("/driver/deliver/{order_id}")
def driver_deliver(order_id: str, req: DeliverRequest):
    with get_conn() as conn:
        row = get_order_or_404(conn, order_id)

        if row["status"] != "ARRIVED":
            raise HTTPException(409, {"error": "INVALID_TRANSITION", "message": f"Order is {row['status']}, not ARRIVED"})

        # --- OTP check (constant-time compare, server-tracked attempts) ---
        if not verify_otp(req.otp, order_id, row["otp_hash"]):
            attempts_left = row["otp_attempts"] - 1
            conn.execute("UPDATE orders SET otp_attempts = ? WHERE id = ?", (attempts_left, order_id))
            if attempts_left <= 0:
                transition(conn, order_id, "ARRIVED", "DISPUTED")
                return JSONResponse(
                    status_code=409,
                    content={"error": "OTP_LOCKED", "status": "DISPUTED"},
                )
            return JSONResponse(
                status_code=422,
                content={"error": "WRONG_OTP", "attempts_left": attempts_left},
            )

        # --- Volume check ---
        litres = req.meter_after - req.meter_before
        if litres < VOLUME_TOLERANCE * row["capacity_l"]:
            conn.execute(
                "UPDATE orders SET meter_before = ?, meter_after = ?, litres_delivered = ? WHERE id = ?",
                (req.meter_before, req.meter_after, litres, order_id),
            )
            transition(conn, order_id, "ARRIVED", "DISPUTED")
            return JSONResponse(
                status_code=409,
                content={"error": "VOLUME_MISMATCH", "status": "DISPUTED", "litres_delivered": litres},
            )

        conn.execute(
            "UPDATE orders SET meter_before = ?, meter_after = ?, litres_delivered = ? WHERE id = ?",
            (req.meter_before, req.meter_after, litres, order_id),
        )
        ok = transition(conn, order_id, "ARRIVED", "DELIVERED")
        if not ok:
            raise HTTPException(409, {"error": "INVALID_TRANSITION", "message": "Order already processed"})

    return {"status": "DELIVERED", "litres_delivered": litres}


# --------------------------------------------------------------------------
# 7. GET /operators/scores
# --------------------------------------------------------------------------

@app.get("/operators/scores")
def operator_scores():
    with get_conn() as conn:
        dispute_rows = conn.execute(
            "SELECT operator_id, COUNT(*) AS n FROM orders WHERE status = 'DISPUTED' GROUP BY operator_id"
        ).fetchall()
    disputes_by_op = {r["operator_id"]: r["n"] for r in dispute_rows}

    return {
        "operators": [
            {
                "operator_id": op["id"],
                "rating": op["rating"],
                "on_time_pct": op["on_time_pct"],
                "deliveries": op["deliveries"],
                "disputes": disputes_by_op.get(op["id"], 0),
            }
            for op in OPERATORS.values()
        ]
    }

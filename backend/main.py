"""JalSetu Lite API. Run with: uvicorn main:app --reload --port 8000"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

app = FastAPI(title="JalSetu Lite API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SEED_PATH = Path(__file__).resolve().parent / "seed" / "operators.json"
if not SEED_PATH.exists():
    SEED_PATH = Path(__file__).resolve().parent.parent / "seed" / "operators.json"
with SEED_PATH.open(encoding="utf-8") as seed_file:
    SEED_DATA = json.load(seed_file)

TARIFF_PUBLIC = {int(size): price for size, price in SEED_DATA["tariff_public"].items()}
OPERATORS = {operator["id"]: operator for operator in SEED_DATA["operators"]}
OTP_SECRET = os.environ.get("JALSETU_OTP_SECRET", "jalsethu-demo-secret-change-before-deploy").encode()
DB_PATH = Path(__file__).resolve().parent / "jalsetu.db"


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchRequest(APIModel):
    capacity_l: int = Field(gt=0)
    water_type: Literal["fresh", "treated"]
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class Offer(APIModel):
    operator_id: str
    name: str
    type: Literal["public", "private"]
    price: int
    price_per_1000l: int
    eta_min: int
    distance_km: float
    rating: float
    flagged: bool


class SearchResponse(APIModel):
    offers: list[Offer]


class SelectRequest(SearchRequest):
    operator_id: str


class SelectResponse(APIModel):
    order_id: str
    status: Literal["OFFERED"] = "OFFERED"


class ConfirmRequest(APIModel):
    order_id: str
    backup_phone: str | None = None


class ConfirmResponse(APIModel):
    order_id: str
    status: Literal["CONFIRMED"] = "CONFIRMED"
    otp: str
    backup_phone: str | None = None


class StatusResponse(APIModel):
    order_id: str
    status: Literal[
        "SEARCHING",
        "OFFERED",
        "CONFIRMED",
        "DISPATCHED",
        "ARRIVED",
        "DELIVERED",
        "DISPUTED",
        "CANCELLED",
    ]
    operator_id: str
    capacity_l: int
    price: int


class DeliveryRequest(APIModel):
    otp: str = Field(pattern=r"^\d{4}$")
    meter_before: int = Field(ge=0)
    meter_after: int = Field(ge=0)


@contextmanager
def get_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                order_number INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT UNIQUE,
                status TEXT NOT NULL,
                operator_id TEXT NOT NULL,
                capacity_l INTEGER NOT NULL,
                water_type TEXT NOT NULL,
                price INTEGER NOT NULL,
                otp_hash TEXT,
                otp_attempts INTEGER NOT NULL DEFAULT 0,
                backup_phone TEXT,
                meter_before INTEGER,
                meter_after INTEGER,
                litres_delivered INTEGER,
                lat REAL NOT NULL,
                lng REAL NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS order_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT NOT NULL,
                at TEXT NOT NULL
            )
            """
        )
    
    # Insert helper to set timestamps
    def now_iso():
        return datetime.now(timezone.utc).isoformat()


init_db()


def fetch_order(order_id: str):
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
    return dict(row) if row else None


def update_order(order_id: str, **fields) -> bool:
    assignments = ", ".join(f"{field} = ?" for field in fields)
    values = [*fields.values(), order_id]
    with get_connection() as connection:
        cursor = connection.execute(f"UPDATE orders SET {assignments} WHERE order_id = ?", values)
    return cursor.rowcount == 1


def api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code, {"error": code, "message": message})


@app.exception_handler(HTTPException)
async def handle_http_error(request, exc: HTTPException):
    detail = exc.detail
    body = detail if isinstance(detail, dict) and "error" in detail else {
        "error": "HTTP_ERROR",
        "message": str(detail),
    }
    return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"error": "VALIDATION_ERROR", "message": "Request does not match the API contract"},
    )


def require_capacity(capacity_l: int) -> None:
    if capacity_l not in TARIFF_PUBLIC:
        raise api_error(422, "UNSUPPORTED_CAPACITY", "Capacity must be 4000, 5000, 6000, or 12000 litres")


def haversine_km(lat: float, lng: float, operator: dict) -> float:
    radius_km = 6371.0
    lat1, lat2 = math.radians(lat), math.radians(operator["lat"])
    lat_delta = lat2 - lat1
    lng_delta = math.radians(operator["lng"] - lng)
    value = math.sin(lat_delta / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(lng_delta / 2) ** 2
    return 2 * radius_km * math.asin(math.sqrt(value))


def make_offer(operator: dict, request: SearchRequest) -> Offer:
    distance = haversine_km(request.lat, request.lng, operator)
    if operator["type"] == "public":
        price = TARIFF_PUBLIC[request.capacity_l]
        price_per_1000l = round(price * 1000 / request.capacity_l)
    else:
        price_per_1000l = operator["rate_per_1000l"]
        price = round(price_per_1000l * request.capacity_l / 1000)
    benchmark = TARIFF_PUBLIC[request.capacity_l]
    flagged = (
        request.water_type == "fresh"
        and operator["type"] == "private"
        and price > benchmark
    )
    return Offer(
        operator_id=operator["id"],
        name=operator["name"],
        type=operator["type"],
        price=price,
        price_per_1000l=price_per_1000l,
        eta_min=max(1, math.ceil(distance * 12)),
        distance_km=round(distance, 1),
        rating=operator["rating"],
        flagged=flagged,
    )


def otp_digest(order_id: str, otp: str) -> str:
    return hmac.new(OTP_SECRET, f"{order_id}:{otp}".encode(), hashlib.sha256).hexdigest()


def filter_and_sort_operators(
    lat: float,
    lng: float,
    capacity_l: int,
    water_type: str,
    *,
    operators: list[dict] | None = None,
    tariff_public: dict[int, int] | None = None,
) -> list[Offer]:
    """Return offers filtered by *water_type*, enriched with pricing and a
    *flagged* indicator, and sorted ascending by *distance_km*.

    Pricing rules (from api_contract.md §1):
      - public  → ``price = tariff_public[capacity_l]``
      - private → ``price = round(rate_per_1000l * capacity_l / 1000)``

    Flagging rule:
      - ``flagged = True`` when *water_type* is **"fresh"**, the operator is
        **private**, and *price* exceeds the public benchmark tariff for that
        capacity.  Treated-water offers are **never** flagged.

    Args:
        lat: Delivery-point latitude in decimal degrees (−90 … 90).
        lng: Delivery-point longitude in decimal degrees (−180 … 180).
        capacity_l: Requested tanker capacity in litres (must be a key in
            *tariff_public*; typically 4000, 5000, 6000, or 12000).
        water_type: ``"fresh"`` or ``"treated"``.
        operators: Optional override for the operator list (defaults to the
            module-level ``SEED_DATA["operators"]``).  Useful in unit tests.
        tariff_public: Optional override for the tariff table (defaults to the
            module-level ``TARIFF_PUBLIC``).  Useful in unit tests.

    Returns:
        List of :class:`Offer` instances sorted by ``distance_km`` ascending.

    Raises:
        ValueError: If *capacity_l* is not present in *tariff_public*.
    """
    _operators = operators if operators is not None else SEED_DATA["operators"]
    _tariff = tariff_public if tariff_public is not None else TARIFF_PUBLIC

    if capacity_l not in _tariff:
        raise ValueError(
            f"capacity_l={capacity_l} is not in the public tariff table. "
            f"Supported values: {sorted(_tariff)}"
        )

    benchmark: int = _tariff[capacity_l]
    offers: list[Offer] = []

    for op in _operators:
        # ── filter: only serve matching water type ──────────────────────────
        if op["water_type"] != water_type:
            continue

        # ── distance (haversine) ────────────────────────────────────────────
        distance = haversine_km(lat, lng, op)

        # ── pricing ─────────────────────────────────────────────────────────
        if op["type"] == "public":
            price: int = _tariff[capacity_l]
            price_per_1000l: int = round(price * 1000 / capacity_l)
        else:
            price_per_1000l = op["rate_per_1000l"]
            price = round(price_per_1000l * capacity_l / 1000)

        # ── flagging: private fresh water above public benchmark ─────────────
        flagged: bool = (
            water_type == "fresh"
            and op["type"] == "private"
            and price > benchmark
        )

        offers.append(
            Offer(
                operator_id=op["id"],
                name=op["name"],
                type=op["type"],
                price=price,
                price_per_1000l=price_per_1000l,
                eta_min=max(1, math.ceil(distance * 12)),
                distance_km=round(distance, 1),
                rating=op["rating"],
                flagged=flagged,
            )
        )

    # ── sort ascending by distance ──────────────────────────────────────────
    offers.sort(key=lambda o: o.distance_km)
    return offers


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/search", response_model=SearchResponse)
def search(request: SearchRequest):
    require_capacity(request.capacity_l)
    offers = filter_and_sort_operators(
        lat=request.lat,
        lng=request.lng,
        capacity_l=request.capacity_l,
        water_type=request.water_type,
    )
    return SearchResponse(offers=offers)


@app.post("/select", response_model=SelectResponse)
def select(request: SelectRequest):
    require_capacity(request.capacity_l)
    operator = OPERATORS.get(request.operator_id)
    if operator is None or operator["water_type"] != request.water_type:
        raise api_error(404, "OPERATOR_NOT_FOUND", "No matching operator serves this water type")
    offer = make_offer(operator, request)
    with get_connection() as connection:
        cursor = connection.execute(
            "INSERT INTO orders (status, operator_id, capacity_l, price, water_type, lat, lng, created_at) VALUES ('OFFERED', ?, ?, ?, ?, ?, ?, ?)",
            (
                operator["id"],
                request.capacity_l,
                offer.price,
                request.water_type,
                request.lat,
                request.lng,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        order_id = f"JS-{cursor.lastrowid + 1041}"
        connection.execute(
            "UPDATE orders SET order_id = ? WHERE order_number = ?",
            (order_id, cursor.lastrowid),
        )
    return SelectResponse(order_id=order_id, status="OFFERED")


@app.post("/confirm", response_model=ConfirmResponse)
def confirm(request: ConfirmRequest):
    order = fetch_order(request.order_id)
    if order is None:
        raise api_error(404, "ORDER_NOT_FOUND", "Order was not found")
    if order["status"] != "OFFERED":
        raise api_error(409, "INVALID_STATE", "Only offered orders can be confirmed")
    otp = f"{secrets.randbelow(10000):04d}"
    update_order(
        request.order_id,
        status="CONFIRMED",
        otp_hash=otp_digest(order["order_id"], otp),
        backup_phone=request.backup_phone,
    )
    return ConfirmResponse(
        order_id=order["order_id"],
        status="CONFIRMED",
        otp=otp,
        backup_phone=request.backup_phone,
    )


@app.get("/status/{order_id}", response_model=StatusResponse)
def get_status(order_id: str):
    order = fetch_order(order_id)
    if order is None:
        raise api_error(404, "ORDER_NOT_FOUND", "Order was not found")
    return StatusResponse(**{key: order[key] for key in ("order_id", "status", "operator_id", "capacity_l", "price")})


@app.get("/status", response_model=StatusResponse, include_in_schema=False)
def get_status_query(order_id: str):
    return get_status(order_id)


@app.post("/driver/arrive/{order_id}")
def driver_arrive(order_id: str):
    order = fetch_order(order_id)
    if order is None:
        raise api_error(404, "ORDER_NOT_FOUND", "Order was not found")
    if order["status"] != "DISPATCHED":
        raise api_error(409, "INVALID_STATE", "Order must be DISPATCHED before arrival")
    update_order(order_id, status="ARRIVED")
    return {"status": "ARRIVED"}


@app.post("/driver/deliver/{order_id}")
def driver_deliver(order_id: str, request: DeliveryRequest):
    order = fetch_order(order_id)
    if order is None:
        raise api_error(404, "ORDER_NOT_FOUND", "Order was not found")
    if order["status"] != "ARRIVED":
        raise api_error(409, "INVALID_STATE", "Order must be ARRIVED before delivery")
    if not hmac.compare_digest(otp_digest(order_id, request.otp), order["otp_hash"]):
        attempts = order["otp_attempts"] + 1
        attempts_left = 3 - attempts
        if attempts_left <= 0:
            update_order(order_id, otp_attempts=attempts, status="DISPUTED")
            raise HTTPException(409, {"error": "OTP_LOCKED", "status": "DISPUTED"})
        update_order(order_id, otp_attempts=attempts)
        raise HTTPException(422, {"error": "WRONG_OTP", "attempts_left": attempts_left})
    litres_delivered = request.meter_after - request.meter_before
    if litres_delivered < 0.95 * order["capacity_l"]:
        update_order(order_id, status="DISPUTED")
        raise HTTPException(
            409,
            {"error": "VOLUME_MISMATCH", "status": "DISPUTED", "litres_delivered": litres_delivered},
        )
    update_order(order_id, status="DELIVERED")
    return {"status": "DELIVERED", "litres_delivered": litres_delivered}


@app.get("/operators/scores")
def operator_scores():
    return {
        "operators": [
            {
                "operator_id": operator["id"],
                "rating": operator["rating"],
                "on_time_pct": operator["on_time_pct"],
                "deliveries": operator["deliveries"],
                "disputes": 0,
            }
            for operator in SEED_DATA["operators"]
        ]
    }


@app.post("/operator/dispatch/{order_id}")
def operator_dispatch(order_id: str):
    order = fetch_order(order_id)
    if order is None:
        raise api_error(404, "ORDER_NOT_FOUND", "Order was not found")
    if order["status"] != "CONFIRMED":
        raise api_error(409, "INVALID_STATE", "Only confirmed orders can be dispatched")
    update_order(order_id, status="DISPATCHED")
    return {"order_id": order_id, "status": "DISPATCHED"}


@app.post("/dev/dispatch/{order_id}")
def dev_dispatch(order_id: str):
    return operator_dispatch(order_id)

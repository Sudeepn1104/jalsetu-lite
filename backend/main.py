"""JalSetu Lite API. Run with: uvicorn main:app --reload --port 8000"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import math
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import FastAPI, Header, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field
import qrcode
import qrcode.image.svg
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="JalSetu Lite API")
cors_origins = [
    origin.strip()
    for origin in os.environ.get("JALSETHU_CORS_ORIGINS", "*").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
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
DB_PATH = Path(os.environ.get("JALSETHU_DB_PATH", Path(__file__).resolve().parent / "jalsetu.db"))
FRONTEND_PATH = Path(__file__).resolve().parent.parent / "frontend"
SESSION_SECONDS = int(os.environ.get("JALSETHU_SESSION_SECONDS", "43200"))
PASSWORD_ITERATIONS = 310000


def validate_production_settings(environment: dict[str, str]) -> None:
    otp_secret = environment.get("JALSETU_OTP_SECRET", "")
    admin_token = environment.get("JALSETHU_ADMIN_TOKEN", "")
    if len(otp_secret.encode()) < 32 or otp_secret == "jalsethu-demo-secret-change-before-deploy":
        raise RuntimeError("JALSETU_OTP_SECRET must be a unique secret of at least 32 bytes")
    if len(admin_token.encode()) < 32:
        raise RuntimeError("JALSETHU_ADMIN_TOKEN must be a unique secret of at least 32 bytes")
    if hmac.compare_digest(otp_secret.encode(), admin_token.encode()):
        raise RuntimeError("JALSETU_OTP_SECRET and JALSETHU_ADMIN_TOKEN must be different")

    origins_value = environment.get("JALSETHU_CORS_ORIGINS", "")
    origins = [origin.strip() for origin in origins_value.split(",") if origin.strip()]
    if not origins or "*" in origins:
        raise RuntimeError("Set JALSETHU_CORS_ORIGINS to one or more explicit origins; wildcards are not allowed")
    for origin in origins:
        try:
            parsed = urlsplit(origin)
            parsed.port
        except ValueError as exc:
            raise RuntimeError(f"Invalid CORS origin {origin!r}") from exc
        is_local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or (parsed.scheme != "https" and not is_local_http)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError(f"Invalid CORS origin {origin!r}; use an HTTPS origin (HTTP is allowed for localhost)")


if os.environ.get("JALSETHU_ENV", "development").lower() == "production":
    validate_production_settings(os.environ)


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


class CitizenRegisterRequest(APIModel):
    name: str = Field(min_length=2, max_length=100)
    email: str = Field(min_length=5, max_length=254)
    phone: str | None = Field(default=None, max_length=24)
    password: str = Field(min_length=10, max_length=128)


class LoginRequest(APIModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=1, max_length=128)
    role: Literal["citizen", "operator", "driver"]


class ProvisionUserRequest(APIModel):
    name: str = Field(min_length=2, max_length=100)
    email: str = Field(min_length=5, max_length=254)
    phone: str | None = Field(default=None, max_length=24)
    password: str = Field(min_length=10, max_length=128)
    role: Literal["operator", "driver"]
    operator_id: str


class AdminPasswordResetRequest(APIModel):
    password: str = Field(min_length=10, max_length=128)


@contextmanager
def get_connection():
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def migrate_order_event_integrity(connection: sqlite3.Connection) -> None:
    foreign_keys = connection.execute("PRAGMA foreign_key_list(order_events)").fetchall()
    has_expected_foreign_key = any(
        row["table"] == "orders"
        and row["from"] == "order_id"
        and row["to"] == "order_id"
        and row["on_delete"].upper() == "CASCADE"
        for row in foreign_keys
    )
    if has_expected_foreign_key:
        connection.execute("CREATE INDEX IF NOT EXISTS idx_order_events_order ON order_events(order_id)")
        return

    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS order_event_orphans (
                original_event_id INTEGER PRIMARY KEY,
                order_id TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT NOT NULL,
                at TEXT NOT NULL,
                quarantined_at TEXT NOT NULL,
                reason TEXT NOT NULL
            )
            """
        )
        quarantined_at = datetime.now(timezone.utc).isoformat()
        connection.execute(
            """
            INSERT OR IGNORE INTO order_event_orphans (
                original_event_id, order_id, from_status, to_status, at, quarantined_at, reason
            )
            SELECT events.id, events.order_id, events.from_status, events.to_status, events.at, ?,
                   'No matching order existed when the event-log foreign key was added'
            FROM order_events AS events
            WHERE NOT EXISTS (
                SELECT 1 FROM orders WHERE orders.order_id = events.order_id
            )
            """,
            (quarantined_at,),
        )
        connection.execute(
            """
            CREATE TABLE order_events_migrated (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
                from_status TEXT,
                to_status TEXT NOT NULL,
                at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO order_events_migrated (id, order_id, from_status, to_status, at)
            SELECT events.id, events.order_id, events.from_status, events.to_status, events.at
            FROM order_events AS events
            WHERE EXISTS (
                SELECT 1 FROM orders WHERE orders.order_id = events.order_id
            )
            ORDER BY events.id
            """
        )
        connection.execute("DROP TABLE order_events")
        connection.execute("ALTER TABLE order_events_migrated RENAME TO order_events")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_order_events_order ON order_events(order_id)")
        violations = connection.execute("PRAGMA foreign_key_check(order_events)").fetchall()
        if violations:
            raise sqlite3.IntegrityError("Order event foreign-key migration left invalid references")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        existing_orders = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'orders'"
        ).fetchone()
        if existing_orders:
            order_columns = {row["name"] for row in connection.execute("PRAGMA table_info(orders)")}
            if "order_number" not in order_columns and "id" in order_columns:
                legacy_orders = [dict(row) for row in connection.execute("SELECT * FROM orders")]
                connection.execute("DROP TABLE orders")
                existing_orders = None
            else:
                legacy_orders = []
        else:
            legacy_orders = []
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
        for column, definition in (
            ("order_id", "TEXT"),
            ("water_type", "TEXT NOT NULL DEFAULT 'fresh'"),
            ("otp_hash", "TEXT"),
            ("otp_attempts", "INTEGER NOT NULL DEFAULT 0"),
            ("backup_phone", "TEXT"),
            ("meter_before", "INTEGER"),
            ("meter_after", "INTEGER"),
            ("litres_delivered", "INTEGER"),
            ("citizen_user_id", "INTEGER"),
            ("operator_user_id", "INTEGER"),
            ("driver_user_id", "INTEGER"),
        ):
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(orders)")}
            if column not in columns:
                connection.execute(f"ALTER TABLE orders ADD COLUMN {column} {definition}")
        if not legacy_orders:
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(orders)")}
            if "order_number" in columns and "order_id" in columns:
                connection.execute(
                    "UPDATE orders SET order_id = 'JS-' || CAST(order_number + 1041 AS TEXT) WHERE order_id IS NULL"
                )
        if legacy_orders:
            for legacy in legacy_orders:
                order_id = legacy.get("order_id") or legacy.get("id")
                if not order_id:
                    continue
                connection.execute(
                    """
                    INSERT OR IGNORE INTO orders (
                        order_id, status, operator_id, capacity_l, water_type, price,
                        otp_hash, otp_attempts, backup_phone, meter_before, meter_after,
                        litres_delivered, lat, lng, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order_id,
                        legacy.get("status", "OFFERED"),
                        legacy.get("operator_id", ""),
                        legacy.get("capacity_l", 0),
                        legacy.get("water_type", "fresh"),
                        legacy.get("price", 0),
                        legacy.get("otp_hash"),
                        legacy.get("otp_attempts", 0),
                        legacy.get("backup_phone"),
                        legacy.get("meter_before"),
                        legacy.get("meter_after"),
                        legacy.get("litres_delivered"),
                        legacy.get("lat", 0),
                        legacy.get("lng", 0),
                        legacy.get("created_at") or datetime.now(timezone.utc).isoformat(),
                    ),
                )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                display_name TEXT NOT NULL,
                phone TEXT,
                role TEXT NOT NULL CHECK (role IN ('citizen', 'operator', 'driver')),
                operator_id TEXT,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_orders_citizen ON orders(citizen_user_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_orders_operator ON orders(operator_user_id)")
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
    with get_connection() as connection:
        migrate_order_event_integrity(connection)


init_db()


def fetch_order(order_id: str):
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
    return dict(row) if row else None


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        algorithm, iterations_text, salt_hex, digest_hex = encoded_hash.split("$", 3)
        iterations = int(iterations_text)
        if algorithm != "pbkdf2_sha256" or not 100000 <= iterations <= 1000000:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), iterations)
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def normalized_email(email: str) -> str:
    normalized = email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
        raise api_error(422, "INVALID_EMAIL", "Enter a valid email address")
    return normalized


def public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["display_name"],
        "role": user["role"],
        "operator_id": user["operator_id"],
        "created_at": user["created_at"],
    }


def issue_session(user: dict) -> dict:
    token = secrets.token_urlsafe(32)
    created_at = datetime.now(timezone.utc)
    expires_at = created_at + timedelta(seconds=SESSION_SECONDS)
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO auth_sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (
                hashlib.sha256(token.encode()).hexdigest(),
                user["id"],
                created_at.isoformat(),
                expires_at.isoformat(),
            ),
        )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": expires_at.isoformat(),
        "user": public_user(user),
    }


def authenticated_user(authorization: str | None) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise api_error(401, "AUTH_REQUIRED", "Sign in to continue")
    token = authorization[7:].strip()
    if not token:
        raise api_error(401, "AUTH_REQUIRED", "Sign in to continue")
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT users.* FROM auth_sessions
            JOIN users ON users.id = auth_sessions.user_id
            WHERE auth_sessions.token_hash = ?
              AND auth_sessions.revoked_at IS NULL
              AND auth_sessions.expires_at > ?
            """,
            (token_hash, now),
        ).fetchone()
    if row is None:
        raise api_error(401, "SESSION_EXPIRED", "Your session expired. Sign in again")
    return dict(row)


def require_role(user: dict, *roles: str):
    if user["role"] not in roles:
        raise api_error(403, "ROLE_FORBIDDEN", "This account cannot perform that action")


def can_view_order(user: dict, order: dict) -> bool:
    if user["role"] == "citizen":
        return order.get("citizen_user_id") == user["id"]
    if user["role"] == "operator":
        return order["operator_id"] == user.get("operator_id")
    if user["role"] == "driver":
        return order["operator_id"] == user.get("operator_id")
    return False


def update_order(order_id: str, *, expected_status: str | None = None, **fields) -> bool:
    assignments = ", ".join(f"{field} = ?" for field in fields)
    values = [*fields.values(), order_id]
    where = "order_id = ?"
    if expected_status is not None:
        where += " AND status = ?"
        values.append(expected_status)
    with get_connection() as connection:
        previous = connection.execute("SELECT status FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        if previous is None:
            return False
        if expected_status is not None and previous["status"] != expected_status:
            return False
        cursor = connection.execute(f"UPDATE orders SET {assignments} WHERE {where}", values)
        if cursor.rowcount != 1:
            return False
        next_status = fields.get("status")
        if next_status and next_status != previous["status"]:
            connection.execute(
                "INSERT INTO order_events (order_id, from_status, to_status, at) VALUES (?, ?, ?, ?)",
                (order_id, previous["status"], next_status, datetime.now(timezone.utc).isoformat()),
            )
    return cursor.rowcount == 1


def record_wrong_otp(order_id: str) -> tuple[int, str] | None:
    """Atomically count a failed OTP and dispute the order on the third try."""
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT status, otp_attempts FROM orders WHERE order_id = ?", (order_id,)
        ).fetchone()
        if row is None or row["status"] != "ARRIVED":
            return None

        attempts = row["otp_attempts"] + 1
        next_status = "DISPUTED" if attempts >= 3 else "ARRIVED"
        cursor = connection.execute(
            "UPDATE orders SET otp_attempts = ?, status = ? "
            "WHERE order_id = ? AND status = 'ARRIVED' AND otp_attempts = ?",
            (attempts, next_status, order_id, row["otp_attempts"]),
        )
        if cursor.rowcount != 1:
            return None
        if next_status != row["status"]:
            connection.execute(
                "INSERT INTO order_events (order_id, from_status, to_status, at) VALUES (?, ?, ?, ?)",
                (order_id, row["status"], next_status, datetime.now(timezone.utc).isoformat()),
            )
    return attempts, next_status


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


def create_user(
    *,
    name: str,
    email: str,
    phone: str | None,
    password: str,
    role: str,
    operator_id: str | None,
) -> dict:
    if phone and not re.fullmatch(r"\+?[0-9 ()-]{7,24}", phone):
        raise api_error(422, "INVALID_PHONE", "Enter a valid phone number")
    normalized = normalized_email(email)
    created_at = datetime.now(timezone.utc).isoformat()
    try:
        with get_connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO users (email, display_name, phone, role, operator_id, password_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (normalized, name.strip(), phone, role, operator_id, hash_password(password), created_at),
            )
            row = connection.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
    except sqlite3.IntegrityError as exc:
        raise api_error(409, "ACCOUNT_EXISTS", "An account with that email already exists") from exc
    return dict(row)


@app.post("/auth/register")
def register_citizen(request: CitizenRegisterRequest):
    user = create_user(
        name=request.name,
        email=request.email,
        phone=request.phone,
        password=request.password,
        role="citizen",
        operator_id=None,
    )
    return issue_session(user)


@app.post("/auth/login")
def login(request: LoginRequest):
    email = normalized_email(request.email)
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email,)).fetchone()
    if row is None or row["role"] != request.role or not verify_password(request.password, row["password_hash"]):
        raise api_error(401, "INVALID_CREDENTIALS", "Email, password, or account type is incorrect")
    return issue_session(dict(row))


@app.post("/auth/admin/users")
def provision_role_user(
    request: ProvisionUserRequest,
    admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    require_admin_token(admin_token)
    if request.operator_id not in OPERATORS:
        raise api_error(422, "INVALID_OPERATOR", "Choose an operator ID from the configured operator list")
    user = create_user(
        name=request.name,
        email=request.email,
        phone=request.phone,
        password=request.password,
        role=request.role,
        operator_id=request.operator_id,
    )
    return {"user": public_user(user)}


def require_admin_token(admin_token: str | None) -> None:
    expected_token = os.environ.get("JALSETHU_ADMIN_TOKEN")
    if not expected_token:
        raise api_error(503, "PROVISIONING_DISABLED", "Set JALSETHU_ADMIN_TOKEN before managing staff accounts")
    if not admin_token or not hmac.compare_digest(admin_token, expected_token):
        raise api_error(401, "ADMIN_AUTH_REQUIRED", "A valid staff provisioning token is required")


@app.get("/auth/admin/users")
def list_role_users(admin_token: str | None = Header(default=None, alias="X-Admin-Token")):
    require_admin_token(admin_token)
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT id, email, display_name, role, operator_id, created_at FROM users WHERE role IN ('operator', 'driver') ORDER BY role, display_name COLLATE NOCASE"
        ).fetchall()
    return {"users": [dict(row) for row in rows]}


@app.put("/auth/admin/users/{user_id}/password")
def reset_role_user_password(
    user_id: int,
    request: AdminPasswordResetRequest,
    admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    require_admin_token(admin_token)
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as connection:
        cursor = connection.execute(
            "UPDATE users SET password_hash = ? WHERE id = ? AND role IN ('operator', 'driver')",
            (hash_password(request.password), user_id),
        )
        if cursor.rowcount == 0:
            raise api_error(404, "STAFF_USER_NOT_FOUND", "Operator or driver account was not found")
        connection.execute(
            "UPDATE auth_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (now, user_id),
        )
    return {"status": "PASSWORD_UPDATED"}


@app.get("/auth/me")
def get_current_user(authorization: str | None = Header(default=None)):
    return {"user": public_user(authenticated_user(authorization))}


@app.post("/auth/logout")
def logout(authorization: str | None = Header(default=None)):
    authenticated_user(authorization)
    token_hash = hashlib.sha256(authorization[7:].strip().encode()).hexdigest()
    with get_connection() as connection:
        connection.execute(
            "UPDATE auth_sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
            (datetime.now(timezone.utc).isoformat(), token_hash),
        )
    return {"status": "SIGNED_OUT"}


@app.get("/auth/history")
def get_account_history(authorization: str | None = Header(default=None)):
    user = authenticated_user(authorization)
    with get_connection() as connection:
        if user["role"] == "citizen":
            rows = connection.execute(
                "SELECT * FROM orders WHERE citizen_user_id = ? ORDER BY created_at DESC LIMIT 100",
                (user["id"],),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM orders WHERE operator_id = ? ORDER BY created_at DESC LIMIT 100",
                (user["operator_id"],),
            ).fetchall()
        orders = []
        for row in rows:
            order = dict(row)
            order = {
                key: order[key]
                for key in (
                    "order_id",
                    "status",
                    "operator_id",
                    "capacity_l",
                    "water_type",
                    "price",
                    "created_at",
                    "meter_before",
                    "meter_after",
                    "litres_delivered",
                )
            }
            order["events"] = [
                dict(event)
                for event in connection.execute(
                    "SELECT from_status, to_status, at FROM order_events WHERE order_id = ? ORDER BY id",
                    (order["order_id"],),
                ).fetchall()
            ]
            orders.append(order)
    return {"orders": orders}


@app.get("/auth/active-orders")
def get_active_citizen_orders(authorization: str | None = Header(default=None)):
    user = authenticated_user(authorization)
    require_role(user, "citizen")
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT order_id, status, operator_id, capacity_l, water_type, price, created_at
            FROM orders
            WHERE citizen_user_id = ? AND status IN ('OFFERED', 'CONFIRMED', 'DISPATCHED', 'ARRIVED')
            ORDER BY created_at DESC, order_number DESC
            """,
            (user["id"],),
        ).fetchall()
    return {"orders": [dict(row) for row in rows]}


@app.get("/operator/orders")
def get_operator_orders(authorization: str | None = Header(default=None)):
    user = authenticated_user(authorization)
    require_role(user, "operator")
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT order_id, status, operator_id, capacity_l, water_type, price,
                   lat, lng, created_at
            FROM orders
            WHERE operator_id = ?
              AND status IN ('OFFERED', 'CONFIRMED', 'DISPATCHED', 'ARRIVED')
            ORDER BY created_at DESC, order_number DESC
            LIMIT 100
            """,
            (user["operator_id"],),
        ).fetchall()
    return {"orders": [dict(row) for row in rows]}


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


@app.get("/health/live")
def health_live():
    return {"status": "ok"}


@app.get("/health/ready")
@app.get("/health")
def health_ready():
    required_tables = {"orders", "users", "auth_sessions", "order_events"}
    if not DB_PATH.is_file():
        raise api_error(503, "DATABASE_UNAVAILABLE", "The service database is unavailable")
    try:
        with get_connection() as connection:
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
    except sqlite3.Error as exc:
        raise api_error(503, "DATABASE_UNAVAILABLE", "The service database is unavailable") from exc
    missing_tables = required_tables - tables
    if missing_tables:
        raise api_error(503, "DATABASE_NOT_READY", "The service database schema is incomplete")
    return {"status": "ok", "database": "ok"}


@app.post("/search", response_model=SearchResponse)
def search(request: SearchRequest, authorization: str | None = Header(default=None)):
    require_capacity(request.capacity_l)
    if authorization:
        user = authenticated_user(authorization)
        if user["role"] == "driver":
            raise api_error(403, "ROLE_FORBIDDEN", "Drivers cannot search for tanker offers")
    offers = filter_and_sort_operators(
        lat=request.lat,
        lng=request.lng,
        capacity_l=request.capacity_l,
        water_type=request.water_type,
    )
    if authorization and user["role"] == "operator":
        offers = [offer for offer in offers if offer.operator_id == user["operator_id"]]
    return SearchResponse(offers=offers)


@app.post("/select", response_model=SelectResponse)
def select(request: SelectRequest, authorization: str | None = Header(default=None)):
    user = authenticated_user(authorization)
    require_role(user, "citizen")
    require_capacity(request.capacity_l)
    operator = OPERATORS.get(request.operator_id)
    if operator is None or operator["water_type"] != request.water_type:
        raise api_error(404, "OPERATOR_NOT_FOUND", "No matching operator serves this water type")
    if user["role"] == "operator" and operator["id"] != user["operator_id"]:
        raise api_error(403, "OPERATOR_MISMATCH", "Operators can only create orders for their own service")
    offer = make_offer(operator, request)
    with get_connection() as connection:
        linked_operator = connection.execute(
            "SELECT id FROM users WHERE role = 'operator' AND operator_id = ? ORDER BY id LIMIT 1",
            (operator["id"],),
        ).fetchone()
        citizen_user_id = user["id"] if user["role"] == "citizen" else None
        operator_user_id = user["id"] if user["role"] == "operator" else (linked_operator["id"] if linked_operator else None)
        cursor = connection.execute(
            """
            INSERT INTO orders (
                status, operator_id, capacity_l, price, water_type, lat, lng, created_at,
                citizen_user_id, operator_user_id
            ) VALUES ('OFFERED', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                operator["id"],
                request.capacity_l,
                offer.price,
                request.water_type,
                request.lat,
                request.lng,
                datetime.now(timezone.utc).isoformat(),
                citizen_user_id,
                operator_user_id,
            ),
        )
        order_id = f"JS-{cursor.lastrowid + 1041}"
        while connection.execute("SELECT 1 FROM orders WHERE order_id = ?", (order_id,)).fetchone():
            order_id = f"JS-{int(order_id.split('-')[1]) + 1}"
        connection.execute(
            "UPDATE orders SET order_id = ? WHERE order_number = ?",
            (order_id, cursor.lastrowid),
        )
        connection.execute(
            "INSERT INTO order_events (order_id, from_status, to_status, at) VALUES (?, NULL, 'OFFERED', ?)",
            (order_id, datetime.now(timezone.utc).isoformat()),
        )
    return SelectResponse(order_id=order_id, status="OFFERED")


@app.post("/confirm", response_model=ConfirmResponse)
def confirm(request: ConfirmRequest, authorization: str | None = Header(default=None)):
    user = authenticated_user(authorization)
    require_role(user, "citizen")
    order = fetch_order(request.order_id)
    if order is None:
        raise api_error(404, "ORDER_NOT_FOUND", "Order was not found")
    if not can_view_order(user, order):
        raise api_error(403, "ORDER_FORBIDDEN", "This order is not assigned to your account")
    if order["status"] != "OFFERED":
        raise api_error(409, "INVALID_STATE", "Only offered orders can be confirmed")
    otp = f"{secrets.randbelow(10000):04d}"
    if not update_order(
        request.order_id,
        expected_status="OFFERED",
        status="CONFIRMED",
        otp_hash=otp_digest(order["order_id"], otp),
        backup_phone=request.backup_phone,
    ):
        raise api_error(409, "INVALID_STATE", "This order changed; refresh before confirming")
    return ConfirmResponse(
        order_id=order["order_id"],
        status="CONFIRMED",
        otp=otp,
        backup_phone=request.backup_phone,
    )


@app.get("/status/{order_id}", response_model=StatusResponse)
def get_status(order_id: str, authorization: str | None = Header(default=None)):
    user = authenticated_user(authorization)
    order = fetch_order(order_id)
    if order is None:
        raise api_error(404, "ORDER_NOT_FOUND", "Order was not found")
    if not can_view_order(user, order):
        raise api_error(403, "ORDER_FORBIDDEN", "This order is not assigned to your account")
    return StatusResponse(**{key: order[key] for key in ("order_id", "status", "operator_id", "capacity_l", "price")})


@app.post("/orders/{order_id}/cancel")
def cancel_order(order_id: str, authorization: str | None = Header(default=None)):
    user = authenticated_user(authorization)
    require_role(user, "citizen")
    order = fetch_order(order_id)
    if order is None:
        raise api_error(404, "ORDER_NOT_FOUND", "Order was not found")
    if not can_view_order(user, order):
        raise api_error(403, "ORDER_FORBIDDEN", "This order is not assigned to your account")
    if order["status"] not in {"OFFERED", "CONFIRMED"}:
        raise api_error(409, "INVALID_STATE", "Orders can only be cancelled before the operator dispatches")
    if not update_order(
        order_id,
        expected_status=order["status"],
        status="CANCELLED",
        otp_hash=None,
    ):
        raise api_error(409, "INVALID_STATE", "This order changed; refresh before cancelling")
    return {"order_id": order_id, "status": "CANCELLED"}


@app.post("/orders/{order_id}/delivery-code")
def regenerate_delivery_code(order_id: str, authorization: str | None = Header(default=None)):
    user = authenticated_user(authorization)
    require_role(user, "citizen")
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        order = connection.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        if order is None:
            raise api_error(404, "ORDER_NOT_FOUND", "Order was not found")
        order = dict(order)
        if not can_view_order(user, order):
            raise api_error(403, "ORDER_FORBIDDEN", "This order is not assigned to your account")
        if order["status"] == "DISPUTED" or order["otp_attempts"] >= 3:
            raise HTTPException(409, {"error": "OTP_LOCKED", "status": "DISPUTED"})
        if order["status"] != "ARRIVED":
            raise api_error(409, "INVALID_STATE", "A delivery code can only be recovered after the tanker arrives")
        otp = f"{secrets.randbelow(10000):04d}"
        connection.execute(
            "UPDATE orders SET otp_hash = ? WHERE order_id = ? AND status = 'ARRIVED' AND otp_attempts < 3",
            (otp_digest(order_id, otp), order_id),
        )
    return {"order_id": order_id, "status": "ARRIVED", "otp": otp}


@app.get("/upi-qr")
def get_upi_qr(order_id: str, authorization: str | None = Header(default=None)):
    user = authenticated_user(authorization)
    order = fetch_order(order_id)
    if order is None:
        raise api_error(404, "ORDER_NOT_FOUND", "Order was not found")
    if not can_view_order(user, order):
        raise api_error(403, "ORDER_FORBIDDEN", "This order is not assigned to your account")
    if order["status"] != "CONFIRMED":
        raise api_error(409, "INVALID_STATE", "Payment QR is available only for confirmed orders")
    payment_uri = build_upi_payment_uri(
        vpa="demo@invalid",
        payee_name=f"JalSetu Demo - {OPERATORS[order['operator_id']]['name']}",
        amount=order["price"],
        order_id=order_id,
    )
    return render_upi_qr(payment_uri)


def build_upi_payment_uri(*, vpa: str, payee_name: str, amount: int, order_id: str) -> str:
    """Create a fixed-amount UPI deep link for a demo-only citizen QR."""
    return "upi://pay?" + urlencode({
        "pa": vpa,
        "pn": payee_name,
        "tr": order_id,
        "am": f"{amount:.2f}",
        "cu": "INR",
        "tn": f"JalSetu order {order_id}",
    })


def render_upi_qr(payment_uri: str):
    image = qrcode.make(payment_uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=4)
    output = io.BytesIO()
    image.save(output)
    return Response(
        output.getvalue(),
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@app.get("/api/upi-qr")
def get_demo_upi_qr(uri: str):
    if len(uri) > 500:
        raise api_error(400, "INVALID_PAYMENT_URI", "The demo UPI URI is too long")
    payment = urlsplit(uri)
    parameters = parse_qs(payment.query)
    amount = parameters.get("am", [""])[0]
    if (
        payment.scheme != "upi"
        or payment.netloc != "pay"
        or parameters.get("pa") != ["demo@upi"]
        or parameters.get("cu") != ["INR"]
        or not re.fullmatch(r"\d+(\.\d{1,2})?", amount)
        or float(amount) <= 0
        or float(amount) > 10000000
    ):
        raise api_error(400, "INVALID_PAYMENT_URI", "Only valid demo UPI payment URIs can be rendered")
    return render_upi_qr(uri)


@app.get("/status", response_model=StatusResponse, include_in_schema=False)
def get_status_query(order_id: str, authorization: str | None = Header(default=None)):
    return get_status(order_id, authorization)


@app.post("/driver/arrive/{order_id}")
def driver_arrive(order_id: str, authorization: str | None = Header(default=None)):
    user = authenticated_user(authorization)
    require_role(user, "driver")
    order = fetch_order(order_id)
    if order is None:
        raise api_error(404, "ORDER_NOT_FOUND", "Order was not found")
    if not can_view_order(user, order) or (order.get("driver_user_id") not in (None, user["id"])):
        raise api_error(403, "ORDER_FORBIDDEN", "This order is not assigned to your account")
    if order["status"] != "DISPATCHED":
        raise api_error(409, "INVALID_STATE", "Order must be DISPATCHED before arrival")
    if not update_order(
        order_id,
        expected_status="DISPATCHED",
        status="ARRIVED",
        driver_user_id=user["id"],
    ):
        raise api_error(409, "INVALID_STATE", "This order changed; refresh before marking arrival")
    return {"status": "ARRIVED"}


@app.post("/driver/deliver/{order_id}")
def driver_deliver(order_id: str, request: DeliveryRequest, authorization: str | None = Header(default=None)):
    user = authenticated_user(authorization)
    require_role(user, "driver")
    order = fetch_order(order_id)
    if order is None:
        raise api_error(404, "ORDER_NOT_FOUND", "Order was not found")
    if not can_view_order(user, order) or (order.get("driver_user_id") not in (None, user["id"])):
        raise api_error(403, "ORDER_FORBIDDEN", "This order is not assigned to your account")
    if order["status"] != "ARRIVED":
        raise api_error(409, "INVALID_STATE", "Order must be ARRIVED before delivery")
    if not hmac.compare_digest(otp_digest(order_id, request.otp), order["otp_hash"]):
        failed_attempt = record_wrong_otp(order_id)
        if failed_attempt is None:
            raise api_error(409, "INVALID_STATE", "This order changed; refresh before retrying delivery")
        attempts, current_status = failed_attempt
        if current_status == "DISPUTED":
            raise HTTPException(409, {"error": "OTP_LOCKED", "status": "DISPUTED"})
        attempts_left = 3 - attempts
        raise HTTPException(422, {"error": "WRONG_OTP", "attempts_left": attempts_left})
    litres_delivered = request.meter_after - request.meter_before
    if litres_delivered < 0.95 * order["capacity_l"]:
        if not update_order(
            order_id,
            expected_status="ARRIVED",
            status="DISPUTED",
            meter_before=request.meter_before,
            meter_after=request.meter_after,
            litres_delivered=litres_delivered,
        ):
            raise api_error(409, "INVALID_STATE", "This order changed; refresh before retrying delivery")
        raise HTTPException(
            409,
            {"error": "VOLUME_MISMATCH", "status": "DISPUTED", "litres_delivered": litres_delivered},
        )
    if not update_order(
        order_id,
        expected_status="ARRIVED",
        status="DELIVERED",
        meter_before=request.meter_before,
        meter_after=request.meter_after,
        litres_delivered=litres_delivered,
    ):
        raise api_error(409, "INVALID_STATE", "This order changed; refresh before retrying delivery")
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


@app.get("/operators")
def list_operators():
    return {
        "operators": [
            {"operator_id": operator["id"], "name": operator["name"], "type": operator["type"]}
            for operator in SEED_DATA["operators"]
        ]
    }


@app.post("/operator/dispatch/{order_id}")
def operator_dispatch(order_id: str, authorization: str | None = Header(default=None)):
    user = authenticated_user(authorization)
    require_role(user, "operator")
    order = fetch_order(order_id)
    if order is None:
        raise api_error(404, "ORDER_NOT_FOUND", "Order was not found")
    if not can_view_order(user, order):
        raise api_error(403, "ORDER_FORBIDDEN", "This order is not assigned to your operator account")
    if order["status"] != "CONFIRMED":
        raise api_error(409, "INVALID_STATE", "Only confirmed orders can be dispatched")
    if not update_order(
        order_id,
        expected_status="CONFIRMED",
        status="DISPATCHED",
        operator_user_id=user["id"],
    ):
        raise api_error(409, "INVALID_STATE", "This order changed; refresh before dispatching")
    return {"order_id": order_id, "status": "DISPATCHED"}


@app.post("/dev/dispatch/{order_id}")
def dev_dispatch(order_id: str, authorization: str | None = Header(default=None)):
    return operator_dispatch(order_id, authorization)


if FRONTEND_PATH.is_dir():
    @app.get("/", include_in_schema=False)
    def frontend_home():
        return RedirectResponse("/index.html")

    app.mount("/", StaticFiles(directory=FRONTEND_PATH), name="frontend")

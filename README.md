```
# 🚰 JalSetu Lite

**Digital Public Infrastructure for Water Logistics**

> **Build for Billions Hackathon** — Track 3: Reinvent Digital Public Infrastructure for Billions  
> **Location:** NITK Surathkal  
> **Team:** Team Diamonds

---

## 📌 Problem statement

In rapidly growing urban and suburban regions, water tanker delivery is heavily fragmented. Citizens face extreme price volatility, surge pricing during water scarcity, and unreliable delivery verification. Private operators work in silos, while municipal authorities struggle to enforce baseline rate cards.

---

## 💡 The solution

**JalSetu Lite** is an open, Beckn-protocol-inspired Digital Public Infrastructure (DPI) network that connects citizens, public municipal water boards (such as BWSSB), and private tanker operators in a transparent marketplace.

### ✨ Key capabilities

1. **Transparent price benchmarking**  
  Private fresh-water quotes exceeding official municipal rates automatically display a **RED price warning tag**. BWSSB benchmark rates are ₹660/4 kL, ₹700/5 kL, ₹740/6 kL, and ₹1,290/12 kL.
2. **Delivery proof and meter check**  
  Deliveries require a server-side HMAC 4-digit OTP and a **95% meter volume validation check**: `((meter_after - meter_before) >= 0.95 * capacity_l)`.
3. **Automatic dispute lock**  
  Three incorrect OTP attempts or a volume deficit automatically lock the transaction in the `DISPUTED` state.
4. **Pre-dispatch cancellation**
  Citizens can cancel their own offered or confirmed order before dispatch. A conditional state transition prevents cancellation and dispatch from both succeeding at once, and the change remains in the order history.
5. **Operator-directed UPI payment request**
  For a confirmed order, the API generates a fixed-amount UPI deep-link QR addressed to that operator and tagged with the order reference. Configure a verified payee VPA for each operator before using this flow. JalSetu Lite does not receive payment-provider callbacks, verify settlement, or hold funds, so users must verify payment in their UPI app and the UI never marks a transfer as paid.

---

## 🏗️ System architecture and order lifecycle

```text
[ Citizen Pin Drop ] ➔ SEARCHING ➔ OFFERED (Ranked by distance/price)
                          │
                          ▼
                     CONFIRMED ➔ DISPATCHED (Operator Dashboard)
                         └──────➔ CANCELLED (Citizen, before dispatch)
                          │
                          ▼
                      ARRIVED ➔ DELIVERED (Verified by OTP + Meter Delta)
                          │
                          └─ (If 3x Bad OTP or <95% Volume) ➔ DISPUTED

```

---

## 🛠️ Tech stack

- **Backend:** Python 3.12, FastAPI, Uvicorn, Pydantic, SQLite (WAL mode)
- **Frontend:** HTML, CSS, and JavaScript dashboards, a Next.js scorecard app, and Leaflet with OpenStreetMap tiles
- **Security and validation:** HMAC-SHA256 salted OTP hashing and Haversine geospatial distance calculation

---

## 🚀 Installation and local setup

### Prerequisites

Install the following before starting:

- Python 3.12 or later
- Git (optional, if cloning the repository)

### 1. Install the backend dependencies

From the `jalsetu-lite` directory, create and activate a virtual environment in `backend`:

**Windows PowerShell**

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

**macOS/Linux**

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Start the backend API

Keep the virtual environment activated and run the API from the `backend` directory:

```bash
uvicorn main:app --reload --port 8000
```

The API is available at `http://localhost:8000`. Leave this terminal running.

### 3. Serve the frontend locally

Open a second terminal, go to the `frontend` directory, and start a local static file server.

**Windows PowerShell**

```powershell
cd frontend
py -m http.server 5500
```

**macOS/Linux**

```bash
cd frontend
python3 -m http.server 5500
```

Open the citizen portal at `http://localhost:5500/citizen.html`. The other dashboards are:

- Operator dashboard: `http://localhost:5500/operator.html`
- Driver dashboard: `http://localhost:5500/driver.html`
- Transparency scorecard: `http://localhost:5500/score/public/scores.html`

The frontend is configured to call the backend at `http://localhost:8000`. Start the backend before using the portals.

### Run the API workflow checks

The HTTP-level regression suite uses a temporary SQLite database and starts its own local API process. It does not modify the development database:

```powershell
cd backend
py -m unittest discover -s tests -v
```

On macOS or Linux, use `python3 -m unittest discover -s tests -v` from `backend`.

### Optional: Configure the OTP secret

For local development, the backend uses a demo OTP secret by default. Set `JALSETU_OTP_SECRET` before starting the backend if you want to use a custom secret:

**Windows PowerShell**

```powershell
$env:JALSETU_OTP_SECRET = "replace-with-a-long-random-secret"
uvicorn main:app --reload --port 8000
```

**macOS/Linux**

```bash
export JALSETU_OTP_SECRET="replace-with-a-long-random-secret"
uvicorn main:app --reload --port 8000
```

### Configure operator UPI payment IDs

Set each operator's verified VPA in the backend environment using its operator ID. For example:

**Windows PowerShell**

```powershell
$env:JALSETHU_UPI_ID_A = "verified-vpa@bank"
uvicorn main:app --reload --port 8000
```

**macOS/Linux**

```bash
export JALSETHU_UPI_ID_A="verified-vpa@bank"
uvicorn main:app --reload --port 8000
```

Use `JALSETHU_UPI_ID_P`, `JALSETHU_UPI_ID_B`, and so on for the other operator IDs. Obtain and verify these IDs with each real operator; do not use sample IDs. If an ID is missing or invalid, the API withholds that operator's payment QR. The payer must confirm the payee and amount in their UPI app; this prototype cannot confirm settlement automatically.

---

## Accounts and order history

- Citizens create an account from the citizen dashboard. Operator and driver accounts must be provisioned by an administrator; users cannot grant themselves staff roles.
- Open **Administrator: manage operator and driver accounts** from a dashboard login screen, then enter `JALSETHU_ADMIN_TOKEN` to list staff, create accounts, or set a new staff password. The API alternatives are `GET /auth/admin/users`, `POST /auth/admin/users`, and `PUT /auth/admin/users/{user_id}/password`, all requiring `X-Admin-Token`.
- Staff passwords are hashed and cannot be retrieved. The administrator chooses a password at creation/reset time and must securely share it with that staff member. Password changes revoke existing sessions.
- All dashboards have sign-in, sign-out, and account history controls. Passwords are salted PBKDF2 hashes; bearer sessions expire after 12 hours by default and are revocable.
- Order requests keep their existing contract fields. Authenticated orders and their status transitions are saved to SQLite for later history. Legacy anonymous orders remain available to their operator but cannot be retroactively attached to a citizen account.

## Deploy with Docker

The container serves the API and all static dashboards on one origin, and persists the SQLite database in a named volume. Use one application instance when using SQLite.

1. Create a local `.env` file (never commit it) with unique, randomly generated values:

```text
JALSETU_OTP_SECRET=<long-random-secret>
JALSETHU_ADMIN_TOKEN=<different-long-random-secret>
JALSETHU_CORS_ORIGINS=https://your-deployed-domain.example
```

Use different random values of at least 32 bytes for both secrets. In production, startup rejects missing, short, demo, or duplicate secrets and rejects wildcard CORS origins. List only the exact frontend origins, separated by commas; public origins must use HTTPS. HTTP origins are allowed only for localhost development.

2. Build and start:

```bash
docker compose up --build -d
```

3. Check `http://localhost:8000/health/ready`, then open the citizen page at `http://localhost:8000/`. `/health/live` checks the API process; `/health/ready` (also `/health`) checks that the database and required schema are available. Operator and driver accounts can be created through `POST /auth/admin/users` using the secret header; citizens self-register in the app.

The named `jalsethu-data` volume stores `/data/jalsetu.db` across container restarts. Back it up regularly. Configure the actual deployed origin in `JALSETHU_CORS_ORIGINS`; keep HTTPS enabled at the deployment edge. Set verified `JALSETHU_UPI_ID_<operator-id>` values for each payee in the deployment environment. A QR is a payment request only: integrate a payment provider and verify its signed settlement callbacks before representing a payment as confirmed.

### Database backup and recovery

The backup utility uses SQLite's online backup API, checks the resulting file with `PRAGMA integrity_check`, and writes the verified snapshot atomically. For Docker, run:

```bash
docker compose exec jalsethu python backup_db.py --destination /data/backups
docker compose cp jalsethu:/data/backups ./backups
```

Copy backups off the deployment host as well; a backup in the same Docker volume does not protect against volume or host loss. For a local backend, run `python backup_db.py --destination ../backups` from `backend`.

On startup, JalSetu upgrades older order-event tables in a transaction, adds a foreign key to their order, and archives any already-orphaned events in `order_event_orphans` instead of discarding them. The migration is repeat-safe.

To restore in Docker, first copy the selected verified backup into `/data/backups`, then stop the service and restore through a one-off container:

```bash
docker compose cp ./backups/jalsetu-YYYYMMDDTHHMMSSffffffZ.sqlite3 jalsethu:/data/backups/
docker compose stop jalsethu
docker compose run --rm --no-deps jalsethu python restore_db.py --source /data/backups/jalsetu-YYYYMMDDTHHMMSSffffffZ.sqlite3 --replace
docker compose up -d jalsethu
```

The restore command validates the source before changing anything and preserves a verified pre-restore copy beside the database. If the current database is corrupt, it preserves the raw database and WAL files instead. Stop the local backend before running `python restore_db.py --source ../backups/<backup-file>.sqlite3 --replace` from `backend`. Keep backups according to your operational retention policy and periodically rehearse restoration.

---

## 📁 Repository structure

```
jalsetu-lite/
├── backend/
│   ├── main.py              # FastAPI endpoints & order state machine
│   ├── backup_db.py         # Consistent, integrity-checked SQLite backups
│   ├── restore_db.py        # Verified restore with pre-restore recovery copy
│   ├── tests/               # Isolated API and database regression checks
│   ├── seed/
│   │   └── operators.json   # Seed operator data (BWSSB & local private)
│   └── requirements.txt     # Python dependencies
├── frontend/
│   ├── citizen.html         # Citizen portal with Leaflet pin drop & quote cards
│   ├── operator.html        # Operator order acceptance & dispatch dashboard
│   ├── driver.html          # Driver arrival, OTP & meter verification form
│   └── scores.html          # Operator transparency scorecard
├── docs/
│   └── api_contract.md      # Frozen API contract specification
├── Dockerfile
├── compose.yaml
├── .dockerignore
├── .gitignore
└── README.md
```

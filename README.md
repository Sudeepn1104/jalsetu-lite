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
4. **Zero-escrow UPI payments**  
  Direct peer-to-operator settlement uses dynamic UPI QR codes after successful delivery verification.

---

## 🏗️ System architecture and order lifecycle

```text
[ Citizen Pin Drop ] ➔ SEARCHING ➔ OFFERED (Ranked by distance/price)
                          │
                          ▼
                     CONFIRMED ➔ DISPATCHED (Operator Dashboard)
                          │
                          ▼
                      ARRIVED ➔ DELIVERED (Verified by OTP + Meter Delta)
                          │
                          └─ (If 3x Bad OTP or <95% Volume) ➔ DISPUTED

```

---

## 🛠️ Tech stack

- **Backend:** Python 3.12, FastAPI, Uvicorn, Pydantic, SQLite (WAL mode)
- **Frontend:** HTML5, Tailwind CSS, Vanilla JavaScript, Leaflet.js, and OpenStreetMap (Wikimedia tiles)
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
- Transparency scorecard: `http://localhost:5500/scores.html`

The frontend is configured to call the backend at `http://localhost:8000`. Start the backend before using the portals.

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

---

## 📁 Repository structure

```
jalsetu-lite/
├── backend/
│   ├── main.py              # FastAPI endpoints & Beckn state machine
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
├── .gitignore
└── README.md
```

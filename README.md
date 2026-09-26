```
# 🚰 JalSetu Lite — Digital Public Infrastructure for Water Logistics

&gt; **Build for Billions Hackathon** | **Track 3: Reinvent Digital Public Infrastructure For Billions**  
&gt; **Location:** NITK Surathkal  
&gt; **Team:** Team Diamonds  

---

## 📌 Problem Statement
In rapidly growing urban and sub-urban regions, water tanker delivery is heavily fragmented. Citizens face extreme price volatility, surge pricing during water scarcity, and lack reliable delivery verification. Private operators operate in siloes, while municipal authorities struggle to enforce baseline rate cards.

---

## 💡 The Solution: JalSetu Lite
**JalSetu Lite** is an open, Beckn-protocol-inspired Digital Public Infrastructure (DPI) network that connects citizens, public municipal water boards (e.g., BWSSB), and private tanker operators into a transparent marketplace.

### ✨ Key Capabilities
1. **Transparent Price Benchmarking**: Private fresh-water quotes exceeding official municipal rates (BWSSB benchmark rates: ₹660/4kL, ₹700/5kL, ₹740/6kL, ₹1,290/12kL) automatically display a **RED price warning tag**.
2. **Delivery Proof &amp; Meter Check**: Deliveries require a server-side HMAC 4-digit OTP and a **95% meter volume validation check** `((meter_after - meter_before) &gt;= 0.95 * capacity_l)`.
3. **Dispute Auto-Lock**: 3 incorrect OTP attempts or a volume deficit automatically lock the transaction into a `DISPUTED` state.
4. **Zero-Escrow UPI Payments**: Direct peer-to-operator settlement using dynamic UPI QR codes upon successful delivery verification.

---

## 🏗️ System Architecture &amp; Order Lifecycle

```text
[ Citizen Pin Drop ] ➔ SEARCHING ➔ OFFERED (Ranked by distance/price)
                          │
                          ▼
                     CONFIRMED ➔ DISPATCHED (Operator Dashboard)
                          │
                          ▼
                      ARRIVED ➔ DELIVERED (Verified by OTP + Meter Delta)
                          │
                          └─ (If 3x Bad OTP or &lt;95% Volume) ➔ DISPUTED

```

---

## 🛠️ Tech Stack

* **Backend**: Python 3.12, FastAPI, Uvicorn, Pydantic, SQLite (WAL mode)
* **Frontend**: HTML5, Tailwind CSS, Vanilla JavaScript, Leaflet.js / OpenStreetMap (Wikimedia tiles)
* **Security &amp; Validation**: HMAC-SHA256 salted OTP hashing, Haversine geospatial distance calculation

---

## 📁 Repository Structure

```
jalsetu-lite/
├── backend/
│   ├── main.py              # FastAPI endpoints &amp; Beckn state machine
│   ├── seed/
│   │   └── operators.json   # Seed operator data (BWSSB &amp; local private)
│   └── requirements.txt     # Python dependencies
├── frontend/
│   ├── citizen.html         # Citizen portal with Leaflet pin drop &amp; quote cards
│   ├── operator.html        # Operator order acceptance &amp; dispatch dashboard
│   ├── driver.html          # Driver arrival, OTP &amp; meter verification form
│   └── scores.html          # Operator transparency scorecard
├── docs/
│   └── api_contract.md      # Frozen API contract specification
├── .gitignore
└── README.md
```

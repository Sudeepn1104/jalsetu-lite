# JalSetu Lite — Build for Billions, Team Diamonds

24-hour build. Spec: docs/api_contract.md. Seed data: seed/operators.json.

## Setup (run tonight, not tomorrow)
```
cd backend
pip install -r requirements.txt --break-system-packages
uvicorn main:app --reload --port 8000
```
Open http://localhost:8000/health — should return {"status":"ok"}.

## Roles
- Backend (1-2): implement TODOs in backend/main.py per docs/api_contract.md
- Frontend (1-2): citizen.html + map (frontend/)
- Operator/driver pages (1): operator.html, driver.html
- Seed/scorecard + demo/slides (1): seed data already in seed/operators.json; build scores.html; own the demo script

## Tomorrow's schedule (10:00-20:00)
10:00-10:20  role lock, branch creation
10:20-13:30  parallel build
13:30-14:00  lunch
14:00-16:30  integration
16:30-18:00  polish (Kannada toggle, UPI QR, scorecard)
18:00-19:00  full run-through + backup video
19:00-19:45  slides + rehearsal
19:45-20:00  buffer + submit

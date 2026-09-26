# Frontend skeleton
- citizen.html — request + offers + tracking (build first)
- operator.html — accept order
- driver.html — OTP + meter entry
- scores.html — operator scorecard

Use fetch() against http://localhost:8000 per docs/api_contract.md.
Poll GET /status/{order_id} every 2 seconds while tracking.

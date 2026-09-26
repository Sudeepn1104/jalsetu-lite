"""Isolated HTTP-level regression tests for the JalSetu API."""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.parse import parse_qs
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]


def reserve_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class APIWorkflowTests(unittest.TestCase):
    def test_production_configuration_rejects_weak_secrets_and_unsafe_cors(self) -> None:
        base = os.environ.copy()
        production_database = Path(tempfile.gettempdir()) / f"jalsethu-production-config-{os.getpid()}.sqlite3"
        for artifact in (
            production_database,
            Path(f"{production_database}-wal"),
            Path(f"{production_database}-shm"),
        ):
            self.addCleanup(artifact.unlink, missing_ok=True)
        base.update(
            {
                "JALSETHU_ENV": "production",
                "JALSETU_OTP_SECRET": "o" * 40,
                "JALSETHU_ADMIN_TOKEN": "a" * 40,
                "JALSETHU_CORS_ORIGINS": "https://app.example.test",
                "JALSETHU_DB_PATH": str(production_database),
            }
        )
        invalid_settings = (
            ("JALSETU_OTP_SECRET", "short"),
            ("JALSETU_OTP_SECRET", "jalsethu-demo-secret-change-before-deploy"),
            ("JALSETHU_ADMIN_TOKEN", "short"),
            ("JALSETHU_ADMIN_TOKEN", "o" * 40),
            ("JALSETHU_CORS_ORIGINS", "*"),
            ("JALSETHU_CORS_ORIGINS", "http://app.example.test"),
            ("JALSETHU_CORS_ORIGINS", "https://app.example.test/path"),
            ("JALSETHU_CORS_ORIGINS", "https://app.example.test:bad"),
        )
        for key, value in invalid_settings:
            with self.subTest(setting=key, value=value):
                environment = base.copy()
                environment[key] = value
                result = subprocess.run(
                    [sys.executable, "-c", "import main"],
                    cwd=BACKEND_DIR,
                    env=environment,
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("RuntimeError", result.stderr)

        valid = subprocess.run(
            [sys.executable, "-c", "import main"],
            cwd=BACKEND_DIR,
            env=base,
            capture_output=True,
            text=True,
        )
        self.assertEqual(valid.returncode, 0, valid.stderr)

        local = base.copy()
        local["JALSETHU_CORS_ORIGINS"] = "http://localhost:8000"
        local_valid = subprocess.run(
            [sys.executable, "-c", "import main"],
            cwd=BACKEND_DIR,
            env=local,
            capture_output=True,
            text=True,
        )
        self.assertEqual(local_valid.returncode, 0, local_valid.stderr)

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="jalsethu-api-tests-")
        cls.database_path = Path(cls.temp_dir.name) / "workflow.sqlite3"
        cls.port = reserve_port()
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        environment = os.environ.copy()
        environment.update(
            {
                "JALSETHU_DB_PATH": str(cls.database_path),
                "JALSETHU_ENV": "test",
                "JALSETHU_CORS_ORIGINS": "http://127.0.0.1",
                "JALSETU_OTP_SECRET": "test-only-otp-secret-that-is-never-deployed",
                "JALSETHU_ADMIN_TOKEN": "test-only-admin-token",
            }
        )
        cls.server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(cls.port),
                "--no-access-log",
            ],
            cwd=BACKEND_DIR,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if cls.server.poll() is not None:
                raise RuntimeError("Test API server exited before becoming ready")
            try:
                status, body = cls.request("GET", "/health")
                if status == 200 and body.get("status") == "ok":
                    break
            except (OSError, URLError):
                time.sleep(0.1)
        else:
            cls.server.terminate()
            raise RuntimeError("Test API server did not become ready within 15 seconds")

        status, _ = cls.request(
            "POST",
            "/auth/admin/users",
            {
                "name": "Test Operator",
                "email": "operator@example.test",
                "phone": None,
                "password": "test-operator-password",
                "role": "operator",
                "operator_id": "A",
            },
            admin_token="test-only-admin-token",
        )
        if status != 200:
            raise RuntimeError(f"Could not provision test operator (HTTP {status})")

        status, _ = cls.request(
            "POST",
            "/auth/admin/users",
            {
                "name": "Test Driver",
                "email": "driver@example.test",
                "phone": None,
                "password": "test-driver-password",
                "role": "driver",
                "operator_id": "A",
            },
            admin_token="test-only-admin-token",
        )
        if status != 200:
            raise RuntimeError(f"Could not provision test driver (HTTP {status})")

        cls.operator_token = cls.login("operator@example.test", "test-operator-password", "operator")
        cls.driver_token = cls.login("driver@example.test", "test-driver-password", "driver")

    @classmethod
    def tearDownClass(cls) -> None:
        server = getattr(cls, "server", None)
        if server is not None and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        temp_dir = getattr(cls, "temp_dir", None)
        if temp_dir is not None:
            temp_dir.cleanup()

    @classmethod
    def request(
        cls,
        method: str,
        path: str,
        body: dict | None = None,
        *,
        token: str | None = None,
        admin_token: str | None = None,
    ) -> tuple[int, object]:
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if admin_token:
            headers["X-Admin-Token"] = admin_token
        request = Request(f"{cls.base_url}{path}", data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=5) as response:
                payload = response.read()
                return response.status, payload if response.headers.get_content_type() == "image/svg+xml" else json.loads(payload or b"{}")
        except HTTPError as response:
            return response.code, json.loads(response.read() or b"{}")

    @classmethod
    def login(cls, email: str, password: str, role: str) -> str:
        status, body = cls.request(
            "POST",
            "/auth/login",
            {"email": email, "password": password, "role": role},
        )
        if status != 200:
            raise RuntimeError(f"Could not sign in test {role} (HTTP {status})")
        return body["access_token"]

    def register_citizen(self, suffix: str) -> str:
        status, body = self.request(
            "POST",
            "/auth/register",
            {
                "name": "Test Citizen",
                "email": f"citizen-{suffix}@example.test",
                "phone": None,
                "password": "test-citizen-password",
            },
        )
        self.assertEqual(status, 200, body)
        return body["access_token"]

    def create_order(self, citizen_token: str) -> tuple[str, str]:
        status, body = self.request(
            "POST",
            "/select",
            {
                "capacity_l": 4000,
                "water_type": "fresh",
                "lat": 12.918,
                "lng": 74.852,
                "operator_id": "A",
            },
            token=citizen_token,
        )
        self.assertEqual(status, 200, body)
        order_id = body["order_id"]
        status, confirmation = self.request(
            "POST", "/confirm", {"order_id": order_id}, token=citizen_token
        )
        self.assertEqual(status, 200, confirmation)
        return order_id, confirmation["otp"]

    def dispatch_and_arrive(self, order_id: str) -> None:
        status, body = self.request(
            "POST", f"/operator/dispatch/{order_id}", token=self.operator_token
        )
        self.assertEqual(status, 200, body)
        status, body = self.request(
            "POST", f"/driver/arrive/{order_id}", token=self.driver_token
        )
        self.assertEqual(status, 200, body)

    def test_delivery_lifecycle_accepts_exactly_95_percent(self) -> None:
        citizen_token = self.register_citizen("exact-volume")
        order_id, otp = self.create_order(citizen_token)

        self.dispatch_and_arrive(order_id)
        status, body = self.request(
            "POST",
            f"/driver/deliver/{order_id}",
            {"otp": otp, "meter_before": 1000, "meter_after": 4800},
            token=self.driver_token,
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["litres_delivered"], 3800)

        status, body = self.request("GET", f"/status/{order_id}", token=citizen_token)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["status"], "DELIVERED")

        status, body = self.request(
            "POST", f"/driver/arrive/{order_id}", token=self.driver_token
        )
        self.assertEqual(status, 409, body)
        self.assertEqual(body["error"], "INVALID_STATE")

        status, history = self.request("GET", "/auth/history", token=citizen_token)
        self.assertEqual(status, 200, history)
        self.assertEqual(history["orders"][0]["meter_before"], 1000)
        self.assertEqual(history["orders"][0]["meter_after"], 4800)
        self.assertEqual(history["orders"][0]["litres_delivered"], 3800)
        events = [event["to_status"] for event in history["orders"][0]["events"]]
        self.assertEqual(events, ["OFFERED", "CONFIRMED", "DISPATCHED", "ARRIVED", "DELIVERED"])

    def test_citizen_can_cancel_before_dispatch_and_history_records_the_change(self) -> None:
        citizen_token = self.register_citizen("cancel")
        order_id, _ = self.create_order(citizen_token)

        status, body = self.request("POST", f"/orders/{order_id}/cancel", token=self.operator_token)
        self.assertEqual(status, 403, body)
        self.assertEqual(body["error"], "ROLE_FORBIDDEN")

        status, body = self.request("POST", f"/orders/{order_id}/cancel", token=citizen_token)
        self.assertEqual(status, 200, body)
        self.assertEqual(body, {"order_id": order_id, "status": "CANCELLED"})

        status, order = self.request("GET", f"/status/{order_id}", token=citizen_token)
        self.assertEqual(status, 200, order)
        self.assertEqual(order["status"], "CANCELLED")

        status, body = self.request("POST", f"/operator/dispatch/{order_id}", token=self.operator_token)
        self.assertEqual(status, 409, body)
        self.assertEqual(body["error"], "INVALID_STATE")

        status, history = self.request("GET", "/auth/history", token=citizen_token)
        self.assertEqual(status, 200, history)
        self.assertEqual(
            [event["to_status"] for event in history["orders"][0]["events"]],
            ["OFFERED", "CONFIRMED", "CANCELLED"],
        )

    def test_cancellation_and_dispatch_race_has_exactly_one_winner(self) -> None:
        citizen_token = self.register_citizen("cancel-race")
        order_id, _ = self.create_order(citizen_token)

        def cancel() -> int:
            return self.request("POST", f"/orders/{order_id}/cancel", token=citizen_token)[0]

        def dispatch() -> int:
            return self.request("POST", f"/operator/dispatch/{order_id}", token=self.operator_token)[0]

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda action: action(), (cancel, dispatch)))
        self.assertCountEqual(results, [200, 409])

        status, body = self.request("GET", f"/status/{order_id}", token=citizen_token)
        self.assertEqual(status, 200, body)
        self.assertIn(body["status"], {"CANCELLED", "DISPATCHED"})
        if body["status"] == "DISPATCHED":
            status, body = self.request("POST", f"/orders/{order_id}/cancel", token=citizen_token)
            self.assertEqual(status, 409, body)

    def test_third_incorrect_otp_disputes_the_order(self) -> None:
        citizen_token = self.register_citizen("otp-lock")
        order_id, correct_otp = self.create_order(citizen_token)
        self.dispatch_and_arrive(order_id)
        wrong_otp = "0000" if correct_otp != "0000" else "0001"

        def submit_wrong_otp(_: int) -> int:
            status, _ = self.request(
                "POST",
                f"/driver/deliver/{order_id}",
                {"otp": wrong_otp, "meter_before": 0, "meter_after": 4000},
                token=self.driver_token,
            )
            return status

        with ThreadPoolExecutor(max_workers=4) as executor:
            statuses = list(executor.map(submit_wrong_otp, range(4)))
        self.assertCountEqual(statuses, [422, 422, 409, 409])

        status, body = self.request("GET", f"/status/{order_id}", token=citizen_token)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["status"], "DISPUTED")
        connection = sqlite3.connect(self.database_path)
        try:
            attempts = connection.execute(
                "SELECT otp_attempts FROM orders WHERE order_id = ?", (order_id,)
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(attempts, 3)

    def test_order_access_is_authenticated_and_scoped_to_its_citizen(self) -> None:
        status, body = self.request(
            "POST",
            "/select",
            {
                "capacity_l": 4000,
                "water_type": "fresh",
                "lat": 12.918,
                "lng": 74.852,
                "operator_id": "A",
            },
        )
        self.assertEqual(status, 401, body)

        owner_token = self.register_citizen("owner")
        other_token = self.register_citizen("other")
        order_id, _ = self.create_order(owner_token)

        status, body = self.request("GET", f"/status/{order_id}", token=other_token)
        self.assertEqual(status, 403, body)
        self.assertEqual(body["error"], "ORDER_FORBIDDEN")

        status, body = self.request("POST", f"/orders/{order_id}/cancel", token=other_token)
        self.assertEqual(status, 403, body)
        self.assertEqual(body["error"], "ORDER_FORBIDDEN")

        status, body = self.request(
            "POST", "/select", {"capacity_l": 4000, "water_type": "fresh", "lat": 0, "lng": 0, "operator_id": "A"}, token=self.driver_token
        )
        self.assertEqual(status, 403, body)
        self.assertEqual(body["error"], "ROLE_FORBIDDEN")

    def test_payment_qr_requires_configured_operator_vpa_and_encodes_fixed_order_amount(self) -> None:
        citizen_token = self.register_citizen("upi-qr")
        order_id, _ = self.create_order(citizen_token)

        status, body = self.request(
            "GET", f"/upi-qr?order_id={order_id}", token=citizen_token
        )
        self.assertEqual(status, 409, body)
        self.assertEqual(body["error"], "PAYMENT_NOT_CONFIGURED")

        from fastapi.responses import Response
        from main import get_upi_qr

        with patch.dict(os.environ, {"JALSETHU_UPI_ID_A": "ganesh.water@bank"}), patch("main.authenticated_user", return_value={"id": 1, "role": "citizen"}), patch(
            "main.fetch_order",
            return_value={
                "order_id": order_id,
                "status": "CONFIRMED",
                "operator_id": "A",
                "price": 500,
                "citizen_user_id": 1,
            },
        ), patch("main.render_upi_qr", side_effect=lambda uri: Response(uri)):
            response = get_upi_qr(order_id)
        uri = response.body.decode("utf-8")
        self.assertTrue(uri.startswith("upi://pay?"))
        parameters = parse_qs(uri.split("?", 1)[1])
        self.assertEqual(parameters["pa"], ["ganesh.water@bank"])
        self.assertEqual(parameters["pn"], ["Ganesh Water Suppliers"])
        self.assertEqual(parameters["tr"], [order_id])
        self.assertEqual(parameters["am"], ["500.00"])
        self.assertEqual(parameters["cu"], ["INR"])

    def test_search_returns_matching_offers_nearest_first(self) -> None:
        status, body = self.request(
            "POST",
            "/search",
            {"capacity_l": 4000, "water_type": "fresh", "lat": 12.92, "lng": 74.85},
        )
        self.assertEqual(status, 200, body)
        offers = body["offers"]
        self.assertTrue(offers)
        distances = [offer["distance_km"] for offer in offers]
        self.assertEqual(distances, sorted(distances))
        self.assertFalse({"F", "G"}.intersection(offer["operator_id"] for offer in offers))


if __name__ == "__main__":
    unittest.main()

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const citizenHtml = readFileSync(new URL("../citizen.html", import.meta.url), "utf8");

test("demo payment QR is labeled as non-payable and hidden until the image loads", () => {
  assert.match(citizenHtml, /DEMO ONLY · Uses demo@invalid/);
  assert.match(citizenHtml, /Do not authorize a payment/);
  assert.match(citizenHtml, /<img class="qr" id="paymentQr"[^>]*\shidden>/);
  assert.match(citizenHtml, /\.qr\[hidden\]\s*\{\s*display\s*:\s*none\s*\}/);
});

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const citizenHtml = readFileSync(new URL("../citizen.html", import.meta.url), "utf8");

test("unavailable payment QR stays hidden instead of showing a broken image box", () => {
  assert.match(citizenHtml, /<img class="qr" id="paymentQr"[^>]*\shidden>/);
  assert.match(citizenHtml, /\.qr\[hidden\]\s*\{\s*display\s*:\s*none\s*\}/);
});

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const driverHtml = readFileSync(new URL("../driver.html", import.meta.url), "utf8");
const inlineScripts = [...driverHtml.matchAll(/<script>([\s\S]*?)<\/script>/g)];
assert.ok(inlineScripts.length, "driver page should contain its workflow script");
const driverScript = inlineScripts.at(-1)[1];

function response(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async text() { return JSON.stringify(body); },
  };
}

function mountDriver(fetch) {
  const ids = [
    "order_id", "arrivalBtn", "statusBtn", "nextOrderBtn", "deliverySection",
    "arrivalStatus", "deliveryStatus", "otp", "meter_before", "meter_after", "deliverBtn",
  ];
  const elements = Object.fromEntries(ids.map((id) => [id, {
    value: "",
    textContent: "",
    className: "status",
    disabled: false,
    text: "",
    style: { display: ["arrivalBtn", "nextOrderBtn", "deliverySection"].includes(id) ? "none" : "block" },
    addEventListener() {},
    focus() {},
  }]));
  const documentListeners = {};
  const windowListeners = {};
  const localStorage = new Map();
  const document = {
    documentElement: { removeAttribute() {}, setAttribute() {} },
    getElementById(id) { return elements[id]; },
    querySelectorAll() { return []; },
    addEventListener(name, callback) { documentListeners[name] = callback; },
  };
  const window = {
    JALSETHU_API_BASE: "http://127.0.0.1:8000",
    JALSETHU_AUTH: { user: null },
    addEventListener(name, callback) { windowListeners[name] = callback; },
  };
  const context = vm.createContext({
    window,
    document,
    localStorage: {
      getItem(key) { return localStorage.get(key) ?? null; },
      setItem(key, value) { localStorage.set(key, String(value)); },
      removeItem(key) { localStorage.delete(key); },
    },
    fetch,
    console: { error() {} },
  });
  vm.runInContext(driverScript, context, { filename: "driver.html" });
  documentListeners.DOMContentLoaded();
  return { context, elements, windowListeners };
}

test("driver can complete one order and reset cleanly for the next order", async () => {
  const calls = [];
  const fetch = async (url, options = {}) => {
    calls.push({ url: String(url), method: options.method || "GET" });
    const statusMatch = String(url).match(/\/status\/(JS-[12])$/);
    if (statusMatch) return response(200, { order_id: statusMatch[1], status: "DISPATCHED" });
    if (String(url).match(/\/driver\/arrive\/JS-[12]$/)) return response(200, { status: "ARRIVED" });
    if (String(url).match(/\/driver\/deliver\/JS-[12]$/)) {
      return response(200, { status: "DELIVERED", litres_delivered: 3800 });
    }
    throw new Error(`Unexpected request: ${url}`);
  };

  const { context, elements } = mountDriver(fetch);
  for (const orderId of ["JS-1", "JS-2"]) {
    elements.order_id.value = orderId;
    const current = await context.checkOrderStatus(true);
    assert.equal(current.status, "DISPATCHED");
    await context.markArrival();
    assert.equal(elements.deliverySection.style.display, "block");
    elements.otp.value = "1234";
    elements.meter_before.value = "1000";
    elements.meter_after.value = "4800";
    await context.completeDelivery();
    assert.match(elements.deliveryStatus.textContent, /Delivery completed successfully/);
    assert.equal(elements.nextOrderBtn.style.display, "block");

    if (orderId === "JS-1") {
      context.startNextOrder();
      assert.equal(elements.order_id.value, "");
      assert.equal(elements.deliverySection.style.display, "none");
      assert.equal(elements.nextOrderBtn.style.display, "none");
      assert.equal(elements.otp.value, "");
      assert.equal(elements.meter_before.value, "");
      assert.equal(elements.meter_after.value, "");
      assert.equal(elements.deliverBtn.disabled, false);
    }
  }

  assert.ok(calls.some(({ url }) => url.endsWith("/driver/deliver/JS-1")));
  assert.ok(calls.some(({ url }) => url.endsWith("/driver/deliver/JS-2")));
});

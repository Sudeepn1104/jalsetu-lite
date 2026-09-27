import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const page = await readFile(new URL("../citizen.html", import.meta.url), "utf8");
const auth = await readFile(new URL("../auth.js", import.meta.url), "utf8");
const inlineScript = [...page.matchAll(/<script>([\s\S]*?)<\/script>/gi)].at(-1)?.[1];

test("citizen feedback page script parses and renders the empty vendor state", () => {
  assert.ok(inlineScript, "citizen page should have its application script");
  assert.doesNotThrow(() => new Function(inlineScript));
  assert.match(inlineScript, /else if\(!summary\.total_reviews\)/);
  assert.match(inlineScript, /feedbackEmpty:"No feedback yet for this vendor"/);
});

test("feedback is offered only on a terminal order result, never during login recovery", () => {
  assert.match(inlineScript, /jalsethu-feedback-dismissed:/);
  assert.doesNotMatch(inlineScript, /\/auth\/feedback\/pending/);
  const activeOrderLoader = inlineScript.match(/async function loadActiveOrders\(\)[\s\S]*?\n\}/)?.[0];
  assert.ok(activeOrderLoader);
  assert.doesNotMatch(activeOrderLoader, /queueFeedbackPrompt/);
  assert.match(inlineScript, /queueFeedbackPrompt\(\{order_id:S\.orderId,status:S\.status\}\)/);
});

test("citizens can open feedback from terminal orders in account history", () => {
  assert.match(auth, /Rate or edit feedback/);
  assert.match(auth, /jalsethu:feedback-request/);
  assert.match(inlineScript, /jalsethu:feedback-request/);
  assert.match(inlineScript, /<form id="feedbackForm">/);
  assert.match(inlineScript, /feedback-toast/);
});

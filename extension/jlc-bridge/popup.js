// Popup: the only place a send can start.
//
// The click here IS the user gesture that authorises the whole flow — the
// service worker acts on nothing else, and nothing outside this extension can
// send it a message.

import { getBaseUrl, SESSION_PATH } from "./config.js";

const nonceInput = document.getElementById("nonce");
const sendButton = document.getElementById("send");
const cancelButton = document.getElementById("cancel");
const statusLine = document.getElementById("status");
const targetLine = document.getElementById("target");

const BUSY_STATES = new Set(["polling", "pushing"]);

/**
 * @param {string} text
 * @param {"ok"|"err"|"busy"|""} kind
 */
function say(text, kind) {
  statusLine.textContent = text;
  statusLine.className = kind || "";
}

/** @param {{state: string, message: string}} status */
function render(status) {
  const busy = BUSY_STATES.has(status.state);
  sendButton.disabled = busy;
  cancelButton.disabled = !busy;
  const kind = status.state === "ok" ? "ok" : status.state === "error" ? "err" : busy ? "busy" : "";
  say(status.message || "Idle.", kind);
}

/**
 * @param {object} message
 * @returns {Promise<object>}
 */
async function ask(message) {
  const reply = await chrome.runtime.sendMessage(message);
  if (!reply) throw new Error("The extension's service worker did not answer.");
  return reply;
}

async function refresh() {
  try {
    const reply = await ask({ type: "status" });
    if (reply.ok) render(reply.status);
  } catch (err) {
    say(err.message, "err");
  }
}

async function showTarget() {
  try {
    const base = await getBaseUrl();
    targetLine.textContent = `${base}${SESSION_PATH}`;
  } catch (err) {
    targetLine.textContent = err.message;
  }
}

sendButton.addEventListener("click", async () => {
  const nonce = nonceInput.value.trim();
  if (!nonce) {
    say("Paste the pairing code dubIS showed you first.", "err");
    return;
  }
  sendButton.disabled = true;
  cancelButton.disabled = false;
  say("Starting…", "busy");
  try {
    const reply = await ask({ type: "start", nonce });
    if (!reply.ok) {
      say(reply.error, "err");
    } else {
      nonceInput.value = "";
    }
  } catch (err) {
    say(err.message, "err");
  }
  await refresh();
});

cancelButton.addEventListener("click", async () => {
  try {
    await ask({ type: "cancel" });
  } catch (err) {
    say(err.message, "err");
  }
  await refresh();
});

document.getElementById("options-link").addEventListener("click", (event) => {
  event.preventDefault();
  chrome.runtime.openOptionsPage();
});

showTarget();
refresh();
// While the popup is open, mirror the worker's progress.
setInterval(refresh, 1000);

// Options page: the dubIS base URL, and nothing else.

import { DEFAULT_BASE_URL, getBaseUrl, setBaseUrl } from "./config.js";

const input = document.getElementById("base-url");
const saveButton = document.getElementById("save");
const line = document.getElementById("line");

/**
 * @param {string} text
 * @param {"ok"|"err"|""} kind
 */
function say(text, kind) {
  line.textContent = text;
  line.className = kind ? `line ${kind}` : "line";
}

async function load() {
  try {
    input.value = await getBaseUrl();
  } catch (err) {
    // A stored value that no longer validates should be visible, not hidden
    // behind a silent fallback to the default.
    input.value = DEFAULT_BASE_URL;
    say(`Stored URL was invalid (${err.message}) — showing the default.`, "err");
  }
}

saveButton.addEventListener("click", async () => {
  try {
    const saved = await setBaseUrl(input.value);
    input.value = saved;
    say(`Saved: ${saved}`, "ok");
  } catch (err) {
    say(err.message, "err");
  }
});

load();

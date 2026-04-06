// @ts-check
/* generic-parts-modal.js — Create / Edit generic part modal */

import { api, AppLog } from './api.js';
import { Modal, showToast, escHtml } from './ui-helpers.js';
import { EventBus, Events } from './event-bus.js';
import { App } from './store.js';

var modal;
var editingId = null;  // null = create mode, string = edit mode

var SPEC_FIELDS = {
  capacitor: ["value", "package", "voltage", "tolerance", "dielectric"],
  resistor:  ["value", "package", "tolerance", "power"],
  inductor:  ["value", "package", "tolerance", "current"],
  ic:        ["package"],
  other:     ["package"],
};

export function init() {
  modal = Modal("generic-modal", { cancelId: "gp-cancel" });

  document.getElementById("gp-type").addEventListener("change", function () {
    renderSpecFields();
    previewMembers();
  });

  document.getElementById("gp-save").addEventListener("click", handleSave);
}

/**
 * Open the modal in create mode, pre-filled from a part's extracted spec.
 * @param {string} [partKey] - inventory part key to extract spec from
 * @param {{ type?: string, value?: string, package?: string }} [bomSpec] - BOM-derived spec
 */
export async function openCreate(partKey, bomSpec) {
  editingId = null;
  document.getElementById("generic-modal-title").textContent = "Create Generic Part";
  document.getElementById("gp-save").textContent = "Create";

  var spec = {};
  if (partKey) {
    try { spec = await api("extract_spec", partKey); } catch (_) { /* use empty */ }
  }
  if (bomSpec) {
    if (bomSpec.type) spec.type = bomSpec.type;
    if (bomSpec.value) spec.value = bomSpec.value;
    if (bomSpec.package) spec.package = bomSpec.package;
  }

  // Set type
  var typeMap = { capacitor: "capacitor", resistor: "resistor", inductor: "inductor" };
  var typeEl = /** @type {HTMLSelectElement} */ (document.getElementById("gp-type"));
  typeEl.value = typeMap[spec.type] || "other";

  // Set name
  var nameParts = [];
  if (spec.value) nameParts.push(spec.value);
  if (spec.package) nameParts.push(spec.package);
  if (spec.type) nameParts.push(spec.type === "capacitor" ? "MLCC" : spec.type === "resistor" ? "Chip Resistor" : spec.type);
  /** @type {HTMLInputElement} */ (document.getElementById("gp-name")).value = nameParts.join(" ") || "";

  renderSpecFields(spec);
  modal.open();
  previewMembers();
}

/**
 * Open the modal in edit mode for an existing generic part.
 * @param {string} genericPartId
 */
export function openEdit(genericPartId) {
  editingId = genericPartId;
  var gp = App.genericParts.find(function (g) { return g.generic_part_id === genericPartId; });
  if (!gp) { showToast("Generic part not found"); return; }

  document.getElementById("generic-modal-title").textContent = "Edit Generic Part";
  document.getElementById("gp-save").textContent = "Update";

  var typeEl = /** @type {HTMLSelectElement} */ (document.getElementById("gp-type"));
  typeEl.value = gp.part_type || "other";

  /** @type {HTMLInputElement} */ (document.getElementById("gp-name")).value = gp.name || "";

  var spec = gp.spec || {};
  var strictness = gp.strictness || {};
  renderSpecFields(spec, strictness);
  modal.open();
  renderMembersPreview(gp.members || []);
}

function renderSpecFields(spec, strictness) {
  spec = spec || {};
  strictness = strictness || {};
  var required = strictness.required || ["value", "package"];
  var type = /** @type {HTMLSelectElement} */ (document.getElementById("gp-type")).value;
  var fields = SPEC_FIELDS[type] || ["package"];
  var container = document.getElementById("gp-spec-fields");

  var html = "";
  for (var i = 0; i < fields.length; i++) {
    var f = fields[i];
    var val = spec[f] || "";
    var isReq = required.indexOf(f) !== -1;
    html += '<div class="modal-form gp-spec-row">' +
      '<label for="gp-spec-' + f + '">' + escHtml(f.charAt(0).toUpperCase() + f.slice(1)) + ':</label>' +
      '<input type="text" id="gp-spec-' + f + '" value="' + escHtml(String(val)) + '" data-field="' + f + '" class="gp-spec-input">' +
      '<label class="gp-req-label"><input type="checkbox" class="gp-req-check" data-field="' + f + '"' + (isReq ? ' checked' : '') + '> Required</label>' +
      '</div>';
  }
  container.innerHTML = html;

  // Live preview on spec changes
  container.querySelectorAll(".gp-spec-input, .gp-req-check").forEach(function (el) {
    el.addEventListener("change", function () { previewMembers(); });
  });
}

function collectSpec() {
  var spec = {};
  var required = [];
  document.querySelectorAll(".gp-spec-input").forEach(function (el) {
    var input = /** @type {HTMLInputElement} */ (el);
    var field = input.dataset.field;
    if (input.value.trim()) spec[field] = input.value.trim();
  });
  document.querySelectorAll(".gp-req-check").forEach(function (el) {
    var cb = /** @type {HTMLInputElement} */ (el);
    if (cb.checked) required.push(cb.dataset.field);
  });
  var type = /** @type {HTMLSelectElement} */ (document.getElementById("gp-type")).value;
  spec.type = type;
  return { spec: spec, strictness: { required: required } };
}

async function previewMembers() {
  var data = collectSpec();
  var name = /** @type {HTMLInputElement} */ (document.getElementById("gp-name")).value.trim();
  if (!name || Object.keys(data.spec).length <= 1) {
    renderMembersPreview([]);
    return;
  }
  try {
    var result = await api("create_generic_part", name + "_preview_" + Date.now(),
      data.spec.type || "other", JSON.stringify(data.spec), JSON.stringify(data.strictness));
    // This is a preview hack — we create then immediately delete
    // TODO: Add a proper preview endpoint
    renderMembersPreview(result.members || []);
  } catch (_) {
    renderMembersPreview([]);
  }
}

function renderMembersPreview(members) {
  var container = document.getElementById("gp-members-preview");
  document.getElementById("gp-member-count").textContent = members.length > 0 ? "(" + members.length + ")" : "";
  if (members.length === 0) {
    container.innerHTML = '<div class="muted" style="padding:6px;font-size:12px">No matching parts found</div>';
    return;
  }
  var html = '<div class="gp-preview-list">';
  for (var i = 0; i < members.length; i++) {
    var m = members[i];
    var prefBadge = m.preferred ? ' <span class="preferred-badge">\u2605</span>' : '';
    html += '<div class="gp-preview-item">' +
      '<span class="mono">' + escHtml(m.part_id) + '</span>' + prefBadge +
      '<span class="muted" style="margin-left:8px">' + m.quantity + ' in stock</span>' +
      '</div>';
  }
  html += '</div>';
  container.innerHTML = html;
}

async function handleSave() {
  var name = /** @type {HTMLInputElement} */ (document.getElementById("gp-name")).value.trim();
  if (!name) { showToast("Name is required"); return; }
  var data = collectSpec();

  try {
    if (editingId) {
      await api("update_generic_part", editingId, name,
        JSON.stringify(data.spec), JSON.stringify(data.strictness));
      showToast("Updated generic part: " + name);
    } else {
      var type = data.spec.type || "other";
      var result = await api("create_generic_part", name, type,
        JSON.stringify(data.spec), JSON.stringify(data.strictness));
      var count = (result.members || []).length;
      showToast("Created generic part: " + name + " (" + count + " members)");
    }

    // Refresh generic parts
    var gps = await api("list_generic_parts");
    App.genericParts = Array.isArray(gps) ? gps : [];
    EventBus.emit(Events.GENERIC_PARTS_LOADED, App.genericParts);

    modal.close();
  } catch (e) {
    AppLog.error("Generic part save failed: " + e);
    showToast("Error: " + e);
  }
}

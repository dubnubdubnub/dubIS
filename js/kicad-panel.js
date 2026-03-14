// @ts-check
/* kicad-panel.js — KiCad projects tab in left panel (self-initializing) */

import { EventBus, Events } from './event-bus.js';
import { api, AppLog } from './api.js';
import { showToast, escHtml } from './ui-helpers.js';
import { App, loadKicadState } from './store.js';
import { processBOM } from './csv-parser.js';
import { matchBOM } from './matching.js';

/**
 * @typedef {object} KicadPart
 * @property {string} ref
 * @property {string} value
 * @property {string} footprint
 * @property {string} footprint_full
 * @property {string} lcsc
 * @property {string} mpn
 * @property {boolean} dnp
 */

const body = document.getElementById("kicad-body");

/** @type {string|null} Currently expanded project path */
let expandedProject = null;

/** @type {KicadPart[]} Parts from the currently expanded project */
let expandedParts = [];

// ── Tab bar switching ──────────────────────────────────────────────────

const tabBar = document.querySelector(".tab-bar");
if (tabBar) {
  tabBar.addEventListener("click", (e) => {
    const btn = /** @type {HTMLElement|null} */ (/** @type {HTMLElement} */ (e.target).closest(".tab"));
    if (!btn) return;
    const tab = btn.dataset.tab;
    tabBar.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    btn.classList.add("active");

    const importBody = document.getElementById("import-body");
    const kicadBody = document.getElementById("kicad-body");
    if (tab === "import") {
      importBody?.classList.remove("hidden");
      kicadBody?.classList.add("hidden");
    } else {
      importBody?.classList.add("hidden");
      kicadBody?.classList.remove("hidden");
    }
  });
}

// ── Render ──────────────────────────────────────────────────────────────

function render() {
  if (!body) return;
  const projects = App.kicadProjects;
  const paths = Object.keys(projects);

  if (paths.length === 0 && !expandedProject) {
    body.innerHTML = `
      <div class="kicad-toolbar">
        <button class="rebuild-btn" id="kicad-add-project">Add Project</button>
      </div>
      <div class="kicad-empty">No KiCad projects registered.<br>Click "Add Project" to scan a KiCad project folder.</div>
    `;
    document.getElementById("kicad-add-project")?.addEventListener("click", addProject);
    return;
  }

  let html = `<div class="kicad-toolbar">
    <button class="rebuild-btn" id="kicad-add-project">Add Project</button>
  </div>`;

  // Project list
  for (const path of paths) {
    const proj = projects[path];
    const isExpanded = path === expandedProject;
    html += `
      <div class="kicad-project-item${isExpanded ? ' expanded' : ''}" data-path="${escHtml(path)}">
        <div class="kicad-project-name">${escHtml(proj.name)}</div>
        <div class="kicad-project-path">${escHtml(path)}</div>
        <div class="kicad-project-meta">
          <span>Last scan: ${proj.last_scan ? new Date(proj.last_scan).toLocaleString() : 'never'}</span>
        </div>
        <div class="kicad-project-actions">
          <button class="adj-btn kicad-rescan" data-path="${escHtml(path)}">Rescan</button>
          <button class="adj-btn kicad-autolink" data-path="${escHtml(path)}">Auto-link</button>
          <button class="adj-btn kicad-load-bom" data-path="${escHtml(path)}">Load as BOM</button>
          <button class="adj-btn kicad-fetch-fps" data-path="${escHtml(path)}">Fetch Footprints</button>
          <button class="adj-btn kicad-remove" data-path="${escHtml(path)}" style="margin-left:auto">Remove</button>
        </div>
      </div>
    `;

    if (isExpanded && expandedParts.length > 0) {
      html += renderPartsTable(expandedParts);
    }
  }

  body.innerHTML = html;
  wireEventListeners();
}

function renderPartsTable(parts) {
  const links = App.partLinks;

  // Summary counts
  let linked = 0, autoLinkable = 0, unlinked = 0, dnpCount = 0;
  for (const p of parts) {
    if (p.dnp) { dnpCount++; continue; }
    const linkKey = p.lcsc || p.mpn;
    if (linkKey && links[linkKey]) { linked++; }
    else if (p.lcsc || p.mpn) { autoLinkable++; }
    else { unlinked++; }
  }

  let html = `<div class="kicad-summary-bar">
    <span class="chip green">${linked} linked</span>
    <span class="chip yellow">${autoLinkable} linkable</span>
    <span class="chip red">${unlinked} unlinked</span>
    ${dnpCount ? `<span class="chip grey">${dnpCount} DNP</span>` : ''}
  </div>`;

  html += '<div class="kicad-parts-table"><table><thead><tr>';
  html += '<th style="width:60px">Ref</th>';
  html += '<th style="width:80px">Value</th>';
  html += '<th style="width:80px">Footprint</th>';
  html += '<th style="width:70px">LCSC</th>';
  html += '<th style="width:24px">Link</th>';
  html += '<th>Actions</th>';
  html += '</tr></thead><tbody>';

  for (const p of parts) {
    const linkKey = p.lcsc || p.mpn;
    const linkedTo = linkKey ? links[linkKey] : null;
    const rowClass = p.dnp ? ' class="row-dnp"' : linkedTo ? ' class="row-green"' : linkKey ? '' : ' class="row-red"';

    let dotClass = "unlinked";
    if (linkedTo) dotClass = "linked";
    else if (linkKey) dotClass = "auto-linkable";

    html += `<tr${rowClass}>`;
    html += `<td class="mono">${escHtml(p.ref)}</td>`;
    html += `<td>${escHtml(p.value)}</td>`;
    html += `<td class="mono" style="font-size:10px">${escHtml(p.footprint)}</td>`;
    html += `<td class="mono" style="font-size:10px">${escHtml(p.lcsc || p.mpn || '')}</td>`;
    html += `<td><span class="link-dot ${dotClass}" title="${linkedTo ? 'Linked: ' + linkedTo : dotClass}"></span></td>`;
    html += '<td class="btn-group">';
    if (!linkedTo && linkKey) {
      html += `<button class="adj-btn kicad-link-part" data-source="${escHtml(linkKey)}">Link</button>`;
    }
    if (p.lcsc || (linkedTo && linkedTo.match(/^C\d+$/))) {
      const lcsc = p.lcsc || linkedTo;
      html += `<button class="openpnp-badge kicad-openpnp-btn" data-lcsc="${escHtml(lcsc)}" data-part-key="${escHtml(linkedTo || lcsc)}">PnP</button>`;
    }
    html += '</td></tr>';
  }

  html += '</tbody></table></div>';
  return html;
}

// ── Event listeners ────────────────────────────────────────────────────

function wireEventListeners() {
  document.getElementById("kicad-add-project")?.addEventListener("click", addProject);

  /** @param {string} sel @param {function(HTMLElement):void} fn */
  const eachBtn = (sel, fn) => body?.querySelectorAll(sel).forEach(el => fn(/** @type {HTMLElement} */ (el)));

  eachBtn(".kicad-project-item", (el) => {
    el.addEventListener("click", (e) => {
      if (/** @type {HTMLElement} */ (e.target).closest("button")) return;
      const path = el.dataset.path;
      if (expandedProject === path) {
        expandedProject = null;
        expandedParts = [];
      } else {
        expandProject(path);
      }
      render();
    });
  });

  eachBtn(".kicad-rescan", (btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      await rescanProject(btn.dataset.path);
    });
  });

  eachBtn(".kicad-autolink", (btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      await autoLinkProject(btn.dataset.path);
    });
  });

  eachBtn(".kicad-load-bom", (btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      await loadAsBom(btn.dataset.path);
    });
  });

  eachBtn(".kicad-fetch-fps", (btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      await fetchAllFootprints(btn.dataset.path);
    });
  });

  eachBtn(".kicad-remove", (btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      await removeProject(btn.dataset.path);
    });
  });

  eachBtn(".kicad-link-part", (btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const source = btn.dataset.source;
      await linkPartPrompt(source);
    });
  });

  eachBtn(".kicad-openpnp-btn", (btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const partKey = btn.dataset.partKey;
      const lcsc = btn.dataset.lcsc;
      EventBus.emit(Events.OPENPNP_PARTS_CHANGED, { action: "open", partKey, lcsc });
    });
  });
}

// ── Actions ────────────────────────────────────────────────────────────

async function addProject() {
  AppLog.info("Opening folder dialog for KiCad project...");
  const result = await api("open_kicad_project_dialog");
  if (!result) return;
  await loadKicadState();
  expandedProject = null;
  // Find the path from projects
  for (const [path, proj] of Object.entries(App.kicadProjects)) {
    if (proj.name === result.name) {
      expandedProject = path;
      expandedParts = result.parts || [];
      break;
    }
  }
  render();
  showToast(`Added KiCad project: ${result.name}`);
  AppLog.info(`Scanned ${result.parts?.length || 0} parts from ${result.name}`);
}

async function expandProject(path) {
  if (!path) return;
  const result = await api("scan_kicad_project", path);
  if (!result) return;
  expandedProject = path;
  expandedParts = result.parts || [];
}

async function rescanProject(path) {
  const result = await api("scan_kicad_project", path);
  if (!result) return;
  await loadKicadState();
  expandedProject = path;
  expandedParts = result.parts || [];
  render();
  showToast(`Rescanned: ${result.parts?.length || 0} parts`);
  AppLog.info(`Rescanned ${result.name}: ${result.parts?.length || 0} parts`);
}

async function autoLinkProject(path) {
  const result = await api("auto_link_kicad_parts", path);
  if (!result) return;
  App.partLinks = result.links?.links || {};
  EventBus.emit(Events.PART_LINKS_CHANGED);
  // Re-expand to show updated links
  if (expandedProject === path) {
    const scan = await api("scan_kicad_project", path);
    if (scan) expandedParts = scan.parts || [];
  }
  render();
  showToast(`Auto-linked ${result.linked} parts`);
  AppLog.info(`Auto-linked ${result.linked} parts`);
}

async function loadAsBom(path) {
  const result = await api("scan_kicad_project", path);
  if (!result || !result.parts) return;

  // Convert KiCad parts to CSV-like format for the BOM pipeline
  const parts = result.parts.filter(p => !p.dnp);
  const csvLines = [
    ["Reference", "Value", "Footprint", "LCSC Part Number", "Quantity", "DNP"].join(","),
  ];
  for (const p of parts) {
    csvLines.push([p.ref, p.value, p.footprint, p.lcsc || p.mpn, "1", p.dnp ? "1" : "0"].join(","));
  }

  const csvText = csvLines.join("\n");
  const bomData = processBOM(csvText);
  if (!bomData) {
    AppLog.error("Failed to process KiCad BOM");
    return;
  }

  const results = matchBOM(bomData, App.inventory, App.links);
  App.bomResults = results;
  App.bomFileName = result.name + " (KiCad)";
  App.bomDirty = false;
  EventBus.emit(Events.BOM_LOADED, { results, fileName: App.bomFileName });
  showToast(`Loaded ${parts.length} parts as BOM from ${result.name}`);
  AppLog.info(`Loaded KiCad BOM: ${parts.length} parts`);
}

async function fetchAllFootprints(path) {
  const result = await api("scan_kicad_project", path);
  if (!result) return;
  const parts = result.parts.filter(p => p.lcsc && !p.dnp);
  if (parts.length === 0) {
    showToast("No LCSC parts to fetch footprints for");
    return;
  }

  AppLog.info(`Fetching footprints for ${parts.length} parts...`);
  let fetched = 0, failed = 0;
  const openpnp = await api("get_openpnp_parts");
  const existing = openpnp?.parts || {};

  for (const p of parts) {
    const partKey = p.lcsc;
    // Skip if already has footprint
    if (existing[partKey]?.footprint) {
      fetched++;
      continue;
    }

    // Try KiCad library first
    if (p.footprint_full) {
      const kicadFp = await api("fetch_kicad_footprint", p.footprint_full);
      if (kicadFp) {
        await api("save_openpnp_part", JSON.stringify({
          part_key: partKey,
          openpnp_id: p.footprint + "-" + p.value,
          package_id: p.footprint,
          height: 0, speed: 1.0, nozzle_tips: [],
          footprint: kicadFp,
          footprint_source: "kicad",
          footprint_fetched: new Date().toISOString(),
        }));
        fetched++;
        continue;
      }
    }

    // Fallback: EasyEDA
    const fp = await api("fetch_footprint", partKey);
    if (fp) {
      await api("save_openpnp_part", JSON.stringify({
        part_key: partKey,
        openpnp_id: p.footprint + "-" + p.value,
        package_id: p.footprint,
        height: 0, speed: 1.0, nozzle_tips: [],
        footprint: fp,
        footprint_source: "easyeda",
        footprint_fetched: new Date().toISOString(),
      }));
      fetched++;
    } else {
      failed++;
      AppLog.warn(`Failed to fetch footprint for ${partKey}`);
    }
  }

  await loadKicadState();
  render();
  showToast(`Fetched ${fetched} footprints, ${failed} failed`);
  AppLog.info(`Footprint fetch complete: ${fetched} ok, ${failed} failed`);
}

async function removeProject(path) {
  await api("remove_kicad_project", path);
  await loadKicadState();
  if (expandedProject === path) {
    expandedProject = null;
    expandedParts = [];
  }
  render();
  showToast("Project removed");
}

async function linkPartPrompt(sourceId) {
  // Simple prompt — link to an inventory part by searching
  const partKey = prompt(`Link "${sourceId}" to inventory part key (LCSC# or MPN):`);
  if (!partKey) return;
  const result = await api("save_part_link", sourceId, partKey.trim());
  if (result) {
    App.partLinks = result.links || {};
    EventBus.emit(Events.PART_LINKS_CHANGED);
    render();
    showToast(`Linked ${sourceId} → ${partKey.trim()}`);
  }
}

// ── Init ───────────────────────────────────────────────────────────────

EventBus.on(Events.PART_LINKS_CHANGED, render);
EventBus.on(Events.KICAD_PROJECT_LOADED, render);

render();

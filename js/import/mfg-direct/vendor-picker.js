/* vendor-picker.js — shared vendor-selection logic for the mfg-direct import
   flows (the inline editor in mfg-direct-panel.js and the OCR-overlay modal in
   ocr-overlay-panel.js).

   Both call sites need the SAME behavior:
     - selectPseudoVendor: pick a built-in pseudo-vendor (Self/Salvage/Unknown)
     - onVendorNameBlur:    upsert by name, case-insensitive existing lookup,
                            carry a pending URL through (skipping pseudo types)
     - onVendorUrlBlur:     canonicalize, stash locally when no record yet,
                            ignore clears
   …but they bind to different storage (mfg-direct mutates `state.vendor`, the
   overlay mutates a module-level `vendor`) and render different markup with
   different element ids/classes. This module owns the behavior; each call site
   keeps its own markup and supplies a getVendor/setVendor/onChange context. */

import { apiVendors } from '../../api.js';
import { escHtml, vendorIconSrc } from '../../ui-helpers.js';
import { store } from '../../store.js';
import { canonicalizeUrl } from './mfg-direct-logic.js';

/** True for built-in pseudo-vendors that don't carry a website/favicon. */
export function isPseudoVendor(v) {
  const type = v && v.type;
  return type === 'self' || type === 'salvage' || type === 'unknown';
}

/** Favicon markup for a vendor: emoji icon, fetched favicon, or empty slot. */
export function vendorFaviconHtml(vendor) {
  if (vendor.icon) {
    return `<span class="vendor-favicon-emoji">${escHtml(vendor.icon)}</span>`;
  }
  if (vendor.favicon_path) {
    return `<img class="vendor-favicon" src="${escHtml(vendorIconSrc(vendor.favicon_path))}" alt="">`;
  }
  return `<span class="vendor-favicon-empty"></span>`;
}

/**
 * Create a vendor picker bound to a get/set context. The returned handlers are
 * identical in behavior across call sites — only the storage (getVendor/setVendor)
 * and the re-render hook (onChange) differ.
 * @param {{ getVendor: () => Object, setVendor: (v: Object) => void, onChange?: () => void }} ctx
 */
export function createVendorPicker({ getVendor, setVendor, onChange }) {
  const fire = () => { if (onChange) onChange(); };

  function selectPseudoVendor(id) {
    const v = (store.vendors || []).find(x => x.id === id);
    if (!v) return;
    setVendor({ ...v });
    fire();
  }

  async function onVendorNameBlur(text) {
    const trimmed = (text || '').trim();
    if (!trimmed) return;
    const vendor = getVendor();
    // Already pointing at this vendor — nothing to do.
    if (vendor.id && vendor.name.toLowerCase() === trimmed.toLowerCase()) return;

    // If the user typed a URL before naming the vendor, carry it through.
    const pendingUrl = vendor.url || '';

    const existing = (store.vendors || []).find(
      v => v.name.toLowerCase() === trimmed.toLowerCase());
    if (existing) {
      let next = { ...existing };
      if (pendingUrl && !next.url && !isPseudoVendor(next)) {
        const v = await apiVendors.upsert(next.id, '', pendingUrl);
        if (v) next = { ...v };
      }
      setVendor(next);
    } else {
      const v = await apiVendors.upsert('', trimmed, pendingUrl);
      if (!v) return;
      setVendor({ ...v });
    }
    fire();
  }

  async function onVendorUrlBlur(text) {
    const canonical = canonicalizeUrl(text || '');
    const vendor = getVendor();
    if (!vendor.id) {
      // No backend record yet — keep the URL locally so onVendorNameBlur picks it up.
      setVendor({ ...vendor, url: canonical });
      return;
    }
    if (canonical === (vendor.url || '')) return;
    if (!canonical) return;  // ignore clears here; vendor flyout handles deletes
    const v = await apiVendors.upsert(vendor.id, '', canonical);
    if (!v) return;
    setVendor({ ...v });
    fire();
  }

  return { selectPseudoVendor, onVendorNameBlur, onVendorUrlBlur };
}

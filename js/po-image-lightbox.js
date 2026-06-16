// @ts-check
/* po-image-lightbox.js — Full-screen blow-up for a PO's source image.
 *
 * The PO picker shows a small thumbnail of an order's archived source file
 * (image, or first page of a PDF rasterized by the backend). Clicking the
 * thumbnail calls openPoImageLightbox() with the same data: URI to show it
 * full-size over a dimmed backdrop. Closes on backdrop click or Escape.
 *
 * Reuses the shared .modal-overlay styling; the overlay element lives in
 * index.html (#po-image-modal). Listeners are wired lazily on first open so
 * no separate init() call is required from app-init.
 */

/** @type {HTMLElement|null} */
let overlay = null;
/** @type {HTMLImageElement|null} */
let imgEl = null;
let wired = false;

function ensureWired() {
  if (wired) return;
  overlay = document.getElementById("po-image-modal");
  if (!overlay) return; // not on this page
  imgEl = /** @type {HTMLImageElement|null} */ (
    overlay.querySelector(".po-image-modal-img")
  );
  // Clicking the dimmed backdrop (but not the image itself) closes.
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) closePoImageLightbox();
  });
  // Escape closes while the lightbox is open.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && overlay && !overlay.classList.contains("hidden")) {
      closePoImageLightbox();
    }
  });
  wired = true;
}

/**
 * Show an image full-size over a dimmed backdrop.
 * @param {string} dataUri  a `data:` URI (image/png|jpeg|gif)
 * @param {string} [alt]    accessible label
 */
export function openPoImageLightbox(dataUri, alt) {
  ensureWired();
  if (!overlay || !imgEl || !dataUri) return;
  imgEl.src = dataUri;
  imgEl.alt = alt || "Purchase order source";
  overlay.classList.remove("hidden");
}

export function closePoImageLightbox() {
  if (!overlay || !imgEl) return;
  overlay.classList.add("hidden");
  imgEl.removeAttribute("src"); // free the (potentially large) data URI
}

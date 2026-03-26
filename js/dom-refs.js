// @ts-check
/* dom-refs.js — Centralized lazy element lookups for stable HTML elements.
   Use these instead of scattered getElementById calls.
   Dynamic elements (created by renderers) should use querySelector locally. */

/** @type {Record<string, HTMLElement>} */
export const dom = {
  // Header
  get prefsBtn()       { return document.getElementById("prefs-btn"); },
  get globalUndo()     { return document.getElementById("global-undo"); },
  get globalRedo()     { return document.getElementById("global-redo"); },
  get invCount()       { return document.getElementById("inv-count"); },
  get invTotalValue()  { return document.getElementById("inv-total-value"); },

  // Panels (outer containers)
  get panelImport()    { return document.getElementById("panel-import"); },
  get panelInventory() { return document.getElementById("panel-inventory"); },
  get panelBom()       { return document.getElementById("panel-bom"); },

  // Import panel
  get importBody()     { return document.getElementById("import-body"); },
  get importDropZone() { return document.getElementById("import-drop-zone"); },
  get importFileInput(){ return document.getElementById("import-file-input"); },
  get newPoRow()       { return document.getElementById("new-po-row"); },
  get importMapper()   { return document.getElementById("import-mapper"); },

  // Inventory panel
  get inventoryBody()  { return document.getElementById("inventory-body"); },
  get invSearch()      { return document.getElementById("inv-search"); },
  get rebuildInv()     { return document.getElementById("rebuild-inv"); },

  // Console
  get consoleLog()     { return document.getElementById("console-log"); },
  get consoleClear()   { return document.getElementById("console-clear"); },
  get consoleEntries() { return document.getElementById("console-entries"); },

  // BOM panel
  get bomBody()        { return document.getElementById("bom-body"); },
  get bomDropZone()    { return document.getElementById("bom-drop-zone"); },
  get bomFileInput()   { return document.getElementById("bom-file-input"); },
  get bomResults()     { return document.getElementById("bom-results"); },
  get bomSummary()     { return document.getElementById("bom-summary"); },
  get bomQtyMult()     { return document.getElementById("bom-qty-mult"); },
  get bomSaveBtn()     { return document.getElementById("bom-save-btn"); },
  get bomConsumeBtn()  { return document.getElementById("bom-consume-btn"); },
  get bomClearBtn()    { return document.getElementById("bom-clear-btn"); },
  get bomPriceInfo()   { return document.getElementById("bom-price-info"); },
  get bomStagingTitle(){ return document.getElementById("bom-staging-title"); },
  get bomThead()       { return document.getElementById("bom-thead"); },
  get bomTbody()       { return document.getElementById("bom-tbody"); },

  // Adjust modal
  get adjustModal()    { return document.getElementById("adjust-modal"); },
  get modalTitle()     { return document.getElementById("modal-title"); },
  get modalDetailTable() { return document.getElementById("modal-detail-table"); },
  get adjType()        { return document.getElementById("adj-type"); },
  get adjQty()         { return document.getElementById("adj-qty"); },
  get adjNote()        { return document.getElementById("adj-note"); },
  get adjUnitPrice()   { return document.getElementById("adj-unit-price"); },
  get adjExtPrice()    { return document.getElementById("adj-ext-price"); },
  get adjCancel()      { return document.getElementById("adj-cancel"); },
  get adjApply()       { return document.getElementById("adj-apply"); },

  // Consume modal
  get consumeModal()   { return document.getElementById("consume-modal"); },
  get consumeTitle()   { return document.getElementById("consume-title"); },
  get consumeSubtitle(){ return document.getElementById("consume-subtitle"); },
  get consumeNote()    { return document.getElementById("consume-note"); },
  get consumeCancel()  { return document.getElementById("consume-cancel"); },
  get consumeConfirm() { return document.getElementById("consume-confirm"); },

  // Preferences modal
  get prefsModal()     { return document.getElementById("prefs-modal"); },
  get prefsSliders()   { return document.getElementById("prefs-sliders"); },
  get prefsCancel()    { return document.getElementById("prefs-cancel"); },
  get prefsSave()      { return document.getElementById("prefs-save"); },
  get dkStatus()       { return document.getElementById("dk-status"); },
  get dkLogin()        { return document.getElementById("dk-login"); },
  get dkLogout()       { return document.getElementById("dk-logout"); },

  // Price modal
  get priceModal()     { return document.getElementById("price-modal"); },
  get priceModalTitle(){ return document.getElementById("price-modal-title"); },
  get priceModalSubtitle() { return document.getElementById("price-modal-subtitle"); },
  get priceUnit()      { return document.getElementById("price-unit"); },
  get priceExt()       { return document.getElementById("price-ext"); },
  get priceCancel()    { return document.getElementById("price-cancel"); },
  get priceApply()     { return document.getElementById("price-apply"); },

  // Close modal
  get closeModal()     { return document.getElementById("close-modal"); },
  get closeCancel()    { return document.getElementById("close-cancel"); },
  get closeDiscard()   { return document.getElementById("close-discard"); },
  get closeSave()      { return document.getElementById("close-save"); },

  // Toast
  get toast()          { return document.getElementById("toast"); },
};

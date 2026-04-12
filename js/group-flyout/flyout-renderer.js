// @ts-check
/* flyout-renderer.js — HTML string generation for the group flyout panel.
   No DOM, no store, no events. All data passed as parameters. */

import { escHtml } from '../ui-helpers.js';
import { detectConflicts } from './flyout-logic.js';

/**
 * @typedef {import('./flyout-logic.js').Tag} Tag
 */

/**
 * Render the saved-search tabs strip (vertical, left edge).
 *
 * Always includes a "Live" tab (id = "") plus one tab per saved search.
 *
 * @param {Array<{ id: string, name: string }>} savedSearches
 * @param {string | null} activeSavedSearchId - null means live (unsaved)
 * @returns {string}
 */
function renderSavedTabs(savedSearches, activeSavedSearchId) {
  var html = '<div class="flyout-saved-tabs">';

  // "Live" tab — always first
  var liveActive = (activeSavedSearchId === null || activeSavedSearchId === "");
  html += '<button class="flyout-saved-tab' + (liveActive ? ' tab-active' : '') + '" data-search-id="">Live</button>';

  for (var i = 0; i < savedSearches.length; i++) {
    var s = savedSearches[i];
    var isActive = (s.id === activeSavedSearchId);
    html += '<button class="flyout-saved-tab' + (isActive ? ' tab-active' : '') + '" data-search-id="' + escHtml(s.id) + '">' + escHtml(s.name) + '</button>';
  }

  html += '</div>';
  return html;
}

/**
 * Render the flyout header: drag handle, title, save button, close button.
 *
 * @param {string} groupName
 * @returns {string}
 */
function renderHeader(groupName) {
  return '<div class="flyout-header">' +
    '<span class="flyout-drag-handle">\u2261</span>' +
    '<span class="flyout-title">' + escHtml(groupName) + '</span>' +
    '<button class="flyout-save-btn" title="Save search">\u2605</button>' +
    '<button class="flyout-close-btn" title="Close">\u00D7</button>' +
    '</div>';
}

/**
 * Render the tag bar: pill buttons for each tag.
 * Enabled tags get .tag-enabled; conflicting tags also get .tag-conflict.
 *
 * @param {Tag[]} tags
 * @returns {string}
 */
function renderTagBar(tags) {
  if (!tags || tags.length === 0) return '';

  var conflicts = detectConflicts(tags);

  var html = '<div class="flyout-tags">';
  for (var i = 0; i < tags.length; i++) {
    var tag = tags[i];
    var cls = 'flyout-tag';
    if (tag.enabled) cls += ' tag-enabled';
    if (conflicts.indexOf(tag.label) !== -1) cls += ' tag-conflict';
    html += '<button class="' + cls + '" data-dim="' + escHtml(tag.dimension) + '" data-label="' + escHtml(tag.label) + '">' + escHtml(tag.label) + '</button>';
  }
  html += '</div>';
  return html;
}

/**
 * Render the search bar: text input and promote button.
 *
 * @param {string} searchText
 * @returns {string}
 */
function renderSearchBar(searchText) {
  return '<div class="flyout-search">' +
    '<input class="flyout-search-input" type="text" placeholder="Search members\u2026" value="' + escHtml(searchText || '') + '">' +
    '<button class="flyout-promote-btn" title="Promote to tag">\u2191</button>' +
    '</div>';
}

/**
 * Render a single member row.
 *
 * @param {{ part_id?: string, preferred?: boolean, qty?: number, description?: string }} member
 * @returns {string}
 */
function renderMemberRow(member) {
  var partId = member.part_id || '';
  var preferred = !!member.preferred;
  var qty = (member.qty !== undefined && member.qty !== null) ? member.qty : 0;
  var desc = member.description || '';

  return '<div class="flyout-member" data-part-id="' + escHtml(partId) + '" draggable="true">' +
    '<span class="flyout-member-grip">\u2807</span>' +
    '<span class="flyout-member-id mono">' + escHtml(partId) + '</span>' +
    '<span class="flyout-preferred">' + (preferred ? '\u2605' : '') + '</span>' +
    '<span class="flyout-member-qty">' + qty + '</span>' +
    '<span class="flyout-member-desc">' + escHtml(desc) + '</span>' +
    '</div>';
}

/**
 * Render the scrollable member list.
 *
 * @param {Array<{ part_id?: string, preferred?: boolean, qty?: number, description?: string }>} filteredMembers
 * @returns {string}
 */
function renderMemberList(filteredMembers) {
  var html = '<div class="flyout-members">';
  if (!filteredMembers || filteredMembers.length === 0) {
    html += '<div class="flyout-empty">No members match</div>';
  } else {
    for (var i = 0; i < filteredMembers.length; i++) {
      html += renderMemberRow(filteredMembers[i]);
    }
  }
  html += '</div>';
  return html;
}

/**
 * Render the footer: member count + total stock.
 *
 * @param {number} memberCount
 * @param {number} totalStock
 * @returns {string}
 */
function renderFooter(memberCount, totalStock) {
  return '<div class="flyout-footer">' +
    memberCount + ' member' + (memberCount === 1 ? '' : 's') +
    ' \u00B7 ' +
    totalStock + ' in stock' +
    '</div>';
}

/**
 * Render the complete flyout HTML string.
 *
 * @param {{
 *   genericPartId: string,
 *   groupName: string,
 *   tags: Tag[],
 *   filteredMembers: Array<{ part_id?: string, preferred?: boolean, qty?: number, description?: string }>,
 *   totalStock: number,
 *   searchText: string,
 *   isActive: boolean,
 *   frozen: boolean,
 *   savedSearches: Array<{ id: string, name: string }>,
 *   activeSavedSearchId: string | null,
 * }} data
 * @returns {string}
 */
export function renderFlyout(data) {
  var cls = 'group-flyout';
  if (data.isActive) cls += ' flyout-active';
  if (data.frozen) cls += ' flyout-frozen';

  var memberCount = data.filteredMembers ? data.filteredMembers.length : 0;

  var html = '<div class="' + cls + '" data-gp-id="' + escHtml(data.genericPartId) + '">';

  // Saved search tabs — vertical strip on left edge
  html += renderSavedTabs(data.savedSearches || [], data.activeSavedSearchId);

  // Main content area
  html += '<div class="flyout-main">';
  html += renderHeader(data.groupName);
  html += renderTagBar(data.tags);
  html += renderSearchBar(data.searchText);
  html += renderMemberList(data.filteredMembers);
  html += renderFooter(memberCount, data.totalStock || 0);
  html += '</div>'; // .flyout-main

  html += '</div>'; // .group-flyout
  return html;
}

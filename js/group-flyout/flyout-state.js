// @ts-check
/* flyout-state.js — Mutable state for open flyouts */

/**
 * @typedef {import('./flyout-logic.js').Tag} Tag
 */

/**
 * @typedef {{
 *   genericPartId: string,
 *   groupName: string,
 *   tags: Tag[],
 *   searchText: string,
 *   allMembers: any[],
 *   el: HTMLElement | null,
 *   sourceRowEl: HTMLElement | null,
 *   frozen: boolean,
 *   frozenMemberIds: string[] | null,
 *   savedSearchId: string | null,
 *   savedSearches: { id: string, name: string }[],
 * }} FlyoutInstance
 */

/** @type {Map<string, FlyoutInstance>} keyed by genericPartId */
export var flyouts = new Map();

/** @type {string | null} */
export var activeFlyoutId = null;

/** @param {string | null} id */
export function setActiveFlyoutId(id) {
  activeFlyoutId = id;
}

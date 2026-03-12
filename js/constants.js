/* constants.js — Shared constants loaded from data/constants.json */

const resp = await fetch('data/constants.json');
if (!resp.ok) throw new Error('Failed to load data/constants.json: ' + resp.status);
const _data = await resp.json();

export const SECTION_ORDER = _data.SECTION_ORDER;
export const FIELDNAMES = _data.FIELDNAMES;

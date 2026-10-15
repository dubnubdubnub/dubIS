// @ts-check
/* js/category-match.js — Does a search term NAME a category? Pure: no DOM, no
   store, no imports, so both halves of the app that need it (the inventory
   grid's section buckets and the BOM table's rows) can consult it without
   importing each other.

   The feature: typing a category name into the inventory search box shows the
   whole category, not only the rows whose text happens to contain the term.
   "Diodes" is the motivating case — every Diodes row's description reads
   "Diode 100V 2A Surface Mount", so a text search for the plural returned
   nothing at all.

   The rule, and why it is this particular rule:

     the query's tokens must appear as a CONTIGUOUS RUN inside the section's
     tokens.

   - Contiguous, so "power regulators" does NOT name "ICs - Power / Voltage
     Regulators": the term is a phrase, not a bag of words. A bag-of-words match
     would let any two common words light up a category.
   - Whole tokens, so "ic" does NOT name "ICs - Microcontrollers" and "switch"
     does NOT name "Switches". This is the whole reason the match is not
     `section.includes(query)`: two letters would then light up nine IC sections
     (and "ic" is a substring of "Switches" too), i.e. the toggle would swallow
     the grid for the shortest, most common keystrokes. Substring matching is
     what the text haystack already does; doing it again here adds only damage.
   - No singular/plural folding, so "capacitor" does NOT name "Capacitors".
     Folding an "s" is what turns "ics" back into "ic" and re-opens the bug
     above — the two requirements are in genuine tension, and the over-match
     loses. The plain text search still finds capacitor rows through their
     descriptions, so a singular term is a narrower result, never an empty one.
   - A parent name names its children, because the child key literally carries
     the parent's tokens ("connectors" is a token of "Connectors > SMD"). That is
     not a special case, it is the hierarchy falling out of the rule — and it is
     what makes typing "Connectors" show the whole family rather than only the
     ungrouped rows sitting directly under the parent.

   Both implementations of this rule (here and domain/category_match.py, used by
   `dubis search --category-hit`) are pinned by the same case table,
   tests/fixtures/category-name/cases.json, so the grid and the CLI cannot drift
   apart. */

/**
 * The alphanumeric tokens of a category name or of a search term, lowercased.
 *
 * Splitting on everything that is not a letter or digit is what lets one rule
 * cover the whole section vocabulary in data/constants.json: "Passives -
 * Capacitors", "ICs - Power / Voltage Regulators", "Connectors > SMD" and
 * "Development Boards, Kits, Programmers" all reduce to plain words, with the
 * dash, slash, ampersand, comma and " > " gone.
 *
 * @param {any} text
 * @returns {string[]}
 */
export function nameTokens(text) {
  return String(text === undefined || text === null ? "" : text)
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter(function (t) { return t.length > 0; });
}

/**
 * Does `query` NAME `section`?
 *
 * @param {string} query - a search term (case is not significant)
 * @param {string} section - a section key like "Passives - Capacitors > MLCC",
 *   or "" for an unsectioned bucket — which names nothing, and never matches
 * @returns {boolean}
 */
export function matchesCategoryName(query, section) {
  var needle = nameTokens(query);
  var hay = nameTokens(section);
  if (needle.length === 0 || hay.length === 0) return false;
  for (var i = 0; i + needle.length <= hay.length; i++) {
    var hit = true;
    for (var j = 0; j < needle.length; j++) {
      if (hay[i + j] !== needle[j]) { hit = false; break; }
    }
    if (hit) return true;
  }
  return false;
}

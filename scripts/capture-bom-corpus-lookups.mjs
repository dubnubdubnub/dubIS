#!/usr/bin/env node
/* capture-bom-corpus-lookups.mjs — record what LCSC says about a BOM's codes.

   Builds the `lcsc-lookups.json` half of a tests/fixtures/bom-match-corpus/
   case: every LCSC code in the BOM, asked of a running dubIS hub over the same
   `GET /v1/distributors/lcsc/product/{code}` route the app's spec lookup uses
   (js/bom/bom-spec-lookup.js), trimmed to the fields matching reads. Prices,
   stock and images are dropped — the corpus is about what a part IS.

   Read-only: that route records nothing (record_fetched_prices is a separate
   call the app makes on purpose, and this script never makes it).

     node scripts/capture-bom-corpus-lookups.mjs <bom.csv> [--out <file>] [--hub <url>]

   Hub: --hub, else $DUBIS_URL, else data/.v1_port. Default out: lcsc-lookups.json
   beside the BOM. An unknown code is recorded as a not-found answer; any other
   failure aborts, because a corpus with a hole in it would test the hole. */

import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { processBOM } from '../js/csv-parser.js';

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), '..');

function usage(msg) {
  if (msg) console.error('error: ' + msg);
  console.error('usage: node scripts/capture-bom-corpus-lookups.mjs <bom.csv> [--out <file>] [--hub <url>]');
  process.exit(2);
}

function parseArgs(argv) {
  const out = { bom: null, out: null, hub: null };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--out') out.out = argv[++i];
    else if (a === '--hub') out.hub = argv[++i];
    else if (a.startsWith('--')) usage('unknown flag ' + a);
    else if (!out.bom) out.bom = a;
    else usage('unexpected argument ' + a);
  }
  if (!out.bom) usage('a BOM CSV is required');
  return out;
}

function hubUrl(flag) {
  if (flag) return flag.replace(/\/$/, '');
  if (process.env.DUBIS_URL) return process.env.DUBIS_URL.replace(/\/$/, '');
  let port;
  try {
    port = readFileSync(join(REPO, 'data', '.v1_port'), 'utf8').trim();
  } catch {
    usage('no hub: pass --hub, set DUBIS_URL, or start dubIS (data/.v1_port)');
  }
  return 'http://127.0.0.1:' + port;
}

/** Same projection the corpus test hands the lookup — keep in step with specFromProduct. */
function trim(product) {
  const keep = ['productCode', 'mpn', 'manufacturer', 'description', 'package', 'category', 'subcategory'];
  const out = {};
  for (const k of keep) out[k] = typeof product[k] === 'string' ? product[k] : '';
  out.attributes = Array.isArray(product.attributes)
    ? product.attributes.map((a) => ({ name: String(a.name), value: String(a.value ?? '') }))
    : [];
  return out;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const bomPath = resolve(args.bom);
  const parsed = processBOM(readFileSync(bomPath, 'utf8'), bomPath);
  if (!parsed) usage('could not parse ' + bomPath);
  const codes = [...new Set([...parsed.aggregated.values()].map((b) => b.lcsc).filter((c) => /^C\d+$/.test(c)))];
  const hub = hubUrl(args.hub);

  const lookups = {};
  for (const code of codes) {
    const res = await fetch(hub + '/v1/distributors/lcsc/product/' + encodeURIComponent(code));
    const body = await res.json().catch(() => null);
    if (res.ok && body) {
      lookups[code] = { product: trim(body) };
    } else if (res.status === 404 && body && body.code === 'product_not_found') {
      lookups[code] = { error: { status: 404, code: 'product_not_found' } };
    } else {
      throw new Error(code + ': HTTP ' + res.status + ' ' + JSON.stringify(body));
    }
    console.error(code + ': ' + (lookups[code].product ? lookups[code].product.description : 'not found'));
  }
  const outPath = args.out ? resolve(args.out) : join(dirname(bomPath), 'lcsc-lookups.json');
  writeFileSync(outPath, JSON.stringify(lookups, null, 2) + '\n');
  console.error('wrote ' + Object.keys(lookups).length + ' lookups to ' + outPath);
}

main().catch((e) => { console.error(e.message || e); process.exit(3); });

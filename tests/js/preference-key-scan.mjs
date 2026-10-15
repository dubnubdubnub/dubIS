/* tests/js/preference-key-scan.mjs — parser-based enumeration of preference keys.

   Why a parser: the guard in tests/js/preference-keys.test.js enumerated keys
   with regexes, and an audit confirmed three conventional-looking ways to add a
   key that evade every regex — destructuring the loader's payload
   (`const { foo } = stored`), bracket-literal writes (`preferences["foo"] = …`),
   and `Object.assign(preferences, …)`. Regexes were the bug. This module parses
   with acorn (the parser eslint itself is built on) and, more importantly,
   RAISES when it meets the preferences bag (or the loader's `stored` payload)
   in a construct it cannot account for. Silent evasion is the failure mode this
   exists to kill: a scan that quietly ignores what it cannot parse is worse
   than no scan, because it reads as a check.

   What is accounted for, per reference form:
   - `bag.foo = …`, `bag["foo"] = …`, `bag.foo[x] = …` (first key wins),
     `++bag.foo`, `delete bag.foo`, destructuring assignment targets;
   - `Object.assign(bag, { foo: … })` — literal keys are writes; a non-literal
     source or a spread is a loud failure, never a shrug;
   - `Object.defineProperty(bag, 'foo', …)`;
   - whole-bag replacement `preferences = { … }` — literal keys are writes, a
     spread of the bag itself is a copy, and a spread of a PARAMETER registers
     the enclosing function as a whole-bag sink whose call sites must pass
     object literals (that is how `setPreferences(prefs)` stays honest);
   - reads of the payload in loadPreferences: `stored.foo`, `stored["foo"]`,
     `hasOwnProperty.call(stored, 'foo')`, and destructuring
     `const { foo } = stored` (a rest element is a loud failure — it reads
     every key, which is exactly what the enumeration must not miss).

   Known, stated limits (both loud, neither silent): the scan attributes
   references by NAME, so a file that shadows `preferences`/`store` with its own
   binding is refused outright, and aliasing the bag into another variable
   (`const bag = preferences`) or returning it outside the store proxy's getter
   is refused — a caller holding an alias could write keys no scan sees. */
import * as acorn from 'acorn';

/** @typedef {{ path: string, src: string }} SourceFile */

/**
 * Parse one module, loudly. A file this scan cannot parse is a file it cannot
 * vouch for, so a parse error is raised, never swallowed.
 * @param {string} src
 * @param {string} label
 */
export function parseModule(src, label) {
  try {
    return acorn.parse(src, { ecmaVersion: 'latest', sourceType: 'module', locations: true });
  } catch (err) {
    throw new Error(`preference-key scan: cannot parse ${label} — ${err && err.message}`);
  }
}

/** Generic ESTree walk that hands the visitor the node's parent. */
export function walk(node, visit, parent = null) {
  if (!node || typeof node !== 'object') return;
  if (Array.isArray(node)) {
    for (const child of node) walk(child, visit, parent);
    return;
  }
  if (typeof node.type !== 'string') return;
  visit(node, parent);
  for (const key of Object.keys(node)) {
    if (key === 'type' || key === 'loc' || key === 'start' || key === 'end') continue;
    walk(node[key], visit, node);
  }
}

/** A parent map for the whole tree, so any node's context is answerable later. */
export function parentMap(ast) {
  /** @type {WeakMap<object, object|null>} */
  const parents = new WeakMap();
  walk(ast, (node, parent) => parents.set(node, parent));
  return parents;
}

function at(node) {
  return node && node.loc ? `line ${node.loc.start.line}` : 'an unknown line';
}

/** Identifier names bound by a binding pattern (destructuring, rest, defaults). */
function patternNames(node, out = []) {
  if (!node) return out;
  if (node.type === 'Identifier') out.push(node);
  else if (node.type === 'ObjectPattern') {
    for (const p of node.properties) {
      if (p.type === 'RestElement') patternNames(p.argument, out);
      else patternNames(p.value, out);
    }
  } else if (node.type === 'ArrayPattern') {
    for (const el of node.elements) patternNames(el, out);
  } else if (node.type === 'AssignmentPattern') patternNames(node.left, out);
  return out;
}

/**
 * The key a member expression reads/writes when its object is the bag:
 * `bag.foo` → "foo", `bag["foo"]` → "foo". Anything else (a computed dynamic
 * key) returns null so the caller can raise — a key the scan cannot name is
 * never silently dropped.
 */
function staticMemberKey(member) {
  if (!member.computed && member.property.type === 'Identifier') return member.property.name;
  if (member.computed && member.property.type === 'Literal' && typeof member.property.value === 'string') {
    return member.property.value;
  }
  return null;
}

/** Static keys of an object literal; raises on anything unenumerable. */
function staticObjectKeys(objExpr, label, what) {
  if (objExpr.type !== 'ObjectExpression') {
    throw new Error(`${label}: ${what} is not an object literal — the scan cannot enumerate the keys it writes`);
  }
  const keys = [];
  for (const p of objExpr.properties) {
    if (p.type === 'SpreadElement') {
      throw new Error(`${label}: ${what} spreads into the preferences bag (${at(p)}) — the scan cannot enumerate the keys a spread brings in`);
    }
    const key = !p.computed && p.key.type === 'Identifier'
      ? p.key.name
      : (p.computed && p.key.type === 'Literal' && typeof p.key.value === 'string'
        ? p.key.value : null);
    if (key === null) {
      throw new Error(`${label}: ${what} has a computed key the scan cannot name (${at(p)})`);
    }
    keys.push(key);
  }
  return keys;
}

// ── loadPreferences: which stored keys does the loader read back? ──

const PREDICATE_UNARY = new Set(['typeof', '!', '-', '+']);
const COMPARE_OPS = new Set(['===', '!==', '==', '!=']);

function isPredicateUse(node, parent) {
  if (parent.type === 'UnaryExpression' && PREDICATE_UNARY.has(parent.operator)) return true;
  if (parent.type === 'BinaryExpression' && COMPARE_OPS.has(parent.operator)) return true;
  if (parent.type === 'LogicalExpression') return true;
  if ((parent.type === 'IfStatement' || parent.type === 'ConditionalExpression'
    || parent.type === 'WhileStatement' || parent.type === 'DoWhileStatement')
    && parent.test === node) return true;
  return false;
}

function isHasOwnPropertyCall(node) {
  return node.type === 'CallExpression' && node.callee.type === 'MemberExpression'
    && !node.callee.computed && node.callee.property.type === 'Identifier'
    && node.callee.property.name === 'call'
    && node.callee.object.type === 'MemberExpression' && !node.callee.object.computed
    && node.callee.object.property.type === 'Identifier'
    && node.callee.object.property.name === 'hasOwnProperty';
}

function addDestructuredKeys(pattern, keys, label) {
  for (const p of pattern.properties) {
    if (p.type === 'RestElement') {
      throw new Error(`${label}: destructuring the stored payload with a rest element (${at(p)}) reads EVERY stored key — `
        + 'the enumeration cannot account for that; carry each key through explicitly so it gets classified');
    }
    const key = !p.computed && p.key.type === 'Identifier'
      ? p.key.name
      : (p.computed && p.key.type === 'Literal' && typeof p.key.value === 'string'
        ? p.key.value : null);
    if (key === null) {
      throw new Error(`${label}: destructuring the stored payload with a computed key (${at(p)}) cannot be enumerated`);
    }
    keys.add(key);
  }
}

/**
 * Every key loadPreferences reads out of the stored payload. Raises when the
 * loader touches `stored` in a way the scan cannot account for — including a
 * rest-element destructure, a computed key, or an alias of the payload.
 * @param {string} src source containing `function loadPreferences(...)`
 * @param {string} label
 * @returns {Set<string>}
 */
export function scanLoaderReadKeys(src, label = 'loader source') {
  const ast = parseModule(src, label);
  let loader = null;
  for (const top of ast.body) {
    const fn = top.type === 'ExportNamedDeclaration' ? top.declaration : top;
    if (fn && fn.type === 'FunctionDeclaration' && fn.id && fn.id.name === 'loadPreferences') loader = fn;
  }
  if (!loader) {
    throw new Error(`${label}: no ` + '`export async function loadPreferences()`' + ` declaration found — `
      + 'the enumeration is anchored on that name and shape; update it deliberately, never by accident');
  }
  /** @type {Set<string>} */
  const keys = new Set();
  let sawStored = false;
  walk(loader.body, (node, parent) => {
    if (node.type !== 'Identifier' || node.name !== 'stored' || !parent) return;
    sawStored = true;
    // The binding itself: `const stored = await api("load_preferences")`.
    if (parent.type === 'VariableDeclarator' && parent.id === node) return;
    // Reading a key: stored.foo / stored["foo"] (also inside a longer chain).
    if (parent.type === 'MemberExpression' && parent.object === node) {
      const key = staticMemberKey(parent);
      if (key === null) {
        throw new Error(`${label}: stored[…] with a computed key (${at(node)}) — the scan cannot name which stored key is read`);
      }
      keys.add(key);
      return;
    }
    // hasOwnProperty.call(stored, "foo")
    if (isHasOwnPropertyCall(parent) && parent.arguments[0] === node) {
      const name = parent.arguments[1];
      if (name && name.type === 'Literal' && typeof name.value === 'string') {
        keys.add(name.value);
        return;
      }
      throw new Error(`${label}: hasOwnProperty.call(stored, …) with a computed key (${at(node)}) cannot be enumerated`);
    }
    // Destructuring the payload: const { a, b } = stored; / ({ a } = stored)
    if (parent.type === 'VariableDeclarator' && parent.init === node) {
      if (parent.id.type === 'ObjectPattern') {
        addDestructuredKeys(parent.id, keys, label);
        return;
      }
      throw new Error(`${label}: the stored payload is bound to a new name (${at(parent)}) — an alias the scan cannot follow; `
        + 'read each key off `stored` directly so the erasure guard can see it');
    }
    if (parent.type === 'AssignmentExpression' && parent.right === node) {
      if (parent.left.type === 'ObjectPattern') {
        addDestructuredKeys(parent.left, keys, label);
        return;
      }
      if (parent.left.type === 'Identifier') {
        throw new Error(`${label}: the stored payload is re-bound to \`${parent.left.name}\` (${at(parent)}) — an alias the scan cannot follow`);
      }
    }
    // Pure predicate uses: `if (stored && typeof stored === "object")` etc.
    if (isPredicateUse(node, parent)) return;
    throw new Error(`${label}: cannot account for the use of \`stored\` at ${at(node)} (inside a ${parent.type}) — `
      + 'the scan refuses to guess which stored keys it reads');
  });
  if (!sawStored) {
    throw new Error(`${label}: loadPreferences never references \`stored\` — the payload binding was renamed or removed; `
      + 'update this enumeration deliberately, never by accident');
  }
  return keys;
}

// ── Writes: which preference keys does the code write? ──

function isStoreFile(path) { return /(^|\/)store\.js$/.test(path); }

/** The module-level `let preferences = {…}` declarator in store.js. */
function findModuleBag(ast) {
  for (const top of ast.body) {
    if (top.type === 'VariableDeclaration') {
      for (const d of top.declarations) {
        if (d.id.type === 'Identifier' && d.id.name === 'preferences') return d;
      }
    }
  }
  return null;
}

/**
 * The initial literal keys of the preferences bag. Raises when the declaration
 * is gone, is not a literal, or contains a key/spread the scan cannot name.
 * @param {object} ast parsed store.js
 * @param {string} path
 * @returns {Set<string>}
 */
export function scanInitialLiteralKeys(ast, path) {
  const decl = findModuleBag(ast);
  if (!decl) {
    throw new Error(`${path}: no module-level \`let preferences = …\` declaration found — `
      + 'the enumeration is anchored on that shape; update it deliberately, never by accident');
  }
  if (!decl.init || decl.init.type !== 'ObjectExpression') {
    throw new Error(`${path}: the initial preferences bag is not an object literal — the scan cannot enumerate its keys`);
  }
  return new Set(staticObjectKeys(decl.init, path, 'the initial preferences literal'));
}

/** A reference to the bag: the module binding in store.js, or `store.preferences` anywhere. */
function bagRootKind(node, isStore) {
  if (isStore && node.type === 'Identifier' && node.name === 'preferences') return 'binding';
  if (node.type === 'MemberExpression'
    && ((node.computed && node.property.type === 'Literal' && node.property.value === 'preferences')
      || (!node.computed && node.property.type === 'Identifier' && node.property.name === 'preferences'))
    && node.object.type === 'Identifier' && node.object.name === 'store') return 'proxy';
  return null;
}

function collectBindings(ast) {
  const out = [];
  walk(ast, (node) => {
    switch (node.type) {
      case 'VariableDeclarator':
        for (const id of patternNames(node.id)) out.push({ name: id.name, node: id, isImport: false });
        break;
      case 'FunctionDeclaration':
      case 'FunctionExpression':
      case 'ClassDeclaration':
      case 'ClassExpression':
        if (node.id) out.push({ name: node.id.name, node: node.id, isImport: false });
        for (const p of node.params) for (const id of patternNames(p)) out.push({ name: id.name, node: id, isImport: false });
        break;
      case 'CatchClause':
        if (node.param) for (const id of patternNames(node.param)) out.push({ name: id.name, node: id, isImport: false });
        break;
      case 'ImportDeclaration':
        for (const s of node.specifiers) out.push({ name: s.local.name, node: s.local, isImport: true });
        break;
    }
  });
  return out;
}

function auditBindings(ast, path, bagIdNode, isStore) {
  for (const b of collectBindings(ast)) {
    if (b.name === 'preferences' && b.node !== bagIdNode) {
      throw new Error(`${path}:${at(b.node)} binds the name \`preferences\` to something other than the module-level bag — `
        + 'this scan attributes every `preferences` reference to the bag; rename the local');
    }
    if (b.name === 'store' && !b.isImport && !isStore) {
      throw new Error(`${path}:${at(b.node)} binds a local name \`store\` — the scan attributes \`store.preferences\` to the `
        + 'store proxy; rename it or import the real store');
    }
  }
}

function enclosingFunction(node, parents) {
  let cur = parents.get(node);
  while (cur) {
    if (cur.type === 'FunctionDeclaration' || cur.type === 'FunctionExpression' || cur.type === 'ArrowFunctionExpression') return cur;
    cur = parents.get(cur);
  }
  return null;
}

function functionName(fn, parents) {
  if ((fn.type === 'FunctionDeclaration') && fn.id) return fn.id.name;
  const link = parents.get(fn);
  if (!link) return null;
  if (link.type === 'VariableDeclarator' && link.id.type === 'Identifier') return link.id.name;
  if (link.type === 'Property' && !link.computed && link.key.type === 'Identifier') return link.key.name;
  return null;
}

function isObjectCall(node, fn) {
  return node.type === 'CallExpression' && node.callee.type === 'MemberExpression'
    && node.callee.object.type === 'Identifier' && node.callee.object.name === 'Object'
    && !node.callee.computed && node.callee.property.type === 'Identifier'
    && node.callee.property.name === fn;
}

/** Is this member expression the mutation target of Object.assign/defineProperty? */
function isMutatorArg(member, parents) {
  const call = parents.get(member);
  if (!call || call.type !== 'CallExpression' || call.arguments[0] !== member) return false;
  return isObjectCall(call, 'assign') || isObjectCall(call, 'defineProperty');
}

function isPrefsGetter(returnNode, parents) {
  const fn = parents.get(parents.get(returnNode)); // Block → Function
  if (!fn || (fn.type !== 'FunctionExpression' && fn.type !== 'FunctionDeclaration')) return false;
  const prop = parents.get(fn);
  return !!prop && prop.type === 'Property' && prop.kind === 'get'
    && !prop.computed && prop.key.type === 'Identifier' && prop.key.name === 'preferences';
}

/** A predicate use of the bag itself (`if (preferences && …)`) cannot write. */
function isPredicateRoot(node, parents) {
  let cur = node;
  let parent = parents.get(node);
  while (parent && parent.type === 'LogicalExpression') { cur = parent; parent = parents.get(cur); }
  if (!parent) return false;
  if (parent.type === 'UnaryExpression' && PREDICATE_UNARY.has(parent.operator)) return true;
  if (parent.type === 'BinaryExpression' && COMPARE_OPS.has(parent.operator)) return true;
  if ((parent.type === 'IfStatement' || parent.type === 'ConditionalExpression'
    || parent.type === 'WhileStatement' || parent.type === 'DoWhileStatement') && parent.test === cur) return true;
  return parent.type === 'ExpressionStatement' && cur.type === 'LogicalExpression';
}

/**
 * Every preference key the given sources write, mapped to the files that write
 * them. Raises on any bag reference the scan cannot account for — a computed
 * write key, an alias, a bag returned outside the store proxy's getter, an
 * Object.assign whose source is not a literal, a whole-bag replacement built
 * from a value the scan cannot enumerate.
 *
 * @param {SourceFile[]} sources
 * @returns {Map<string, string[]>} key → writing file paths
 */
export function scanWrittenKeys(sources) {
  /** @type {Map<string, string[]>} */
  const byKey = new Map();
  const add = (key, file) => {
    if (!byKey.has(key)) byKey.set(key, []);
    if (!byKey.get(key).includes(file)) byKey.get(key).push(file);
  };
  /** @type {{fn: string, pos: number, path: string}[]} */
  const sinks = [];
  const files = sources.map(({ path, src }) => ({ path, ast: parseModule(src, path) }));

  for (const { path, ast } of files) {
    const isStore = isStoreFile(path);
    const bag = isStore ? findModuleBag(ast) : null;
    if (isStore && !bag) {
      throw new Error(`${path}: is named store.js but declares no module-level \`let preferences\` — the scan cannot attribute \`preferences\` references`);
    }
    const parents = parentMap(ast);
    auditBindings(ast, path, bag ? bag.id : null, isStore);

    // Nodes sitting in a write-target position (assignment/update/delete/for-of
    // targets, including every node of a nested member chain).
    const targets = new Set();
    const mark = (n) => walk(n, (x) => targets.add(x));

    /** `preferences = <expr>` — whole-bag replacement. */
    const wholeBagReassign = (assignNode) => {
      const right = assignNode.right;
      if (right.type === 'Identifier' && right.name === 'preferences') return; // self-copy
      if (right.type !== 'ObjectExpression') {
        throw new Error(`${path}:${at(right)} replaces the whole preferences bag with a value the scan cannot enumerate — `
          + 'build the new bag from literals and spreads of the bag itself');
      }
      for (const p of right.properties) {
        if (p.type === 'SpreadElement') {
          const a = p.argument;
          if (a.type === 'Identifier' && a.name === 'preferences') continue; // copy of the bag
          if (a.type === 'Identifier') {
            const fn = enclosingFunction(assignNode, parents);
            const fnName = fn ? functionName(fn, parents) : null;
            const plain = fn && fn.params.every((x) => x.type === 'Identifier');
            const pos = plain ? fn.params.findIndex((x) => x.name === a.name) : -1;
            if (!fnName || pos < 0) {
              throw new Error(`${path}:${at(p)} spreads \`${a.name}\` into a fresh preferences bag inside a function the scan `
                + 'cannot name or whose parameters are destructured — its callers cannot be traced');
            }
            sinks.push({ fn: fnName, pos, path });
            continue;
          }
          throw new Error(`${path}:${at(p)} spreads a computed value into a fresh preferences bag — the scan cannot enumerate the keys it brings in`);
        }
        const key = !p.computed && p.key.type === 'Identifier' ? p.key.name
          : (p.computed && p.key.type === 'Literal' && typeof p.key.value === 'string' ? p.key.value : null);
        if (key === null) {
          throw new Error(`${path}:${at(p)} has a computed key in a whole-bag preferences replacement — cannot enumerate it`);
        }
        add(key, path);
      }
    };

    walk(ast, (node) => {
      if (node.type === 'AssignmentExpression') mark(node.left);
      else if (node.type === 'UpdateExpression') mark(node.argument);
      else if (node.type === 'UnaryExpression' && node.operator === 'delete') mark(node.argument);
      else if ((node.type === 'ForOfStatement' || node.type === 'ForInStatement') && node.left.type !== 'VariableDeclaration') mark(node.left);
    });

    walk(ast, (node, parent) => {
      // Whole-bag replacement is analysed from the assignment itself.
      if (node.type === 'AssignmentExpression'
        && ((isStore && node.left.type === 'Identifier' && node.left.name === 'preferences')
          || bagRootKind(node.left, isStore) === 'proxy')) {
        wholeBagReassign(node);
        return;
      }
      const kind = bagRootKind(node, isStore);
      if (!kind || !parent) return;
      // Non-reference positions: `foo.preferences`, `{ preferences: … }` keys,
      // the bag declaration itself.
      if (parent.type === 'MemberExpression' && !parent.computed && parent.property === node) return;
      if (parent.type === 'Property' && !parent.computed && parent.key === node) return;
      if (parent.type === 'VariableDeclarator' && parent.id === node) return;
      if (parent.type === 'AssignmentExpression' && parent.left === node) return; // handled above

      if (parent.type === 'MemberExpression' && parent.object === node) {
        const key = staticMemberKey(parent);
        const written = targets.has(parent) || isMutatorArg(parent, parents);
        if (key === null) {
          if (written) {
            throw new Error(`${path}:${at(parent)} writes preferences[…] with a COMPUTED key — the scan cannot name the key being written, so this write evades the guard`);
          }
          return; // a dynamic READ: the guard tracks writes, not reads
        }
        if (written) add(key, path);
        return;
      }
      if (parent.type === 'CallExpression') {
        if (isObjectCall(parent, 'assign') && parent.arguments[0] === node) {
          for (const srcArg of parent.arguments.slice(1)) {
            for (const key of staticObjectKeys(srcArg, `${path}:${at(parent)}`, 'Object.assign(preferences, …) source')) add(key, path);
          }
          return;
        }
        if (isObjectCall(parent, 'defineProperty') && parent.arguments[0] === node) {
          const nameNode = parent.arguments[1];
          if (nameNode && nameNode.type === 'Literal' && typeof nameNode.value === 'string') add(nameNode.value, path);
          else throw new Error(`${path}:${at(parent)} Object.defineProperty(preferences, …) with a computed key — cannot enumerate it`);
          return;
        }
        if (isObjectCall(parent, 'defineProperties') || isObjectCall(parent, 'freeze')
          || isObjectCall(parent, 'setPrototypeOf') || isObjectCall(parent, 'preventExtensions')) {
          throw new Error(`${path}:${at(parent)} mutates the preferences bag through Object.${parent.callee.property.name} — the scan cannot enumerate what it writes`);
        }
        return; // passed to a function: treated as a read (stated limit)
      }
      if (parent.type === 'VariableDeclarator' && parent.init === node) {
        throw new Error(`${path}:${at(parent)} aliases the preferences bag into another variable — writes through the alias `
          + 'would evade this scan; take a copy (snapshotPreferences) or name the keys directly');
      }
      if (parent.type === 'AssignmentExpression' && parent.right === node) {
        throw new Error(`${path}:${at(parent)} assigns the preferences bag to another variable — an alias the scan cannot follow`);
      }
      if (parent.type === 'ReturnStatement' && parent.argument === node) {
        if (!isPrefsGetter(parent, parents)) {
          throw new Error(`${path}:${at(parent)} returns the preferences bag — a caller holding it can write keys this scan `
            + 'cannot see; return a copy (snapshotPreferences) instead');
        }
        return;
      }
      if (parent.type === 'SpreadElement') return; // `{ …preferences }` — a copy, not a write
      if (parent.type === 'Property' && parent.value === node) return; // embedded in a literal: a read
      if (parent.type === 'TemplateLiteral') return;
      if (parent.type === 'ArrayPattern' || parent.type === 'ObjectPattern' || parent.type === 'RestElement') {
        throw new Error(`${path}:${at(node)} uses the preferences bag as a destructuring target — the keys written cannot be enumerated`);
      }
      if (isPredicateRoot(node, parents)) return;
      throw new Error(`${path}: cannot account for the reference to the preferences bag at ${at(node)} (inside a ${parent.type}) — `
        + 'the scan refuses to guess whether it writes');
    });
  }

  // Whole-bag setters: every call site must pass an object literal, and its
  // keys count as writes (attributed to the setter's file — the setter itself
  // is what publishes for its callers).
  for (const sink of sinks) {
    for (const { path, ast } of files) {
      walk(ast, (node) => {
        if (node.type !== 'CallExpression' || node.callee.type !== 'Identifier' || node.callee.name !== sink.fn) return;
        const arg = node.arguments[sink.pos];
        if (!arg) {
          throw new Error(`${path}:${at(node)} calls ${sink.fn}(…) without its whole-bag argument — the keys it writes cannot be enumerated`);
        }
        for (const key of staticObjectKeys(arg, `${path}:${at(node)}`, `call to ${sink.fn}()`)) add(key, sink.path);
      });
    }
  }
  return byKey;
}

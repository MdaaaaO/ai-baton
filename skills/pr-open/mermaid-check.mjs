// Usage: node $BATON/skills/pr-open/mermaid-check.mjs [--allow-none] <markdown-file>...
// Run from a scratchpad dir seeded with this skill's package.json + package-lock.json
// (`npm ci --no-audit --no-fund` — modules resolve from the CURRENT DIRECTORY, not from this file's location).
// Parses every ```mermaid block in each file (CRLF- and LF-terminated fences alike); prints
// `OK (<type>)` / `FAIL <error>` per block; exits 1 on any FAIL. A file with zero blocks also exits 1
// (a plan called for diagrams that never got drawn) unless --allow-none is passed. Exits 2, with an
// install hint on stderr, when jsdom/dompurify/mermaid do not resolve from the CWD (deps not installed
// here yet, or the script was run from the wrong directory). See SKILL.md § Diagrams.
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';

const req = createRequire(path.join(process.cwd(), '/'));
const load = async (name) => (await import(pathToFileURL(req.resolve(name)).href)).default;

let JSDOM, DOMPurifyFactory, mermaid, dom;
try {
  ({ JSDOM } = await load('jsdom'));
  // dompurify and mermaid each read globalThis.window at import time to decide which build to use;
  // the window must exist before they load, or mermaid's own DOMPurify instance comes up without one
  // (e.g. `DOMPurify.addHook is not a function`) and every diagram with a labelled node fails to parse.
  dom = new JSDOM('<!DOCTYPE html><body></body>', { pretendToBeVisual: true });
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  DOMPurifyFactory = await load('dompurify');
  mermaid = await load('mermaid');
} catch (e) {
  const skillDir = path.dirname(fileURLToPath(import.meta.url));
  console.error(`FAIL cannot resolve jsdom/dompurify/mermaid from ${process.cwd()}: ${String(e.message).split('\n')[0]}`);
  console.error(`install first: copy ${skillDir}/package.json and package-lock.json into a scratch dir, ` +
    `\`npm ci --no-audit --no-fund\` there, then run this script with that dir as your CWD.`);
  process.exit(2);
}
globalThis.DOMPurify = DOMPurifyFactory(dom.window);
mermaid.initialize({ startOnLoad: false });

const args = process.argv.slice(2);
const allowNone = args.includes('--allow-none');
const files = args.filter((a) => a !== '--allow-none');

let bad = 0;
for (const f of files) {
  const src = fs.readFileSync(f, 'utf8');
  const blocks = [...src.matchAll(/```mermaid\r?\n([\s\S]*?)```/g)].map((m) => m[1]);
  if (blocks.length === 0) {
    console.log(`${f}: no mermaid blocks`);
    if (!allowNone) bad++;
  }
  for (const [i, b] of blocks.entries()) {
    try {
      const r = await mermaid.parse(b);
      console.log(`${f} #${i + 1}: OK (${r.diagramType})`);
    } catch (e) {
      bad++;
      console.log(`${f} #${i + 1}: FAIL ${String(e.message).split('\n').slice(0, 3).join(' | ')}`);
    }
  }
}
process.exit(bad ? 1 : 0);

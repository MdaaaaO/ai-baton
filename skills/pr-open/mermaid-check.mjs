// Usage: node $BATON/skills/pr-open/mermaid-check.mjs [--allow-none] <markdown-file>...
// Run from a scratchpad dir that has `npm i --no-audit --no-fund mermaid@11 jsdom` installed
// (modules resolve from the CURRENT DIRECTORY, not from this file's location).
// Parses every ```mermaid block in each file (CRLF- and LF-terminated fences alike); prints
// `OK (<type>)` / `FAIL <error>` per block; exits 1 on any FAIL. A file with zero blocks also exits 1
// (a plan called for diagrams that never got drawn) unless --allow-none is passed. See SKILL.md § Diagrams.
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

const req = createRequire(path.join(process.cwd(), '/'));
const load = async (name) => (await import(pathToFileURL(req.resolve(name)).href)).default;

const { JSDOM } = await load('jsdom');
const dom = new JSDOM('<!DOCTYPE html><body></body>', { pretendToBeVisual: true });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.DOMPurify = (await load('dompurify'))(dom.window);
const mermaid = await load('mermaid');
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

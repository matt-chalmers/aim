// Side-by-side fidelity compare for UNAUTHENTICATED pages (auth flow): renders
// the handover component vs our route WITHOUT logging in. Same output contract
// as fidelity-compare.mjs. Usage: node fidelity-noauth.mjs <name> <comp> <path> <width>
import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

// playwright and sharp are the project's Node dependencies, not the harness's — the
// harness has no package.json and deliberately installs nothing. Resolve them
// relative to this file so the tool works from any checkout or worktree (it used
// to hard-code one developer's absolute path, which broke everywhere else).
// The Node dependency directory is a PROJECT fact — the node stack's `dependency_dir`
// under its `root`. Hardcoding `../../frontend/node_modules` made this the one line
// that still assumed the origin's layout, and it throws on import before any of the
// configurable values below are read.
const NODE_MODULES = process.env.FIDELITY_NODE_MODULES
  || resolve(dirname(fileURLToPath(import.meta.url)), '../../node_modules');
const { chromium } = await import(`${NODE_MODULES}/playwright/index.mjs`);
const require = createRequire(import.meta.url);
const sharp = require(`${NODE_MODULES}/sharp`);
// Scratch output follows SCRATCHPAD/TMPDIR; /tmp is one machine's layout.
const SHOTS_DIR = process.env.SHOTS_DIR
  || `${process.env.SCRATCHPAD || process.env.TMPDIR || '/tmp'}/shots`;
mkdirSync(`${SHOTS_DIR}/cmp`, { recursive: true });

const [,, name, comp, ourPath, widthArg] = process.argv;
const width = Number(widthArg || 1280);
// Hosts and the handover root element come from the environment; see
// fidelity-compare.mjs for why none of this is hard-coded any more.
const APP_URL           = process.env.FIDELITY_APP_URL      || 'http://localhost:3000';
const HANDOVER_URL      = process.env.FIDELITY_HANDOVER_URL || 'http://localhost:8899/inspect.html';
const HANDOVER_SELECTOR = process.env.FIDELITY_HANDOVER_SELECTOR || 'body';

const b = await chromium.launch();

const hc = await b.newContext({ viewport:{ width, height: 1000 }, deviceScaleFactor: 1 });
const hp = await hc.newPage();
await hp.goto(`${HANDOVER_URL}?s=${comp}`, { waitUntil:'networkidle' });
await hp.waitForTimeout(1200);
const hEl = await hp.$(HANDOVER_SELECTOR);
const bufH = await (hEl ?? hp).screenshot();

// our render — fresh context, NO login
const oc = await b.newContext({ viewport:{ width, height: 1000 }, deviceScaleFactor: 1 });
const op = await oc.newPage();
await op.goto(`${APP_URL}${ourPath}`,{ waitUntil:'networkidle' });
await op.waitForTimeout(1500);
const bufO = await op.screenshot();
await b.close();

const PANEL_H = 900, top = 30, gap = 16;
const pH = await sharp(bufH).resize({ height: PANEL_H, fit: 'inside' }).png().toBuffer();
const pO = await sharp(bufO).resize({ height: PANEL_H, fit: 'inside' }).png().toBuffer();
const mH = await sharp(pH).metadata();
const mO = await sharp(pO).metadata();
const W = mH.width + gap + mO.width;
const H = PANEL_H + top;
const lbl = (text, w) => Buffer.from(
  `<svg width="${Math.max(w,120)}" height="${top}"><rect width="${Math.max(w,120)}" height="${top}" fill="#151311"/><text x="10" y="20" font-family="monospace" font-size="14" fill="#5EC2A8">${text}</text></svg>`);
const out = `${SHOTS_DIR}/cmp/${name}.png`;
await sharp({ create:{ width:W, height:H, channels:4, background:{ r:245,g:243,b:239,alpha:1 } } })
  .composite([
    { input: pH, left:0, top },
    { input: pO, left: mH.width+gap, top },
    { input: lbl(`HANDOVER ${comp}`, mH.width), left:0, top:0 },
    { input: lbl(`OURS ${ourPath}`, mO.width), left: mH.width+gap, top:0 },
  ]).png().toFile(out);
console.log('wrote', out, `(${W}x${H})`);

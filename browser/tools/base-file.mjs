#!/usr/bin/env node
// Reproduce files of the builder's base tree locally, without a checkout:
// Chromium at chromium_version.txt + ungoogled-chromium's patches (series order, minus
// patches/ungoogled-drop.list, plus patches/ungoogled-extra.list), then ungoogled's domain
// substitution (minus patches/substitution-exclude.list), for just these paths. Same steps as
// remote/ungoogle.sh, so anchors that touch (substituted) URLs check true locally.
//
//   node tools/base-file.mjs chrome/browser/prefs/browser_prefs.cc ...
//   -> prints the local path of each file (cached under ~/.cache/mast/base/<version>/)
//   node tools/base-file.mjs --series   -> the ungoogled patches applied, in order
//
// MAST_CHROMIUM_VERSION and MAST_UNGOOGLED_TAG pick another release than the pinned one
// (./mast check-rebase uses them to look at the next release before moving to it).
// The base is base.txt's (what Mast ships), or MAST_BASE: "chromium" is pristine Chromium (no
// ungoogled patches and no domain substitution; docs/BASE-PLAN.md), cached apart from the
// "ungoogled" base.
// MAST_ALLOW_MISSING=1 prints "-" for a file Chromium's source doesn't have (generated or
// from another repo) instead of failing.
//
// Used by `./mast check-patches` to validate make-patches.py anchors before renting the server.
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const here = path.dirname(new URL(import.meta.url).pathname);
const root = path.resolve(here, '..');
const version = process.env.MAST_CHROMIUM_VERSION
  || fs.readFileSync(path.join(root, 'chromium_version.txt'), 'utf8').trim();
const ugTag = process.env.MAST_UNGOOGLED_TAG
  || fs.readFileSync(path.join(root, 'ungoogled_version.txt'), 'utf8').trim();
const cache = path.join(os.homedir(), '.cache/mast');
const pristine = (process.env.MAST_BASE || fs.readFileSync(path.join(root, 'base.txt'), 'utf8').trim()) === 'chromium';
const baseDir = path.join(cache, 'base', pristine ? `${version}-chromium` : `${version}+${ugTag}-sub`);
const ugDir = path.join(cache, 'ungoogled', ugTag);

const list = (f) => fs.readFileSync(path.join(root, 'patches', f), 'utf8')
  .split('\n').map((l) => l.replace(/#.*/, '').trim()).filter(Boolean);

async function get(url) {
  for (let i = 0; ; i++) {
    try {
      const r = await fetch(url, { signal: AbortSignal.timeout(30000) });
      if (r.status === 404) throw Object.assign(new Error(`404 ${url}`), { missing: true });
      if (!r.ok) throw new Error(`${r.status} ${url}`);
      return r;
    } catch (e) {
      if (e.missing || i >= 3) throw e;
      await new Promise((res) => setTimeout(res, 1000 * (i + 1)));
    }
  }
}

// Run fn over items, n at a time.
async function pool(items, n, fn) {
  const q = [...items];
  await Promise.all(Array.from({ length: n }, async () => { while (q.length) await fn(q.shift()); }));
}

async function ungoogledSeries() {
  const seriesFile = path.join(ugDir, 'series');
  if (!fs.existsSync(seriesFile)) {
    fs.mkdirSync(ugDir, { recursive: true });
    const raw = `https://raw.githubusercontent.com/ungoogled-software/ungoogled-chromium/${ugTag}/patches/`;
    const series = (await (await get(raw + 'series')).text());
    const names = series.split('\n').map((l) => l.replace(/#.*/, '').trim()).filter(Boolean);
    await pool(names, 8, async (n) => {
      const out = path.join(ugDir, 'p', n);
      fs.mkdirSync(path.dirname(out), { recursive: true });
      fs.writeFileSync(out, await (await get(raw + n)).text());
    });
    fs.writeFileSync(seriesFile, series);
  }
  const drop = new Set(list('ungoogled-drop.list').map((n) => path.basename(n)));
  const extra = new Set(list('ungoogled-extra.list').map((n) => path.basename(n)));
  return fs.readFileSync(seriesFile, 'utf8').split('\n').map((l) => l.replace(/#.*/, '').trim())
    .filter(Boolean)
    .filter((n) => !drop.has(path.basename(n)))
    .filter((n) => !n.startsWith('extra/') || extra.has(path.basename(n)));
}

// ungoogled-chromium's domain substitution: domain_regex.list (Python regexes, applied in
// order to every file in domain_substitution.list, minus Mast's exclusions, which are
// substring matches like the grep -F in remote/ungoogle.sh).
let substitution;
async function domainSubstitution() {
  if (substitution) return substitution;
  const raw = `https://raw.githubusercontent.com/ungoogled-software/ungoogled-chromium/${ugTag}/`;
  const cached = async (name) => {
    const p = path.join(ugDir, name);
    if (!fs.existsSync(p)) {
      fs.mkdirSync(ugDir, { recursive: true });
      fs.writeFileSync(p, await (await get(raw + name)).text());
    }
    return fs.readFileSync(p, 'utf8');
  };
  const exclude = list('substitution-exclude.list');
  const files = new Set((await cached('domain_substitution.list')).split('\n').filter(Boolean)
    .filter((l) => !exclude.some((e) => l.includes(e))));
  const rules = (await cached('domain_regex.list')).split('\n').filter(Boolean).map((l) => {
    const i = l.indexOf('#');
    return [new RegExp(l.slice(0, i), 'g'), l.slice(i + 1).replace(/\\g<(\d+)>/g, '$$1')];
  });
  return (substitution = { files, rules });
}

// The part of a unified diff that changes `file`.
function sectionFor(patchText, file) {
  const parts = patchText.split(/^(?=diff |--- a\/)/m);
  return parts.filter((p) => new RegExp(`^\\+\\+\\+ b/${file.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`, 'm').test(p)).join('');
}

async function baseFile(file) {
  const out = path.join(baseDir, file);
  if (fs.existsSync(out)) return out;
  const url = `https://chromium.googlesource.com/chromium/src/+/refs/tags/${version}/${file}?format=TEXT`;
  const text = Buffer.from(await (await get(url)).text(), 'base64').toString('utf8');
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(out, text);
  if (pristine) return out;
  for (const name of await ungoogledSeries()) {
    const section = sectionFor(fs.readFileSync(path.join(ugDir, 'p', name), 'utf8'), file);
    if (!section) continue;
    execFileSync('patch', ['--silent', '--forward', '-p1', '-d', baseDir], { input: section });
    process.stderr.write(`  ${file}: applied ${name}\n`);
  }
  const { files, rules } = await domainSubstitution();
  if (files.has(file)) {
    let text = fs.readFileSync(out, 'utf8');
    for (const [re, to] of rules) text = text.replace(re, to);
    fs.writeFileSync(out, text);
    process.stderr.write(`  ${file}: domain substitution\n`);
  }
  return out;
}

const args = process.argv.slice(2);
if (args[0] === '--series') {
  for (const n of await ungoogledSeries()) console.log(n);
} else {
  for (const f of args) {
    try {
      console.log(await baseFile(f));
    } catch (e) {
      if (!(e.missing && process.env.MAST_ALLOW_MISSING)) throw e;
      console.log('-');
    }
  }
}

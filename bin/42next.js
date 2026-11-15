#!/usr/bin/env node
'use strict';
// 42next: menu + project detection. The real testing is done by the testers
// in ../testers (one per project, same CLI contract, see testers/common/core.py).

const fs = require('fs');
const path = require('path');
const os = require('os');
const { spawnSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const T = path.join(ROOT, 'testers');
const HOME = os.homedir();
// python3 on Linux / macOS, python or py on Windows
const PY = process.env.PYTHON || ['python3', 'python', 'py'].find((c) => spawnSync(c, ['-c', 'import sys; assert sys.version_info >= (3, 9)']).status === 0) || 'python3';
const tty = process.stdout.isTTY && !process.env.NO_COLOR;
// dark blue theme: exact colours on truecolor terminals, nearest 256-colour otherwise
const TRUE = /truecolor|24bit/.test(process.env.COLORTERM || '');
const rgb = (hex, c256, bg = false) => (TRUE
  ? `${bg ? 48 : 38};2;${parseInt(hex.slice(1, 3), 16)};${parseInt(hex.slice(3, 5), 16)};${parseInt(hex.slice(5, 7), 16)}`
  : `${bg ? 48 : 38};5;${c256}`);
const THEME = { acc: rgb('#4f9dff', 75), dim: rgb('#7088b0', 67), rule: rgb('#1d3a6b', 24), sel: rgb('#12306b', 17, true),
  ok: rgb('#4ade80', 78), ko: rgb('#f87171', 203), warn: rgb('#fbbf24', 220) };
const col = (c) => (s) => (tty ? `\x1b[${c}m${s}\x1b[0m` : String(s));
const bold = col('1'), dim = col(THEME.dim), green = col(THEME.ok), red = col(THEME.ko), cyan = col(THEME.acc), yellow = col(THEME.warn);
const ruleCol = col(THEME.rule);

// ---------------------------------------------------------------- helpers

const exists = (...p) => fs.existsSync(path.join(...p));
const isDir = (p) => { try { return fs.statSync(p).isDirectory(); } catch { return false; } };
const ls = (d) => { try { return fs.readdirSync(d); } catch { return []; } };
const read = (p) => { try { return fs.readFileSync(p, 'utf8'); } catch { return ''; } };
const subdirs = (d) => ls(d).filter((n) => !n.startsWith('.') && n !== 'node_modules' && isDir(path.join(d, n))).map((n) => path.join(d, n));
const short = (p) => (p.startsWith(HOME) ? '~' + p.slice(HOME.length) : p);

function findFile(dir, test, depth = 2) {
  for (const n of ls(dir)) {
    const p = path.join(dir, n);
    if (n.startsWith('.')) continue;
    if (isDir(p)) { if (depth > 0) { const r = findFile(p, test, depth - 1); if (r) return r; } } else if (test(n, p)) return p;
  }
  return null;
}

// ---------------------------------------------------------------- projects

const SPECS = ls(path.join(T, 'py', 'specs')).filter((f) => f.endsWith('.json'))
  .map((f) => { try { return JSON.parse(read(path.join(T, 'py', 'specs', f))); } catch { return null; } })
  .filter(Boolean);

// Each detector gets a directory and returns a score (0 = not this project).
const C_PROJECTS = [
  {
    id: 'libft', name: 'libft', tester: 'libft/run.py',
    score: (d) => (exists(d, 'libft.h') ? 1 : 0) * ls(d).filter((n) => /^ft_.*\.c$/.test(n)).length,
  },
  {
    id: 'printf', name: 'ft_printf', tester: 'printf/run.py',
    score: (d) => (/libftprintf\.a/.test(read(path.join(d, 'Makefile'))) ? 50 : 0)
      + (findFile(d, (n, p) => n.endsWith('.h') && /ft_printf\s*\(/.test(read(p))) && exists(d, 'Makefile') ? 30 : 0),
  },
  {
    id: 'gnl', name: 'get_next_line', tester: 'gnl/run.py',
    score: (d) => (exists(d, 'get_next_line.c') ? 50 : 0) + (exists(d, 'get_next_line.h') ? 10 : 0),
  },
  {
    id: 'push_swap', name: 'push_swap', tester: 'push_swap/run.py',
    score: (d) => (/push_swap/.test(read(path.join(d, 'Makefile'))) ? 50 : 0) + (exists(d, 'push_swap.h') ? 20 : 0),
  },
  {
    id: 'born2beroot', name: 'Born2beRoot', tester: 'born2beroot/run.py',
    score: (d) => (exists(d, 'signature.txt') ? 50 : 0),
  },
];

function pyScore(d, spec) {
  return spec.exercises.filter((ex) => exists(d, ex.file)).length;
}

// Look around `start`: itself, its parents (when run from ex3/ or src/), its
// children and grandchildren (when run from ~/doc/cc/libft with the repo inside).
function detect(start) {
  const dirs = new Set([start]);
  let p = start;
  for (let i = 0; i < 3; i++) { p = path.dirname(p); dirs.add(p); }
  for (const c of subdirs(start)) { dirs.add(c); for (const g of subdirs(c)) dirs.add(g); }

  const found = [];
  for (const d of dirs) {
    if (d === HOME || d === '/') continue;
    for (const proj of C_PROJECTS) {
      const s = proj.score(d);
      if (s >= (proj.id === 'libft' ? 10 : 50)) found.push({ ...proj, dir: d, s, kind: 'c' });
    }
    for (const spec of SPECS) {
      const s = pyScore(d, spec);
      if (s > 0) found.push({ id: spec.project, name: spec.title, tester: 'py/run_tests.py', spec, dir: d, s, kind: 'py' });
    }
  }
  // keep the best python spec per directory, drop weak matches
  const best = new Map();
  for (const f of found) {
    const k = f.kind === 'py' ? `py:${f.dir}` : `${f.id}:${f.dir}`;
    if (!best.has(k) || best.get(k).s < f.s) best.set(k, f);
  }
  const list = [...best.values()].filter((f) => f.kind !== 'py' || f.s >= Math.min(2, f.spec.exercises.length));
  // closest to where you are first, a libft inside another project last
  const dist = (f) => (f.dir === start ? 0 : start.startsWith(f.dir) ? 1 : 2) + (f.id === 'libft' && path.basename(f.dir) === 'libft' ? 3 : 0);
  list.sort((a, b) => dist(a) - dist(b) || b.s - a.s);
  // the exercise you are standing in, for python projects
  for (const f of list) {
    if (f.kind !== 'py') continue;
    const rel = path.relative(f.dir, start).split(path.sep)[0];
    const ex = f.spec.exercises.find((e) => e.file.split('/')[0] === rel);
    if (ex) f.current = ex.id;
  }
  return list;
}

// ---------------------------------------------------------------- running

function testerArgs(p) {
  const file = path.join(T, p.tester);
  const base = ['-B', file];
  return p.kind === 'py' ? [...base, '--spec', path.join(T, 'py', 'specs', `${p.id}.json`), '--project', p.dir] : [...base, '--project', p.dir];
}

function groups(p) {
  if (!fs.existsSync(path.join(T, p.tester))) return null;
  const r = spawnSync(PY, [...testerArgs(p), '--list'], { encoding: 'utf8' });
  if (r.status !== 0) return [];
  return r.stdout.split('\n').filter(Boolean).map((l) => { const [id, ...rest] = l.split('\t'); return { id, label: rest.join(' · ') || id }; });
}

function runTester(p, only = [], extra = []) {
  const args = [...testerArgs(p), ...(only.length ? ['--only', only.join(',')] : []), ...(tty ? ['--color'] : []), ...extra];
  if (process.env.NEXT_DEBUG) console.log(dim(`$ ${PY} ${args.join(' ')}`));
  console.log();
  const r = spawnSync(PY, args, { stdio: 'inherit' });
  if (r.status === 0) askStar();
  return r.status;
}

// One quiet line after a fully green run, at most once a week.
function askStar() {
  const stamp = path.join(HOME, '.cache', '42next-star');
  try { if (Date.now() - fs.statSync(stamp).mtimeMs < 7 * 24 * 3600 * 1000) return; } catch { /* never shown */ }
  try { fs.mkdirSync(path.dirname(stamp), { recursive: true }); fs.writeFileSync(stamp, ''); } catch { return; }
  console.log(dim('\nAll green. If 42next helped, a ⭐ helps others find it: https://github.com/maxencelobry/42next'));
}

// ---------------------------------------------------------------- cleaning

const GARBAGE_DIRS = new Set(['__pycache__', '.mypy_cache', '.pytest_cache', '.ruff_cache', '.dSYM']);
const GARBAGE_FILE = /(\.pyc|\.pyo|\.o|\.d|\.gch|\.swp)$|^(\.DS_Store|a\.out|vgcore\.\d+|core)$/;
const SKIP = new Set(['.git', 'node_modules', '.venv', 'venv']);

function findGarbage(dir, out = []) {
  for (const n of ls(dir)) {
    const p = path.join(dir, n);
    let st; try { st = fs.lstatSync(p); } catch { continue; }
    if (st.isSymbolicLink()) continue;
    if (st.isDirectory()) {
      if (GARBAGE_DIRS.has(n) || n.endsWith('.dSYM')) out.push(p);
      else if (!SKIP.has(n)) findGarbage(p, out);
    } else if (GARBAGE_FILE.test(n)) out.push(p);
  }
  return out;
}

async function cleanProject(dir) {
  const g = findGarbage(dir);
  if (!g.length) { console.log(green(`\nNothing to delete in ${short(dir)}.`)); return pause(); }
  console.log(bold(`\n${g.length} item(s) to delete in ${short(dir)}:`));
  g.forEach((p) => console.log('  ' + path.relative(dir, p) + (isDir(p) ? '/' : '')));
  if (await confirm('Delete all of them?')) {
    g.forEach((p) => fs.rmSync(p, { recursive: true, force: true }));
    console.log(green(`Deleted ${g.length} item(s).`));
  }
  return pause();
}

async function cleanPc() {
  const script = path.join(ROOT, 'clean-pc.sh');
  console.log(bold('\nClean the caches in your home folder:'));
  console.log(read(script).split('\n').filter((l) => l.startsWith('# -')).map((l) => '  ' + l.slice(4)).join('\n'));
  if (await confirm('Run the cleanup?')) spawnSync('bash', [script], { stdio: 'inherit', shell: process.platform === 'win32' });
  return pause();
}

// ---------------------------------------------------------------- terminal UI

function key() {
  return new Promise((res) => {
    const stdin = process.stdin;
    stdin.setRawMode(true); stdin.resume();
    stdin.once('data', (b) => { stdin.setRawMode(false); stdin.pause(); res(b.toString()); });
  });
}

// Arrow keys / j k, Enter, 1-9 to jump, q or Esc to go back.
// multi: Space toggles, Enter validates.
async function select(title, items, { multi = false, hint = '' } = {}) {
  if (!process.stdin.isTTY) return lineSelect(title, items);
  let i = 0;
  const on = new Set();
  const width = Math.min(process.stdout.columns || 80, 90);
  const rule = ruleCol('─'.repeat(width));
  const labelW = Math.min(Math.max(...items.map((it) => it.label.length)) + 2, 40);
  for (;;) {
    process.stdout.write('\x1b[2J\x1b[H');
    console.log(` ${bold(cyan('42next'))}${hint ? dim('  ·  ' + hint) : ''}`);
    console.log(rule);
    console.log(` ${bold(title)}\n`);
    const rows = process.stdout.rows ? process.stdout.rows - 8 : 30;
    const top = Math.max(0, Math.min(i - Math.floor(rows / 2), items.length - rows));
    items.slice(top, top + rows).forEach((it, k) => {
      const n = top + k;
      const mark = multi ? (on.has(n) ? green('◉ ') : dim('○ ')) : '';
      const num = !multi && n < 9 ? `${n + 1}` : ' ';
      const label = it.label.padEnd(labelW);
      const hintTxt = it.hint ? it.hint.slice(0, Math.max(0, width - labelW - 10)) : '';
      if (n === i && tty) {
        // the selected row is a full-width navy bar
        const plain = ` ▸ ${num} ${multi ? (on.has(n) ? '◉ ' : '○ ') : ''}${label}${hintTxt}`;
        const pad = ' '.repeat(Math.max(0, width - plain.length));
        const s = THEME.sel;
        console.log(`\x1b[${s}m \x1b[${THEME.acc}m▸ \x1b[${THEME.dim}m${num} \x1b[${THEME.ok}m${multi ? (on.has(n) ? '◉ ' : '○ ') : ''}\x1b[1;${THEME.acc}m${label}\x1b[22m\x1b[${THEME.dim}m${hintTxt}${pad}\x1b[0m`);
      } else if (n === i) console.log(` > ${num} ${mark}${label}${hintTxt}`);
      else console.log(`   ${dim(num)} ${mark}${label}${hintTxt ? dim(hintTxt) : ''}`);
    });
    if (items.length > rows) console.log(dim(`   ... ${items.length} items, scroll with ↑↓`));
    console.log('\n' + rule);
    console.log(dim(` ↑↓ move · enter select${multi ? ` · space tick · a all · ${on.size} ticked` : ' · 1-9 jump'} · q back`));
    const k = await key();
    if (k === '\x03') { process.stdout.write('\n'); process.exit(130); }
    if (k === 'q' || k === '\x1b') return null;
    if (k === '\x1b[A' || k === 'k') i = (i - 1 + items.length) % items.length;
    else if (k === '\x1b[B' || k === 'j') i = (i + 1) % items.length;
    else if (multi && k === ' ') { on.has(i) ? on.delete(i) : on.add(i); }
    else if (multi && k === 'a') { if (on.size === items.length) on.clear(); else items.forEach((_, n) => on.add(n)); }
    else if (k === '\r' || k === '\n') {
      process.stdout.write('\x1b[2J\x1b[H');
      return multi ? (on.size ? [...on].map((n) => items[n].value) : [items[i].value]) : items[i].value;
    } else if (!multi && /^[1-9]$/.test(k) && +k <= items.length) { process.stdout.write('\x1b[2J\x1b[H'); return items[+k - 1].value; }
  }
}

function prompt(q) {
  return new Promise((res) => {
    process.stdout.write(q);
    if (process.stdin.readableEnded) return res('');
    process.stdin.resume(); process.stdin.setEncoding('utf8');
    const done = (d) => { process.stdin.off('data', done); process.stdin.off('end', done); process.stdin.pause(); res(String(d ?? '').trim()); };
    process.stdin.on('data', done);
    process.stdin.on('end', done);
  });
}

async function lineSelect(title, items) {
  console.log(bold(title));
  items.forEach((it, n) => console.log(`  ${n + 1}. ${it.label}`));
  const a = await prompt('> ');
  const n = parseInt(a, 10);
  return n >= 1 && n <= items.length ? items[n - 1].value : null;
}

async function confirm(q) {
  if (!process.stdin.isTTY) return /^[yo]/i.test(await prompt(`${q} [y/N] `));
  process.stdout.write(`${bold(q)} ${dim('[y/N]')} `);
  const k = await key();
  console.log(k);
  return k === 'o' || k === 'y' || k === 'O' || k === 'Y';
}

async function pause() {
  if (!process.stdin.isTTY) return 'menu';
  process.stdout.write(dim('\nenter menu · r run again · q quit '));
  const k = await key();
  console.log();
  if (k === 'q' || k === '\x03') process.exit(0);
  return k === 'r' ? 'again' : 'menu';
}

// ---------------------------------------------------------------- menus

async function run(p, only, extra = []) {
  for (;;) {
    runTester(p, only, extra);
    if ((await pause()) !== 'again') return;
  }
}

async function projectMenu(p) {
  for (;;) {
    const gs = groups(p);
    if (gs === null) { console.log(red(`Tester ${p.tester} is missing.`)); await pause(); return; }
    const items = [];
    if (p.current) {
      const ex = p.spec.exercises.find((e) => e.id === p.current);
      items.push({ label: `Test ${p.current} (you are here)`, hint: ex.file, value: ['only', [p.current]] });
    }
    items.push({ label: 'Run everything', value: ['only', []] });
    items.push({ label: 'Quick check', hint: 'no valgrind, no big benchmarks', value: ['quick'] });
    items.push({ label: p.kind === 'py' ? 'Pick an exercise' : 'Pick a part / a function', hint: `${gs.length} available`, value: ['one'] });
    items.push({ label: 'Pick several', value: ['multi'] });
    if (p.kind === 'py') items.push({ label: 'Lint only (flake8 + mypy)', value: ['lint'] });
    if (p.id === 'push_swap') items.push({ label: 'Custom benchmark (size + runs)', value: ['perf'] });
    items.push({ label: 'Clean the project', hint: '__pycache__, .mypy_cache, .o, a.out...', value: ['clean'] });
    items.push({ label: 'Back to projects', value: ['back'] });

    const c = await select(`${p.name}  ${dim(short(p.dir))}`, items, { hint: p.kind === 'py' ? 'Python' : 'C' });
    if (!c || c[0] === 'back') return;
    if (c[0] === 'only') await run(p, c[1]);
    else if (c[0] === 'quick') await run(p, [], ['--quick']);
    else if (c[0] === 'lint') await run(p, [], ['--lint-only']);
    else if (c[0] === 'clean') await cleanProject(p.dir);
    else if (c[0] === 'one' || c[0] === 'multi') {
      const pick = await select(c[0] === 'one' ? 'What do you want to test?' : 'Tick what you want to test', gs.map((g) => ({ label: g.id, hint: g.label !== g.id ? g.label : '', value: g.id })), { multi: c[0] === 'multi' });
      if (pick) await run(p, Array.isArray(pick) ? pick : [pick]);
    } else if (c[0] === 'perf') {
      const size = await prompt('Stack size (e.g. 100): ');
      const runs = await prompt('Number of runs (e.g. 50): ');
      await run(p, ['perf'], ['--size', size || '100', '--runs', runs || '20']);
    }
  }
}

async function mainMenu(start) {
  let dir = start;
  for (;;) {
    const found = detect(dir);
    const items = found.map((f) => ({ label: f.name, hint: short(f.dir) + (f.current ? `  (you are in ${f.current})` : ''), value: ['proj', f] }));
    items.push({ label: 'Open another folder', value: ['dir'] });
    items.push({ label: 'Pick the project type myself', hint: 'if detection got it wrong', value: ['force'] });
    items.push({ label: 'Clean caches on this computer', hint: 'pip, mypy, VS Code, Discord, old versions...', value: ['pc'] });
    items.push({ label: 'Quit', value: ['quit'] });
    const title = found.length ? `Projects found around ${short(dir)}` : `No 42 project found in ${short(dir)}`;
    const c = await select(title, items);
    if (!c || c[0] === 'quit') return;
    if (c[0] === 'proj') await projectMenu(c[1]);
    else if (c[0] === 'pc') await cleanPc();
    else if (c[0] === 'force') {
      const all = [
        ...C_PROJECTS.map((pr) => ({ label: pr.name, value: { ...pr, kind: 'c', dir } })),
        ...SPECS.map((sp) => ({ label: sp.title, hint: 'Python', value: { id: sp.project, name: sp.title, tester: 'py/run_tests.py', spec: sp, kind: 'py', dir } })),
      ];
      const pick = await select(`Which project is ${short(dir)}?`, all);
      if (pick) await projectMenu(pick);
    }
    else if (c[0] === 'dir') {
      const subs = subdirs(dir).map((d) => ({ label: path.basename(d) + '/', value: d }));
      const pick = await select(`Folder (from ${short(dir)})`, [{ label: '.. (parent)', value: path.dirname(dir) }, { label: 'Type a path', value: '?' }, ...subs]);
      if (pick === '?') { const t = await prompt('Path: '); const r = path.resolve(dir, t.replace(/^~/, HOME)); if (isDir(r)) dir = r; }
      else if (pick) dir = pick;
    }
  }
}

// ---------------------------------------------------------------- update

// Pulls the latest tests from GitHub. Automatic once a day, silent when there
// is no network, never touches a checkout with local changes.
function update(force = false) {
  if (!exists(ROOT, '.git') || process.env.NEXT_NO_UPDATE) return;
  const stamp = path.join(HOME, '.cache', '42next-last-update');
  let last = 0;
  try { last = fs.statSync(stamp).mtimeMs; } catch { /* first run */ }
  if (!force && Date.now() - last < 24 * 3600 * 1000) return;
  try { fs.mkdirSync(path.dirname(stamp), { recursive: true }); fs.writeFileSync(stamp, ''); } catch { /* read-only home */ }
  const git = (...a) => spawnSync('git', ['-C', ROOT, ...a], { encoding: 'utf8', timeout: 4000 });
  if (git('status', '--porcelain', '--untracked-files=no').stdout.trim()) {
    if (force) console.log(yellow('local changes in ~/42next: update skipped'));
    return;
  }
  if (force) console.log(dim('checking for updates...'));
  if (git('fetch', '--quiet', 'origin').status !== 0) { if (force) console.log(yellow('no network: update skipped')); return; }
  const behind = git('rev-list', '--count', 'HEAD..@{u}').stdout.trim();
  if (!behind || behind === '0') { if (force) console.log(green('42next is up to date')); return; }
  const r = git('pull', '--ff-only', '--quiet');
  if (r.status === 0) console.log(green(`42next updated (${behind} new commit${behind === '1' ? '' : 's'}): `) + dim(git('log', '-1', '--format=%s').stdout.trim()));
  else if (force) console.log(red('update failed: ') + (r.stderr || '').trim());
}

// ---------------------------------------------------------------- CLI

const HELP = `${bold('42next')} - tester for 42 projects. Run it in your project folder.

  42next                   menu (finds the project around the current folder)
  42next <folder>          menu on that folder
  42next all               test the whole detected project, no menu
  42next quick             same, without valgrind and big benchmarks
  42next <part...>         test only these parts: ft_split, ex3, c, p100...
  42next <file.py>         test the exercise of that file
  42next list              list the testable parts of the detected project
  42next clean             clean the project (__pycache__, .mypy_cache, .o...)
  42next clean-pc          clean caches on this computer
  42next update            get the latest tests now (also automatic, once a day)
`;

async function main() {
  const args = process.argv.slice(2);
  const cwd = process.cwd();
  if (args[0] === '-h' || args[0] === '--help' || args[0] === 'help') return console.log(HELP);
  if (args[0] === 'update') return update(true);
  update();
  if (args[0] === 'clean-pc') return cleanPc();
  // a folder opens the menu there, unless it is also a part name (ex1/ in a Python project)
  if (args.length === 1 && isDir(path.resolve(args[0]))) {
    const here = detect(cwd)[0];
    if (!here || !(groups(here) || []).some((g) => g.id === args[0].replace(/\/$/, ''))) return mainMenu(path.resolve(args[0]));
    args[0] = args[0].replace(/\/$/, '');
  }
  if (!args.length) return mainMenu(cwd);

  let start = cwd;
  let only = args;
  if (args.length === 1 && fs.existsSync(args[0])) { start = path.dirname(path.resolve(args[0])); only = []; }
  const found = detect(start);
  if (!found.length) { console.log(red(`No 42 project found around ${short(start)}.`)); process.exit(2); }
  const p = found[0];
  if (args[0] === 'clean') return cleanProject(p.dir);
  if (args[0] === 'list') return (groups(p) || []).forEach((g) => console.log(`${g.id.padEnd(16)} ${dim(g.label)}`));
  if (args[0] === 'all') only = [];
  if (args[0] === 'quick') process.exit(runTester(p, [], ['--quick']) ?? 2);
  // a C file: test the part named like it (ft_split.c -> ft_split)
  if (fs.existsSync(args[0] || '') && p.kind === 'c') {
    const base = path.basename(args[0]).replace(/(_bonus)?\.c$/, '');
    if ((groups(p) || []).some((g) => g.id === base)) only = [base];
  }
  if (fs.existsSync(args[0] || '') && p.kind === 'py') {
    const rel = path.relative(p.dir, path.resolve(args[0]));
    const ex = p.spec.exercises.find((e) => e.file === rel || e.file.split('/')[0] === rel.split(path.sep)[0]);
    if (ex) only = [ex.id];
  }
  process.exit(runTester(p, only) ?? 2);
}

main().catch((e) => { console.error(red(e.stack)); process.exit(1); });

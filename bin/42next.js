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
const PY_FOUND = process.env.PYTHON || ['python3', 'python', 'py'].find((c) => spawnSync(c, ['-c', 'import sys; assert sys.version_info >= (3, 9)']).status === 0);
const PY = PY_FOUND || 'python3';
const tty = process.stdout.isTTY && !process.env.NO_COLOR;
// neutral theme: grey text, colour only for OK / KO / WARN.
// Exact colours on truecolor terminals, nearest 256-colour otherwise.
const TRUE = /truecolor|24bit/.test(process.env.COLORTERM || '');
const rgb = (hex, c256) => (TRUE
  ? `38;2;${parseInt(hex.slice(1, 3), 16)};${parseInt(hex.slice(3, 5), 16)};${parseInt(hex.slice(5, 7), 16)}`
  : `38;5;${c256}`);
const THEME = { dim: rgb('#8a8a8a', 245), ok: rgb('#4ade80', 78), ko: rgb('#f87171', 203), warn: rgb('#fbbf24', 220) };
const col = (c) => (s) => (tty ? `\x1b[${c}m${s}\x1b[0m` : String(s));
const bold = col('1'), dim = col(THEME.dim), green = col(THEME.ok), red = col(THEME.ko), yellow = col(THEME.warn);

// ---------------------------------------------------------------- helpers

const exists = (...p) => fs.existsSync(path.join(...p));
const isDir = (p) => { try { return fs.statSync(p).isDirectory(); } catch { return false; } };
const ls = (d) => { try { return fs.readdirSync(d); } catch { return []; } };
const read = (p) => { try { return fs.readFileSync(p, 'utf8'); } catch { return ''; } };
const subdirs = (d) => ls(d).filter((n) => !n.startsWith('.') && n !== 'node_modules' && isDir(path.join(d, n))).map((n) => path.join(d, n));
const short = (p) => (p.startsWith(HOME) ? '~' + p.slice(HOME.length) : p);
// git in `dir`: a missing git gives status null and empty output, never a throw
const gitIn = (dir, a, o = {}) => {
  const r = spawnSync('git', ['-C', dir, ...a], { encoding: 'utf8', ...o });
  return { ...r, stdout: r.stdout || '', stderr: r.stderr || '' };
};

function findFile(dir, test, depth = 2) {
  for (const n of ls(dir)) {
    const p = path.join(dir, n);
    if (n.startsWith('.')) continue;
    if (isDir(p)) { if (depth > 0) { const r = findFile(p, test, depth - 1); if (r) return r; } } else if (test(n, p)) return p;
  }
  return null;
}

// ---------------------------------------------------------------- projects

// Python projects in the order of the Common Core
const PY_ORDER = ['growing_code', 'code_cultivation', 'garden_guardian', 'data_quest', 'data_archivist', 'code_nexus', 'the_codex',
  'datadeck', 'funcmage', 'the_matrix', 'cosmic_data'];
const pyRank = (sp) => { const i = PY_ORDER.indexOf(sp.project); return i < 0 ? PY_ORDER.length : i; };
const SPECS = ls(path.join(T, 'py', 'specs')).filter((f) => f.endsWith('.json'))
  .map((f) => { try { return JSON.parse(read(path.join(T, 'py', 'specs', f))); } catch { return null; } })
  .filter(Boolean)
  .sort((a, b) => pyRank(a) - pyRank(b));

// Each detector gets a directory and returns a score (0 = not this project).
const C_PROJECTS = [
  {
    id: 'libft', name: 'libft', group: 'C', tester: 'libft/run.py',
    score: (d) => (exists(d, 'libft.h') ? 1 : 0) * ls(d).filter((n) => /^ft_.*\.c$/.test(n)).length,
  },
  {
    id: 'printf', name: 'ft_printf', group: 'C', tester: 'printf/run.py',
    score: (d) => (/libftprintf\.a/.test(read(path.join(d, 'Makefile'))) ? 50 : 0)
      // the cheap test first: findFile reads every .h two levels down
      + (exists(d, 'Makefile') && findFile(d, (n, p) => n.endsWith('.h') && /ft_printf\s*\(/.test(read(p))) ? 30 : 0),
  },
  {
    id: 'gnl', name: 'get_next_line', group: 'C', tester: 'gnl/run.py',
    score: (d) => (exists(d, 'get_next_line.c') ? 50 : 0) + (exists(d, 'get_next_line.h') ? 10 : 0),
  },
  {
    id: 'push_swap', name: 'push_swap', group: 'C', tester: 'push_swap/run.py',
    score: (d) => (/push_swap/.test(read(path.join(d, 'Makefile'))) ? 50 : 0) + (exists(d, 'push_swap.h') ? 20 : 0),
  },
  {
    id: 'born2beroot', name: 'Born2beRoot', group: 'Sysadmin', tester: 'born2beroot/run.py',
    score: (d) => (exists(d, 'signature.txt') ? 50 : 0),
  },
  {
    id: 'netpractice', name: 'NetPractice', group: 'Sysadmin', tester: 'netpractice/run.py',
    // "Get my config" exports: {"routes":{...},"ifs":{...}}, level1.json ...
    score: (d) => {
      const n = ls(d).filter((f) => f.endsWith('.json') && /"routes"\s*:/.test(read(path.join(d, f))) && /"ifs"\s*:/.test(read(path.join(d, f)))).length;
      return n ? 50 + n : 0;
    },
  },
  {
    id: 'inception', name: 'Inception', group: 'Sysadmin', tester: 'inception/run.py',
    score: (d) => (exists(d, 'Makefile') && (exists(d, 'srcs', 'docker-compose.yml') || exists(d, 'srcs', 'docker-compose.yaml')) ? 60 : 0),
  },
  // Python programs with their own tester (names imposed by the subjects)
  {
    id: 'amazeing', name: 'A-Maze-ing', group: 'Python', tester: 'amazeing/run.py',
    score: (d) => (exists(d, 'a_maze_ing.py') ? 60 : 0),
  },
  {
    id: 'pacman', name: 'Pacman', group: 'Python', tester: 'pacman/run.py',
    score: (d) => (exists(d, 'pac-man.py') ? 60 : 0),
  },
];

function pyScore(d, spec) {
  return spec.exercises.filter((ex) => exists(d, ex.file)).length;
}

// Folders never scanned: a drive root, the home folder and its parents, and
// the Windows home seen from WSL (/mnt/c/Users/<name>). Reading every file
// two levels down there (AppData...) takes minutes, and no project lives
// directly in them.
const HOME_UP = new Set();
for (let p = HOME; ; p = path.dirname(p)) { HOME_UP.add(p); if (path.dirname(p) === p) break; }
const tooBig = (d) => HOME_UP.has(d) || path.dirname(d) === d
  || /^\/mnt(\/[a-z](\/Users(\/[^/]+)?)?)?\/?$/i.test(d)
  || /^[a-z]:[\\/](Users([\\/][^\\/]+)?)?[\\/]?$/i.test(d)
  || /^\/Users(\/[^/]+)?\/?$/.test(d);

// Look around `start`: itself, its parents (when run from ex3/ or src/), its
// children and grandchildren (when run from ~/doc/cc/libft with the repo inside).
function detect(start) {
  const dirs = new Set([start]);
  let p = start;
  // 5 levels up: Inception's srcs/requirements/nginx/conf is 4 below the root
  for (let i = 0; i < 5; i++) { p = path.dirname(p); dirs.add(p); }
  for (const c of subdirs(start)) { dirs.add(c); for (const g of subdirs(c)) dirs.add(g); }

  const found = [];
  for (const d of dirs) {
    if (tooBig(d)) continue;
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
  // one exercise file is enough: a project you just started (only ex0/) is found
  const list = [...best.values()];
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

// The testers need python 3.9+: say it and stop, instead of an empty menu.
function noPython() {
  console.log(red('python3 3.9+ is required to run the tests: https://www.python.org/downloads/'));
  process.exit(2);
}

function groups(p) {
  if (!fs.existsSync(path.join(T, p.tester))) return null;
  if (!PY_FOUND) noPython();
  const r = spawnSync(PY, [...testerArgs(p), '--list'], { encoding: 'utf8' });
  if (r.error) noPython();
  if (r.status !== 0) return [];
  return r.stdout.split('\n').filter(Boolean).map((l) => { const [id, ...rest] = l.split('\t'); return { id, label: rest.join(' · ') || id }; });
}

function runTester(p, only = [], extra = [], env = {}) {
  if (!PY_FOUND) noPython();
  const args = [...testerArgs(p), ...(only.length ? ['--only', only.join(',')] : []), ...(tty && !extra.includes('--no-color') ? ['--color'] : []), ...extra];
  if (process.env.NEXT_DEBUG) console.log(dim(`$ ${PY} ${args.join(' ')}`));
  // ctrl-c goes to the tester only: it stops, cleans up, and the menu stays
  const ignore = () => {};
  process.on('SIGINT', ignore);
  const r = spawnSync(PY, args, { stdio: 'inherit', env: { ...process.env, ...env } });
  process.off('SIGINT', ignore);
  if (r.error) noPython();
  if (r.status === 0) askStar();
  return r.status ?? (r.signal === 'SIGINT' ? 130 : 2);
}

// A job is what to run: { only: [ids], extra: [flags], commit: bool, keep: bool }.
const jobText = (j) => [
  j.commit ? 'last commit' : '',
  j.extra.includes('--quick') ? 'quick' : j.extra.includes('--lint-only') ? 'lint only' : '',
  j.only.includes('perf') && j.extra.includes('--size') ? `benchmark ${j.extra[j.extra.indexOf('--size') + 1]} numbers` : '',
  j.only.length && !j.only.includes('perf') ? j.only.join(', ') : '',
].filter(Boolean).join(' · ') || 'everything';

// The last job per project folder, for "Run again" at the top of the menu.
const LAST = path.join(HOME, '.cache', '42next-last.json');
function lastJob(dir) {
  try { return JSON.parse(read(LAST))[dir] || null; } catch { return null; }
}
function saveJob(dir, job) {
  let all = {};
  try { all = JSON.parse(read(LAST)); } catch { /* first one */ }
  all[dir] = { only: job.only, extra: job.extra, commit: !!job.commit };
  try { fs.mkdirSync(path.dirname(LAST), { recursive: true }); fs.writeFileSync(LAST, JSON.stringify(all)); } catch { /* read-only home */ }
}

// Export the last git commit of `dir` (only that folder) to a temporary
// folder: exactly what is graded, without the files git does not have.
function exportCommit(dir) {
  const git = (...a) => gitIn(dir, a);
  const pre = git('rev-parse', '--show-prefix');
  if (pre.status !== 0) return { error: 'this folder is not in a git repository' };
  const head = git('log', '-1', '--format=%h %s');
  if (head.status !== 0) return { error: 'no commit yet' };
  const prefix = pre.stdout.trim().replace(/\/$/, '');
  const ar = spawnSync('git', ['-C', dir, 'archive', '--format=tar', prefix ? `HEAD:${prefix}` : 'HEAD'], { maxBuffer: 1 << 30 });
  if (ar.status !== 0) return { error: String(ar.stderr || '').trim() || 'git archive failed' };
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), '42next-commit-'));
  const x = spawnSync('tar', ['-x', '-C', tmp], { input: ar.stdout });
  if (x.status !== 0) { fs.rmSync(tmp, { recursive: true, force: true }); return { error: 'tar failed' }; }
  const dirty = git('status', '--porcelain', '--', '.').stdout.split('\n').filter(Boolean).length;
  const up = git('rev-list', '--count', '@{u}..HEAD');
  return { tmp, head: head.stdout.trim(), dirty, ahead: up.status === 0 ? +up.stdout.trim() : null };
}
const inGit = (dir) => gitIn(dir, ['rev-parse', '--is-inside-work-tree']).stdout.trim() === 'true';

// Run a job. Returns { status, failed } where failed is the list of the
// group ids that failed, or null when the tester could not tell.
function runJob(p, job) {
  saveJob(p.dir, job);
  const out = path.join(os.tmpdir(), `42next-failed-${process.pid}`);
  const env = { NEXT42_FAILED: out };
  let target = p, exp = null;
  if (job.commit) {
    exp = exportCommit(p.dir);
    if (exp.error) { console.log(red(`\n cannot test the last commit: ${exp.error}`)); return { status: 2, failed: null }; }
    console.log(bold(`\n testing the last commit: ${exp.head}`));
    if (exp.dirty) console.log(yellow(` ${exp.dirty} file(s) changed since this commit: not tested (commit them)`));
    if (exp.ahead === null) console.log(yellow(' no remote branch: nothing is pushed yet'));
    else if (exp.ahead) console.log(yellow(` ${exp.ahead} commit(s) not pushed yet (git push)`));
    target = { ...p, dir: exp.tmp };
    env.NEXT42_PROJECT_LABEL = `${short(p.dir)}  @ ${exp.head.split(' ')[0]}`;
  }
  console.log(dim(' ctrl-c stops the test and brings you back'));
  try { fs.rmSync(out, { force: true }); } catch { /* nothing */ }
  const status = runTester(target, job.only, [...job.extra, ...(job.keep ? ['--keep'] : [])], env);
  if (exp) fs.rmSync(exp.tmp, { recursive: true, force: true });
  let failed = null;
  try {
    const lines = read(out).split('\n').filter(Boolean);
    failed = fs.existsSync(out) && !lines.includes('*') ? lines : null;
    fs.rmSync(out, { force: true });
  } catch { /* no result file */ }
  return { status, failed };
}

// Files whose change re-runs the tests in watch mode.
const WATCHED = /\.(c|h|py|json|txt|md)$|^Makefile$/;
function snapshot(dir, acc = []) {
  for (const n of ls(dir)) {
    const p = path.join(dir, n);
    let st; try { st = fs.statSync(p); } catch { continue; }
    if (st.isDirectory()) { if (!SKIP.has(n) && !GARBAGE_DIRS.has(n) && !n.startsWith('.')) snapshot(p, acc); } else if (WATCHED.test(n)) acc.push(`${p}:${st.mtimeMs}:${st.size}`);
  }
  return acc.join('\n');
}

// Re-run the job each time a source file is saved. q or enter stops.
async function watch(p, job) {
  for (;;) {
    process.stdout.write(CLEAR);
    const before = snapshot(p.dir);   // taken first: a save during the run re-runs
    runJob(p, { ...job, keep: false });
    if (!process.stdin.isTTY) return 'menu';
    process.stdout.write('\n' + dim(` watching ${short(p.dir)}: save a file to run again   r now   q stop `));
    const why = await new Promise((res) => {
      const stdin = process.stdin;
      const done = (v) => { clearInterval(t); stdin.off('data', onKey); stdin.setRawMode(false); stdin.pause(); res(v); };
      const onKey = (b) => { const k = b.toString(); if (k === '\x03') { process.stdout.write('\n'); process.exit(130); } if (k === 'r') done('run'); else if (k === 'q' || k === '\r' || k === '\x1b' || k === '\x1b[D') done('stop'); };
      const t = setInterval(() => { if (snapshot(p.dir) !== before) done('run'); }, 500);
      stdin.setRawMode(true); stdin.resume(); stdin.on('data', onKey);
    });
    if (why === 'stop') { console.log(); return 'menu'; }
    await new Promise((r) => setTimeout(r, 300));   // let the editor finish writing
  }
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
  const script = path.join(__dirname, 'clean-pc.sh');
  console.log(bold('\nClean the caches in your home folder:'));
  console.log(read(script).split('\n').filter((l) => l.startsWith('# -')).map((l) => '  ' + l.slice(4)).join('\n'));
  if (await confirm('Run the cleanup?')) spawnSync('bash', [script], { stdio: 'inherit', shell: process.platform === 'win32' });
  return pause();
}

// ---------------------------------------------------------------- terminal UI

const strip = (s) => String(s).replace(/\x1b\[[0-9;]*m/g, '');
const fit = (s, n) => (s.length <= n ? s : n < 2 ? s.slice(0, Math.max(0, n)) : s.slice(0, n - 1) + '…');
// Shorten a path from the middle and keep its end: ~/…/cc/libft
function fitPath(p, n) {
  if (p.length <= n) return p;
  const parts = p.split('/');
  let tail = parts.pop();
  while (parts.length > 1 && `${parts[0]}/…/${parts[parts.length - 1]}/${tail}`.length <= n) tail = `${parts.pop()}/${tail}`;
  const s = `${parts[0]}/…/${tail}`;
  return s.length <= n ? s : '…' + p.slice(p.length - n + 1);
}

const CLEAR = '\x1b[H\x1b[2J';
if (tty) process.on('exit', () => process.stdout.write('\x1b[?25h'));

function key() {
  return new Promise((res) => {
    const stdin = process.stdin;
    stdin.setRawMode(true); stdin.resume();
    stdin.once('data', (b) => { stdin.setRawMode(false); stdin.pause(); res(b.toString()); });
  });
}

// One screen, no frame:
//    42next · libft · C
//    ~/…/cc/libft
//
//    SECTION
//    ▸ 1  Label       hint
//
//    ↑↓ move   enter run   q back
// items: { label, hint, value } to select, { section } for a heading,
// { gap: true } for an empty line. Returns the value, or null on q / Esc.
// multi: space ticks, enter returns the ticked values (or the current one).
// state: { pos } keeps the cursor where it was when you come back.
async function select({ title, sub = '', tag = '', items, multi = false, enter = 'select', back = 'back', state = {} }) {
  const pick = items.map((it, n) => (it.value !== undefined ? n : -1)).filter((n) => n >= 0);
  if (!pick.length) {
    console.log(dim('\n nothing to test'));
    if (process.stdin.isTTY) { process.stdout.write(dim(' enter back ')); const k = await key(); console.log(); if (k === '\x03') process.exit(130); }
    return null;
  }
  if (!process.stdin.isTTY) { const v = await lineSelect(title, items); return multi && v !== null ? [v] : v; }
  const I = Math.max(40, Math.min(process.stdout.columns || 80, 78)) - 2;
  const row = (txt = '') => ' ' + txt;
  const labelW = Math.min(Math.max(...pick.map((n) => items[n].label.length)) + 2, Math.floor(I / 2));
  // the screen lines of the list, a heading gets an empty line above it
  const lines = [];
  items.forEach((it, n) => {
    if (it.section !== undefined && lines.length) lines.push({ blank: true });
    lines.push(it.gap ? { blank: true } : { n });
  });
  const head = ['42next', title, tag].filter(Boolean);
  let i = Math.min(state.pos || 0, pick.length - 1);
  const on = new Set();
  if (tty) process.stdout.write('\x1b[?25l');
  for (;;) {
    const cur = pick[i];
    const keys = ['↑↓ move', ...(multi ? ['space tick', 'a all'] : []), `enter ${enter}`, ...(multi ? [] : ['1-9 jump']), `q ${back}`];
    const foot = keys.join('   ') + (multi && on.size ? `   ${on.size} ticked` : '');
    const fixed = 5 + (sub ? 1 : 0);
    const avail = Math.max(5, (process.stdout.rows || 30) - fixed - 1);
    const at = lines.findIndex((l) => l.n === cur);
    const start = lines.length <= avail ? 0 : Math.max(0, Math.min(at - Math.floor(avail / 2), lines.length - avail));
    const shown = lines.slice(start, start + avail);
    const out = ['', row(fit(head.join(' · '), I).split(' · ').map((h, k) => (k === 1 ? bold(h) : dim(h))).join(dim(' · ')))];
    if (sub) out.push(row(dim(fitPath(sub, I))));
    out.push('');
    shown.forEach((l, k) => {
      if (k === 0 && start > 0) return out.push(row(dim('  ↑ more')));
      if (k === shown.length - 1 && start + avail < lines.length) return out.push(row(dim('  ↓ more')));
      if (l.blank) return out.push('');
      const it = items[l.n];
      if (it.section !== undefined) return out.push(row(dim(it.section.toUpperCase())));
      const pos = pick.indexOf(l.n);
      const sel = l.n === cur;
      const mark = multi ? (on.has(pos) ? '◉ ' : '○ ') : (pos < 9 ? `${pos + 1} ` : '  ');
      const label = fit(it.label, labelW - 2).padEnd(labelW);
      const room = I - 5 - labelW;
      const hint = it.hint && room >= 6 ? fit(it.hint, room) : '';
      const m = multi && on.has(pos) ? green(mark) : dim(mark);
      out.push(row(`${sel ? bold('▸') : ' '} ${m} ${sel ? bold(label) : label}${hint ? dim(hint) : ''}`));
    });
    out.push('', row(dim(fit(foot, I))));
    process.stdout.write(CLEAR + out.join('\n') + '\n');
    const k = await key();
    if (k === '\x03') { process.stdout.write('\n'); process.exit(130); }
    state.pos = i;
    // q, Esc, ← or backspace: back. →: same as enter.
    if (k === 'q' || k === '\x1b' || k === '\x1b[D' || k === '\x7f' || k === '\b') { process.stdout.write(CLEAR); return null; }
    if (k === '\x1b[A' || k === 'k') i = (i - 1 + pick.length) % pick.length;
    else if (k === '\x1b[B' || k === 'j') i = (i + 1) % pick.length;
    else if (multi && k === ' ') { if (on.has(i)) on.delete(i); else on.add(i); }
    else if (multi && k === 'a') { if (on.size === pick.length) on.clear(); else pick.forEach((_, n) => on.add(n)); }
    else if (k === '\r' || k === '\n' || k === '\x1b[C') {
      process.stdout.write(CLEAR);
      return multi ? (on.size ? [...on].sort((a, b) => a - b).map((n) => items[pick[n]].value) : [items[cur].value]) : items[cur].value;
    } else if (!multi && /^[1-9]$/.test(k) && +k <= pick.length) { state.pos = +k - 1; process.stdout.write(CLEAR); return items[pick[+k - 1]].value; }
  }
}

function prompt(q) {
  return new Promise((res) => {
    if (tty) process.stdout.write('\x1b[?25h');
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
  const pick = [];
  for (const it of items) {
    if (it.section !== undefined) console.log(dim(it.section));
    else if (it.value !== undefined) { pick.push(it); console.log(`  ${pick.length}. ${it.label}`); }
  }
  const n = parseInt(await prompt('> '), 10);
  return n >= 1 && n <= pick.length ? pick[n - 1].value : null;
}

async function confirm(q) {
  if (!process.stdin.isTTY) return /^[yo]/i.test(await prompt(`${q} [y/N] `));
  process.stdout.write(`${bold(q)} ${dim('[y/N]')} `);
  const k = await key();
  console.log(k);
  return k === 'o' || k === 'y' || k === 'O' || k === 'Y';
}

// After a clean: back to the menu or quit.
async function pause() {
  if (!process.stdin.isTTY) return 'menu';
  process.stdout.write('\n' + dim(' enter back   q quit') + ' ');
  const k = await key();
  console.log();
  if (k === 'q' || k === '\x03') process.exit(0);
  return 'menu';
}

// After a run: what next. Returns 'menu', 'pick', or the next job to run.
async function afterRun(p, job, res) {
  if (!process.stdin.isTTY) return 'menu';
  const failed = res.failed && res.failed.length ? res.failed : null;
  const keys = [['r', 'run again']];
  if (failed) keys.push(['f', `only the ${failed.length} failed part${failed.length > 1 ? 's' : ''}`]);
  keys.push(['c', 'choose other parts']);
  if (!job.commit) keys.push(['w', 'watch: run again on save']);
  if (p.kind === 'c') keys.push(['k', 'keep the build folder']);
  keys.push(['enter', 'back'], ['q', 'quit']);
  const txt = keys.map(([k, what]) => `${bold(k)} ${dim(what)}`);
  const half = Math.ceil(txt.length / 2);
  process.stdout.write(`\n ${txt.slice(0, half).join('   ')}\n ${txt.slice(half).join('   ')} `);
  for (;;) {
    const k = await key();
    if (k === 'q' || k === '\x03') { console.log(); process.exit(0); }
    if (k === '\r' || k === '\n' || k === '\x1b' || k === '\x1b[D' || k === '\x7f') { console.log(); return 'menu'; }
    if (k === 'r') return { ...job, keep: false };
    if (k === 'f' && failed) return { ...job, only: failed, keep: false };
    if (k === 'c') return 'pick';
    if (k === 'w' && !job.commit) return { ...job, watch: true, keep: false };
    if (k === 'k' && p.kind === 'c') return { ...job, keep: true };
  }
}

// ---------------------------------------------------------------- menus

// what --quick leaves out, per tester (no entry: the tester has no quick mode)
const QUICK = {
  libft: 'no valgrind', printf: 'no valgrind, no ASan', gnl: 'no valgrind, fewer BUFFER_SIZE',
  push_swap: 'no valgrind, fewer benchmark runs',
};
const CHECK_IDS = new Set(['files', 'norm', 'makefile', 'functions', 'readme', 'repo']);

// Run a job, then whatever is picked after it, until back or pick.
async function run(p, job) {
  for (;;) {
    if (job.watch) { const { watch: _, ...j } = job; await watch(p, j); return 'menu'; }
    process.stdout.write(CLEAR);
    const res = runJob(p, job);
    const next = await afterRun(p, job, res);
    if (typeof next === 'string') return next;
    job = next;
  }
}

// The parts of a project, under headings: checks, then the tests.
function partItems(p, gs) {
  const secs = new Map();
  for (const g of gs) {
    if (g.id === 'perf') continue;  // has its own entry: Custom benchmark
    const sec = CHECK_IDS.has(g.id) ? 'Checks' : p.kind === 'py' ? 'Exercises' : /^ft_/.test(g.id) ? 'Functions' : 'Tests';
    let hint = g.label.startsWith(g.id + ' · ') ? g.label.slice(g.id.length + 3) : g.label;
    if (hint === g.id || hint === `tests for ${g.id}`) hint = '';
    if (!secs.has(sec)) secs.set(sec, []);
    secs.get(sec).push({ label: g.id, hint, value: g.id });
  }
  return [...secs].flatMap(([sec, its]) => [{ section: sec }, ...its]);
}

async function projectMenu(p) {
  const tag = p.kind === 'py' ? 'Python' : 'C';
  const gs = groups(p);
  if (gs === null) { console.log(red(`Tester ${p.tester} is missing.`)); await pause(); return; }
  const n = gs.filter((g) => g.id !== 'perf').length;
  const parts = partItems(p, gs);
  const git = inGit(p.dir);
  const job = (only = [], extra = [], more = {}) => ({ only, extra, ...more });
  const menuState = {}, pickState = {}, watchState = {};

  // after a run, `c` opens the part picker straight away
  let next = null;
  for (;;) {
    const items = [{ section: 'Test' }];
    const last = lastJob(p.dir);
    if (last) items.push({ label: 'Run again', hint: jobText(last), value: ['job', last] });
    if (p.current) {
      const ex = p.spec.exercises.find((e) => e.id === p.current);
      items.push({ label: `Test ${p.current}`, hint: `you are here · ${ex.file}`, value: ['job', job([p.current])] });
    }
    items.push({ label: 'Test everything', value: ['job', job()] });
    if (p.kind === 'py' || QUICK[p.id]) items.push({ label: 'Quick test', hint: p.kind === 'py' ? 'no mypy' : QUICK[p.id], value: ['job', job([], ['--quick'])] });
    items.push({ label: 'Choose what to test', hint: `${n} ${p.kind === 'py' ? 'exercises' : 'parts'}`, value: ['pick'] });
    items.push({ label: 'Watch mode', hint: 'runs again each time you save', value: ['watch'] });
    if (p.kind === 'py') items.push({ label: 'Lint only', hint: 'flake8 + mypy', value: ['job', job([], ['--lint-only'])] });
    if (p.id === 'push_swap') items.push({ label: 'Custom benchmark', hint: 'you pick the size and the runs', value: ['perf'] });
    items.push({ section: 'Project' });
    if (git) items.push({ label: 'Test the last commit', hint: 'only what git has, like the moulinette', value: ['job', job([], [], { commit: true })] });
    items.push({ label: 'Clean the project', hint: p.kind === 'py' ? '__pycache__, .mypy_cache' : '.o, a.out, core files', value: ['clean'] });
    items.push({ gap: true }, { label: 'Back', hint: 'all projects, another folder', value: ['back'] });

    const c = next === 'pick' ? ['pick'] : await select({ title: p.name, sub: short(p.dir), tag, items, enter: 'run', state: menuState });
    next = null;
    if (!c || c[0] === 'back') return;
    if (c[0] === 'job') next = await run(p, c[1]);
    else if (c[0] === 'clean') await cleanProject(p.dir);
    else if (c[0] === 'pick') {
      const pick = await select({ title: p.name, tag: 'choose what to test', sub: 'enter runs the current line · space ticks several', items: parts, multi: true, enter: 'run', state: pickState });
      if (pick) next = await run(p, job(pick));
    } else if (c[0] === 'watch') {
      const pick = await select({
        title: p.name, tag: 'watch mode', sub: 'what to run again each time you save a file', multi: true, enter: 'start', state: watchState,
        items: [{ label: 'everything', value: '*' }, ...parts],
      });
      if (pick) next = await run(p, job(pick.includes('*') ? [] : pick, [], { watch: true }));
    } else if (c[0] === 'perf') {
      const num = (s, d) => (/^\d+$/.test(s) ? s : d);
      const size = num(await prompt('Stack size [100]: '), '100');
      const runs = num(await prompt('Number of runs [20]: '), '20');
      next = await run(p, job(['perf'], ['--size', size, '--runs', runs]));
    }
  }
}

async function mainMenu(start) {
  let dir = start;
  let auto = true;    // a single project found: open it straight away
  const mainState = {};
  for (;;) {
    const found = detect(dir);
    if (auto && found.length === 1) { auto = false; await projectMenu(found[0]); continue; }
    auto = false;
    const where = (f) => {
      const rel = path.relative(dir, f.dir);
      const at = !rel ? 'this folder' : rel.startsWith('..') ? short(f.dir) : rel + '/';
      return at + (f.current ? ` · you are in ${f.current}` : '');
    };
    const items = [];
    if (found.length) items.push({ section: 'Projects' }, ...found.map((f) => ({ label: f.name, hint: where(f), value: ['proj', f] })));
    items.push({ section: 'Other' });
    items.push({ label: 'Open another folder', value: ['dir'] });
    items.push({ label: 'Choose the project type', hint: 'if detection is wrong', value: ['force'] });
    items.push({ label: 'Clean this computer', hint: 'caches, trash, old versions', value: ['pc'] });
    items.push({ gap: true }, { label: 'Quit', value: ['quit'] });
    const title = found.length ? `${found.length} project${found.length > 1 ? 's' : ''} found` : 'No 42 project found here';
    const c = await select({ title, sub: short(dir), items, enter: 'open', back: 'quit', state: mainState });
    if (!c || c[0] === 'quit') return;
    if (c[0] === 'proj') await projectMenu(c[1]);
    else if (c[0] === 'pc') await cleanPc();
    else if (c[0] === 'force') {
      const all = [
        ...['C', 'Sysadmin'].flatMap((g) => [{ section: g },
          ...C_PROJECTS.filter((pr) => pr.group === g).map((pr) => ({ label: pr.name, value: { ...pr, kind: 'c', dir } }))]),
        { section: 'Python' },
        ...SPECS.map((sp) => ({ label: sp.title, value: { id: sp.project, name: sp.title, tester: 'py/run_tests.py', spec: sp, kind: 'py', dir } })),
        ...C_PROJECTS.filter((pr) => pr.group === 'Python').map((pr) => ({ label: pr.name, value: { ...pr, kind: 'c', dir } })),
      ];
      const pick = await select({ title: 'Which project is this folder?', sub: short(dir), items: all });
      if (pick) await projectMenu(pick);
    } else if (c[0] === 'dir') {
      const subs = subdirs(dir).map((d) => ({ label: path.basename(d) + '/', value: d }));
      const pick = await select({
        title: 'Open a folder', sub: short(dir), enter: 'open',
        items: [{ label: '..', hint: 'parent folder', value: path.dirname(dir) }, { label: 'Type a path', value: '?' },
          ...(subs.length ? [{ section: 'Folders' }, ...subs] : [])],
      });
      if (pick === '?') {
        const t = await prompt('Path: ');
        const r = path.resolve(dir, t.replace(/^~/, HOME));
        if (isDir(r)) { dir = r; auto = true; mainState.pos = 0; }
      } else if (pick) { dir = pick; auto = true; mainState.pos = 0; }
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
  removeStaleLocks(path.join(ROOT, '.git'));
  const git = (...a) => gitIn(ROOT, a);
  const st = git('status', '--porcelain', '--untracked-files=no');
  if (st.status !== 0) { if (force) console.log(yellow('git is not available: update skipped')); return; }
  if (st.stdout.trim()) {
    if (force) console.log(yellow(`local changes in ${short(ROOT)}: update skipped`));
    return;
  }
  if (force) console.log(dim('checking for updates...'));
  // only the fetch needs the network, so only the fetch gets a short timeout
  if (gitIn(ROOT, ['fetch', '--quiet', 'origin'], { timeout: 4000 }).status !== 0) { if (force) console.log(yellow('no network: update skipped')); return; }
  const behind = git('rev-list', '--count', 'HEAD..@{u}').stdout.trim();
  if (!behind || behind === '0') { if (force) console.log(green('42next is up to date')); return; }
  // local, no timeout: a merge killed halfway leaves a lock behind
  const r = git('merge', '--ff-only', '--quiet', '@{u}');
  if (r.status === 0) {
    console.log(green(`42next updated (${behind} new commit${behind === '1' ? '' : 's'}): `) + dim(git('log', '-1', '--format=%s').stdout.trim()));
    // this process still runs the old code: let the new one finish the
    // install now (shortcuts, folder move), not at the next command
    spawnSync(process.execPath, [path.join(ROOT, 'bin', '42next.js'), '--post-update'], { stdio: 'inherit', env: { ...process.env, NEXT_NO_UPDATE: '1' } });
  } else if (force) console.log(red('update failed: ') + (r.stderr || '').trim());
}

// A git killed halfway (closed terminal, timeout) can leave *.lock files, and
// every later git command fails. A lock older than 10 minutes belongs to nobody.
function removeStaleLocks(gitDir) {
  const old = Date.now() - 10 * 60 * 1000;
  const check = (p) => { try { if (fs.statSync(p).mtimeMs < old) fs.rmSync(p, { force: true }); } catch { /* no lock */ } };
  ['index.lock', 'HEAD.lock', 'ORIG_HEAD.lock', 'FETCH_HEAD.lock', 'packed-refs.lock', 'config.lock', 'shallow.lock'].forEach((n) => check(path.join(gitDir, n)));
  const walk = (d) => ls(d).forEach((n) => { const p = path.join(d, n); if (isDir(p)) walk(p); else if (n.endsWith('.lock')) check(p); });
  walk(path.join(gitDir, 'refs'));
}

// Short names for installs made by install.sh before they existed: 42n,
// and 42 unless another tool (42-cli...) already uses that name.
function shortcuts() {
  if (process.platform === 'win32') return;
  const bin = path.join(HOME, '.local', 'bin');
  const main = path.join(bin, '42next');
  const real = (p) => { try { return fs.realpathSync(p); } catch { return ''; } };
  if (!real(main) || real(main) !== real(path.join(ROOT, 'bin', '42next.js'))) return;
  let target;
  try { target = fs.readlinkSync(main); } catch { return; }
  const onPath = (n) => (process.env.PATH || '').split(path.delimiter).some((d) => d && exists(d, n));
  for (const name of ['42n', '42']) {
    const p = path.join(bin, name);
    try { fs.lstatSync(p); continue; } catch { /* free */ }
    if (name === '42' && onPath('42')) continue;
    try { fs.symlinkSync(target, p); console.log(dim(`shortcut added: ${name} (same as 42next)`)); } catch { /* read-only */ }
  }
}

// Older installs lived in ~/42next. Move them to ~/.local/share/42next (out of
// sight), point the ~/.local/bin/42next link there and run again from there.
// Only for an install made by install.sh: a clone elsewhere is never moved.
function migrate() {
  if (process.platform === 'win32') return;
  const real = (p) => { try { return fs.realpathSync(p); } catch { return ''; } };
  const old = path.join(HOME, '42next');
  const dest = path.join(HOME, '.local', 'share', '42next');
  const link = path.join(HOME, '.local', 'bin', '42next');
  // a symlinked ~/42next points to a clone elsewhere: leave it
  try { if (!fs.lstatSync(old).isDirectory()) return; } catch { return; }
  if (real(ROOT) !== real(old) || fs.existsSync(dest)) return;
  if (real(link) !== path.join(real(old), 'bin', '42next.js')) return;
  const origin = gitIn(old, ['remote', 'get-url', 'origin']).stdout;
  if (!origin.includes('maxencelobry/42next')) return;
  try {
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    fs.renameSync(old, dest);
  } catch { return; }
  try {
    fs.symlinkSync(path.join(dest, 'bin', '42next.js'), link + '.new');
    fs.renameSync(link + '.new', link);
  } catch {
    try { fs.rmSync(link + '.new', { force: true }); fs.renameSync(dest, old); } catch { /* keep the move */ }
    return;
  }
  console.log(dim(`42next moved to ${short(dest)}`));
  process.on('SIGINT', () => {}); // ctrl-c belongs to the new process
  const r = spawnSync(process.execPath, [link, ...process.argv.slice(2)], { stdio: 'inherit' });
  process.exit(r.status ?? 1);
}

// ---------------------------------------------------------------- CLI

const HELP = `${bold('42next')} - tester for 42 projects. Run it in your project folder.
Shorter: 42n (and 42 when no other tool uses that name) is the same command.

  42next                   menu (finds the project around the current folder)
  42next <folder>          menu on that folder
  42next all               test the whole detected project, no menu
  42next quick [part...]   same, skip the slow checks (valgrind, big benchmarks, mypy)
  42next <part...>         test only these parts: ft_split, ex3, c, p100...
  42next <file...>         test the parts of these files: ft_split.c, ex3/main.py
  42next list              list the testable parts of the detected project
  42next watch [part...]   run again each time you save a file
  42next commit [part...]  test the last git commit only (what is graded)
  42next clean             clean the project (__pycache__, .mypy_cache, .o...)
  42next clean-pc          clean caches on this computer
  42next update            get the latest tests now (also automatic, once a day)

  Options go to the tester: --verbose (passing checks too), --keep (keep the
  build folder), --quick, --no-color. Example: 42next ft_split --verbose
`;

// A word of the command line is a part name, or a file / folder of the
// project: ft_split.c -> ft_split, ex3/main.py -> ex3. null: no part matches.
function partOf(p, gs, w) {
  const id = w.replace(/[/\\]$/, '');
  if (gs.some((g) => g.id === id)) return id;
  if (!fs.existsSync(w)) return id;   // not a file: the tester says if the name is unknown
  if (p.kind === 'c') {
    const base = path.basename(w).replace(/(_bonus)?\.c$/, '');
    return gs.some((g) => g.id === base) ? base : null;
  }
  if (p.kind !== 'py') return null;
  const rel = path.relative(p.dir, path.resolve(w));
  const ex = p.spec.exercises.find((e) => e.file === rel.split(path.sep).join('/') || e.file.split('/')[0] === rel.split(path.sep)[0]);
  return ex ? ex.id : null;
}

async function main() {
  const args = process.argv.slice(2);
  // -x / --xxx go to the tester as they are, the rest are commands and parts
  const flags = args.filter((a) => a.startsWith('-'));
  const words = args.filter((a) => !a.startsWith('-'));
  const cwd = process.cwd();
  migrate();
  shortcuts();
  if (args[0] === '--post-update') return;   // run by update() with the new code
  if (flags.includes('-h') || flags.includes('--help') || words[0] === 'help') return console.log(HELP);
  if (words[0] === 'update') return update(true);
  update();
  if (words[0] === 'clean-pc') return cleanPc();
  // a folder opens the menu there, unless it is also a part name (ex1/ in a Python project)
  if (words.length === 1 && isDir(path.resolve(words[0]))) {
    const here = detect(cwd)[0];
    if (!here || !(groups(here) || []).some((g) => g.id === words[0].replace(/[/\\]$/, ''))) return mainMenu(path.resolve(words[0]));
  }
  if (!args.length) return mainMenu(cwd);

  const cmd = ['all', 'quick', 'list', 'watch', 'commit', 'clean'].includes(words[0]) ? words[0] : '';
  const names = cmd ? words.slice(1) : words;
  // a file or folder given: look for the project around it
  const file = names.find((w) => fs.existsSync(w));
  const start = file ? path.dirname(path.resolve(file)) : cwd;
  const found = detect(start);
  if (!found.length) { console.log(red(`No 42 project found around ${short(start)}.`)); process.exit(2); }
  const p = found[0];
  if (cmd === 'clean') return cleanProject(p.dir);
  if (cmd === 'list') return (groups(p) || []).forEach((g) => console.log(`${g.id.padEnd(16)} ${dim(g.label)}`));

  let only = [];
  if (names.length) {
    const gs = groups(p) || [];
    for (const w of names) {
      const id = partOf(p, gs, w);
      if (id === null) console.log(yellow(`${w}: no part of ${p.name} matches this file (42next list shows them)`));
      else if (!only.includes(id)) only.push(id);
    }
    if (!only.length) process.exit(2);
  }
  const extra = cmd === 'quick' && !flags.includes('--quick') ? ['--quick', ...flags] : flags;
  if (cmd === 'watch') return watch(p, { only, extra });
  if (cmd === 'commit') process.exit(runJob(p, { only, extra, commit: true }).status ?? 2);
  process.exit(runTester(p, only, extra) ?? 2);
}

// output piped into `head` or a closed terminal: stop quietly, no stack trace
process.stdout.on('error', (e) => { if (e.code === 'EPIPE') process.exit(0); throw e; });
main().catch((e) => { console.error(red(e.stack)); process.exit(1); });

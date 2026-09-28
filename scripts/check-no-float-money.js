const fs = require('fs');
const path = require('path');

const candidates = [
  path.resolve(__dirname, '..', 'frontend', 'src'),
  path.resolve(__dirname, '..', 'src'),
];
const SRC = candidates.find((dir) => fs.existsSync(dir));
const EXTENSIONS = new Set(['.js', '.jsx']);
const FORBIDDEN = /parseFloat|Math\.round|Math\.floor|Math\.ceil/;
const MONEY = /escrow|payout|amount|refund|balance|wei|gen/i;
const ROOT = path.resolve(__dirname, '..');

function walk(dir, acc = []) {
  if (!fs.existsSync(dir)) {
    console.error(`check-no-float-money: missing directory ${dir}`);
    process.exit(1);
  }
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      walk(full, acc);
    } else if (EXTENSIONS.has(path.extname(entry.name))) {
      acc.push(full);
    }
  }
  return acc;
}

if (!SRC) {
  console.error('check-no-float-money: could not find frontend src directory');
  process.exit(1);
}

const files = walk(SRC);
let failed = false;

for (const file of files) {
  const lines = fs.readFileSync(file, 'utf8').split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (!FORBIDDEN.test(line)) continue;
    FORBIDDEN.lastIndex = 0;
    const start = Math.max(0, i - 2);
    const end = Math.min(lines.length, i + 3);
    const ctx = lines.slice(start, end).join('\n');
    if (MONEY.test(line) || MONEY.test(ctx)) {
      const rel = path.relative(ROOT, file);
      console.error(`${rel}:${i + 1}: forbidden floating-point op near money terms`);
      console.error(`  ${line.trim()}`);
      failed = true;
    }
  }
}

if (failed) {
  console.error('check-no-float-money: FAIL');
  process.exit(1);
}

console.log(`check-no-float-money: PASS (${files.length} files scanned)`);

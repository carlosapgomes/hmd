// Drive Playwright pinado do verify-hmd (slice 002/D7) — prova oficial.
// Login REAL pelo form (nenhum cookie injetado: sem addCookies em nenhum
// caminho), home por papel e leitura da fila médica. Evidência em
// <run>/artifacts/: PNG + snapshot + report.json + observed-state.json.
// Exit: 0 PASS · 1 falha observada da aplicação · 2 bloqueador infra/driver.
import { execSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync, readlinkSync, writeFileSync } from 'node:fs';
import path from 'node:path';

import { chromium } from 'playwright';

const runArg = process.argv.indexOf('--run');
if (runArg < 0 || !process.argv[runArg + 1]) {
  console.error('uso: drive.mjs --run <tmp/verification/hmd-verify-...>');
  process.exit(2);
}
const runDir = path.resolve(process.argv[runArg + 1]);
const manifest = JSON.parse(readFileSync(path.join(runDir, 'manifest.json'), 'utf8'));
const base = manifest.url;
const artifacts = path.join(runDir, 'artifacts');
const PASSWORD = manifest.seedPassword;

// Origem exata verificada: o driver só dirige a URL do manifest se ela for
// o loopback da porta do manifest (a mesma origem que o doctor atestou).
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base) || Number(new URL(base).port) !== manifest.port) {
  console.error(`infra: manifest.url=${base} não é a origem loopback verificada (porta ${manifest.port}) — recuso dirigir.`);
  process.exit(2);
}
if (!existsSync(artifacts)) {
  console.error(`infra: diretório de artefatos ausente em ${artifacts} — recuso dirigir.`);
  process.exit(2);
}

// Posse da instância (mesma amarração do doctor, repetida aqui para que
// NENHUM ponto de entrada dirija sem ela — nem a invocação direta deste
// script): PID vivo com start-ticks, marcador/build por-run no ambiente do
// PID, posse do socket LISTEN (inode) e identidade HEAD+dirty do checkout.
function refuseOwned(reason) {
  console.error(`infra: posse da instância não atestada (${reason}) — recuso dirigir (rode o doctor).`);
  process.exit(2);
}
function assertOwnedInstance() {
  const pid = Number(manifest.pid);
  if (!Number.isInteger(pid) || pid <= 0) refuseOwned(`pid inválido: ${manifest.pid}`);
  let stat;
  try {
    stat = readFileSync(`/proc/${pid}/stat`, 'utf8');
  } catch {
    refuseOwned(`PID ${pid} não está vivo`);
  }
  const ticks = stat.slice(stat.lastIndexOf(')') + 2).trim().split(/\s+/)[19];
  if (String(ticks) !== String(manifest.startTicks)) refuseOwned(`PID ${pid} foi reusado`);
  if (readFileSync('/proc/sys/kernel/random/boot_id', 'utf8').trim() !== manifest.bootId) {
    refuseOwned('instância de outro boot');
  }
  const environ = readFileSync(`/proc/${pid}/environ`, 'utf8').split('\0');
  if (!environ.includes(`HMD_VERIFY_MARKER=${manifest.marker}`)) {
    refuseOwned(`PID ${pid} não carrega o marcador desta run`);
  }
  if (!environ.includes(`HMD_VERIFY_BUILD=${manifest.build}`)) {
    refuseOwned(`PID ${pid} não carrega o build registrado`);
  }
  // STATIC_ROOT por-run: presente, sob o diretório da run (isolamento) e
  // no ambiente do processo servidor (serve deste root, não do checkout).
  if (!manifest.staticRoot) refuseOwned('staticRoot ausente no manifest');
  if (!String(manifest.staticRoot).startsWith(`${runDir}/`)) {
    refuseOwned(`staticRoot fora do diretório da run: ${manifest.staticRoot}`);
  }
  if (!environ.includes(`HMD_VERIFY_STATIC_ROOT=${manifest.staticRoot}`)) {
    refuseOwned(`PID ${pid} não serve do static root desta run`);
  }
  const cmdline = readFileSync(`/proc/${pid}/cmdline`, 'utf8').replaceAll('\0', ' ');
  if (!cmdline.includes('config.settings.verify') || !cmdline.includes(`127.0.0.1:${manifest.port}`)) {
    refuseOwned(`PID ${pid} não é o runserver de verificação desta porta`);
  }
  const portHex = Number(manifest.port).toString(16).toUpperCase().padStart(4, '0');
  let inode = null;
  for (const file of ['/proc/net/tcp', '/proc/net/tcp6']) {
    for (const line of readFileSync(file, 'utf8').split('\n')) {
      const cols = line.trim().split(/\s+/);
      if (cols[3] === '0A' && cols[1]?.endsWith(`:${portHex}`)) {
        inode = cols[9];
        break;
      }
    }
    if (inode) break;
  }
  const ownsSocket =
    inode !== null &&
    readdirSync(`/proc/${pid}/fd`).some((fd) => {
      try {
        return readlinkSync(`/proc/${pid}/fd/${fd}`) === `socket:[${inode}]`;
      } catch {
        return false;
      }
    });
  if (!ownsSocket) refuseOwned(`PID ${pid} não detém o socket de escuta em 127.0.0.1:${manifest.port}`);
  const git = (args) => execSync(`git ${args}`, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
  const head = git('rev-parse HEAD').trim();
  // Mesmo algoritmo do control-hmd build_identity: status+diffs rastreados
  // + CONTEÚDO sha256 das fontes untracked relevantes de verificação/
  // harness — edição só-conteúdo de arquivo untracked muda o hash.
  const hash = createHash('sha256');
  hash.update(git('status --porcelain'));
  hash.update(git('diff'));
  hash.update(git('diff --cached'));
  const untracked = git(
    "ls-files --others --exclude-standard -- '.pi/skills/verify-hmd' 'config/settings/verify.py' 'apps/accounts/backends_verify.py' 'apps/accounts/management/commands/seed_verify.py' 'apps/accounts/tests/test_verify_guards.py'",
  )
    .split('\n')
    .filter(Boolean)
    .sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
  for (const file of untracked) {
    if (!existsSync(file)) continue;
    const fileHash = createHash('sha256').update(readFileSync(file)).digest('hex');
    hash.update(`${file} ${fileHash}\n`);
  }
  const dirty = hash.digest('hex').slice(0, 12);
  if (`${head}-${dirty}` !== manifest.build) refuseOwned('build (HEAD+dirty) divergiu do manifest');
}
assertOwnedInstance();

// Assets estáticos exigidos pela casca do app: 404 aqui reprova o drive
// (capturas sem CSS/JS não servem como evidência visual).
const REQUIRED_ASSETS = ['/static/css/app.css', '/static/js/sw.js'];
const failedAssets = [];

const checks = [];
const failures = [];
function check(id, ok, detail) {
  checks.push({ id, ok: !!ok, detail: String(detail ?? '') });
  if (!ok) failures.push(id);
  console.log(`${ok ? 'PASS' : 'FAIL'} ${id} ${detail ?? ''}`);
}

async function snap(page, name) {
  const file = path.join(artifacts, `${name}.txt`);
  let text;
  try {
    text = await page.locator('body').ariaSnapshot();
  } catch {
    text = await page.locator('body').innerText();
  }
  writeFileSync(file, `${page.url()}\n${text}\n`);
  await page.screenshot({ path: path.join(artifacts, `${name}.png`), fullPage: true });
}

async function loginAs(browser, username, label) {
  const context = await browser.newContext();
  const page = await context.newPage();
  page.on('response', (res) => {
    if (res.url().includes('/static/') && res.status() >= 400) {
      failedAssets.push(`${res.status()} ${res.url()}`);
    }
  });
  await page.goto(`${base}/login/`, { waitUntil: 'domcontentloaded' });
  await snap(page, `${label}-login-before`);
  await page.getByLabel('Usuário').fill(username);
  await page.getByLabel('Senha', { exact: true }).fill(PASSWORD);
  await page.getByRole('button', { name: 'Entrar' }).click();
  await page.waitForURL((url) => !url.pathname.endsWith('/login/'), { timeout: 15000 });
  return { context, page };
}

// Jornada provada: login doctor pelo form → home/fila médica → leitura do
// caso sintético (cartão + página de trabalho, sem decidir) → homes dos
// demais papéis pelo form. Cada papel em contexto isolado.
let browser;
try {
  // Chromium pinado do Playwright — nunca o Chrome do sistema. Se o
  // download não aconteceu (ver artifacts/playwright-install.log), o
  // lançamento falha e o drive sai BLOCKED (exit 2), sem substituir motor.
  browser = await chromium.launch({ headless: true });
} catch (err) {
  console.error(`infra BLOCKED: Chromium pinado do Playwright indisponível (${err.message}) — nenhum motor substituto foi usado.`);
  process.exit(2);
}

const observed = { runId: manifest.runId, base, database: manifest.database, steps: [] };
try {
  // 1) Médico: fila médica em leitura.
  {
    const { context, page } = await loginAs(browser, 'verify-doctor', 'doctor');
    check('doctor-home-url', page.url().includes('/doctor/'), page.url());
    check('doctor-queue-heading', await page.getByRole('heading', { name: 'Casos aguardando decisão' }).count() === 1);
    const body = await page.locator('body').innerText();
    check('doctor-sees-VERIFY-001', body.includes('VERIFY-001'), 'cartão sintético 001');
    check('doctor-sees-patient', body.includes('Paciente Sintético Um'), 'nome sintético');
    await snap(page, 'doctor-queue');
    observed.steps.push({ step: 'doctor-queue', url: page.url() });
    // Assets reais servidos pela instância: cada um precisa responder 200.
    for (const asset of REQUIRED_ASSETS) {
      const status = await page.evaluate(
        async (u) => (await fetch(u, { cache: 'no-store' })).status,
        asset,
      );
      check(`static-asset-${asset.split('/').pop()}`, status === 200, `${asset} HTTP ${status}`);
    }
    const card = page.locator('[data-doctor-queue-card] a.stretched-link').first();
    check('doctor-card-link', (await card.count()) === 1);
    await card.click();
    await page.waitForURL(/\/doctor\/case\//);
    check('doctor-case-read-url', page.url().includes('/doctor/case/'), page.url());
    await snap(page, 'doctor-case-read');
    observed.steps.push({ step: 'doctor-case-read', url: page.url() });
    await context.close();
  }

  // 2) Homes por papel (login real pelo form em cada um).
  const homes = [
    ['verify-nir', 'nir', '/intake/', 'Envio de relatório de regulação'],
    ['verify-scheduler', 'scheduler', '/scheduler/', 'Fila de agendamento'],
    ['verify-manager', 'manager', '/dashboard/', 'Painel gerencial'],
    ['verify-admin', 'admin', '/dashboard/', 'Painel gerencial'],
  ];
  for (const [user, label, urlPart, heading] of homes) {
    const { context, page } = await loginAs(browser, user, label);
    check(`${label}-home-url`, page.url().includes(urlPart), page.url());
    check(
      `${label}-heading`,
      (await page.getByRole('heading', { name: heading }).count()) >= 1,
      heading,
    );
    await snap(page, `${label}-home`);
    observed.steps.push({ step: `${label}-home`, url: page.url() });
    await context.close();
  }
} catch (err) {
  console.error(`infra/driver: ${err.message}`);
  writeFileSync(path.join(artifacts, 'observed-state.json'), JSON.stringify(observed, null, 2));
  await browser.close();
  process.exit(2);
}

check('no-failed-static-assets', failedAssets.length === 0, failedAssets.join('; ') || 'nenhum /static/* >= 400');

writeFileSync(
  path.join(artifacts, 'report.json'),
  JSON.stringify({ runId: manifest.runId, checks, failures, pass: failures.length === 0 }, null, 2),
);
writeFileSync(path.join(artifacts, 'observed-state.json'), JSON.stringify(observed, null, 2));
await browser.close();
console.log(failures.length === 0 ? 'DRIVE PASS' : `DRIVE FAIL: ${failures.join(', ')}`);
process.exit(failures.length === 0 ? 0 : 1);

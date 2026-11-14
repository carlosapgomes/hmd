// Drive Playwright pinado do verify-hmd — ui-parity-intake-detail slice 004.
// Jornada painel-visual: painel gerencial no padrão ats-web (toolbar de
// período, cards de métrica e lista em case-card) com os mesmos números e
// filtros do HMD. Login real pelo form como verify-manager (e verify-admin),
// leitura da toolbar/cards/lista, troca de período pela toolbar e filtro de
// busca server-side. Sem mutação de banco nem de seed. Evidência em
// <run>/artifacts/. Exit: 0 PASS · 1 falha observada · 2 infra/driver.
import { execSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync, readlinkSync, writeFileSync } from 'node:fs';
import path from 'node:path';

import { chromium } from 'playwright';

const runArg = process.argv.indexOf('--run');
if (runArg < 0 || !process.argv[runArg + 1]) {
  console.error('uso: drive-painel-visual.mjs --run <tmp/verification/hmd-verify-...>');
  process.exit(2);
}
const runDir = path.resolve(process.argv[runArg + 1]);
const manifest = JSON.parse(readFileSync(path.join(runDir, 'manifest.json'), 'utf8'));
const base = manifest.url;
const artifacts = path.join(runDir, 'artifacts');
const PASSWORD = manifest.seedPassword;

if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base) || Number(new URL(base).port) !== manifest.port) {
  console.error(`infra: manifest.url=${base} não é a origem loopback verificada (porta ${manifest.port}).`);
  process.exit(2);
}
if (!existsSync(artifacts)) {
  console.error(`infra: diretório de artefatos ausente em ${artifacts}.`);
  process.exit(2);
}

function refuseOwned(reason) {
  console.error(`infra: posse da instância não atestada (${reason}) — rode o doctor.`);
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
  if (readFileSync('/proc/sys/kernel/random/boot_id', 'utf8').trim() !== manifest.bootId) refuseOwned('instância de outro boot');
  const environ = readFileSync(`/proc/${pid}/environ`, 'utf8').split('\0');
  if (!environ.includes(`HMD_VERIFY_MARKER=${manifest.marker}`)) refuseOwned('sem marcador');
  if (!environ.includes(`HMD_VERIFY_BUILD=${manifest.build}`)) refuseOwned('sem build');
  if (!manifest.staticRoot) refuseOwned('staticRoot ausente');
  if (!String(manifest.staticRoot).startsWith(`${runDir}/`)) refuseOwned('staticRoot fora da run');
  if (!environ.includes(`HMD_VERIFY_STATIC_ROOT=${manifest.staticRoot}`)) refuseOwned('staticRoot divergente');
  const cmdline = readFileSync(`/proc/${pid}/cmdline`, 'utf8').replaceAll('\0', ' ');
  if (!cmdline.includes('config.settings.verify') || !cmdline.includes(`127.0.0.1:${manifest.port}`)) refuseOwned('cmdline sem verify/loopback');
  const portHex = Number(manifest.port).toString(16).toUpperCase().padStart(4, '0');
  let inode = null;
  for (const file of ['/proc/net/tcp', '/proc/net/tcp6']) {
    for (const line of readFileSync(file, 'utf8').split('\n')) {
      const cols = line.trim().split(/\s+/);
      if (cols[3] === '0A' && cols[1]?.endsWith(`:${portHex}`)) { inode = cols[9]; break; }
    }
    if (inode) break;
  }
  const ownsSocket = inode !== null && readdirSync(`/proc/${pid}/fd`).some((fd) => {
    try { return readlinkSync(`/proc/${pid}/fd/${fd}`) === `socket:[${inode}]`; } catch { return false; }
  });
  if (!ownsSocket) refuseOwned('socket LISTEN não detido');
  const git = (args) => execSync(`git ${args}`, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
  const head = git('rev-parse HEAD').trim();
  const hash = createHash('sha256');
  hash.update(git('status --porcelain'));
  hash.update(git('diff'));
  hash.update(git('diff --cached'));
  const untracked = git("ls-files --others --exclude-standard -- '.pi/skills/verify-hmd' 'config/settings/verify.py' 'apps/accounts/backends_verify.py' 'apps/accounts/management/commands/seed_verify.py' 'apps/accounts/tests/test_verify_guards.py'")
    .split('\n').filter(Boolean).sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
  for (const file of untracked) {
    if (!existsSync(file)) continue;
    hash.update(`${file} ${createHash('sha256').update(readFileSync(file)).digest('hex')}\n`);
  }
  if (`${head}-${hash.digest('hex').slice(0, 12)}` !== manifest.build) refuseOwned('build divergiu');
}
assertOwnedInstance();

const REQUIRED_ASSETS = ['/static/css/app.css', '/static/js/sw.js', '/static/js/work_lock.js'];
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
  try { text = await page.locator('body').ariaSnapshot(); } catch { text = await page.locator('body').innerText(); }
  writeFileSync(file, `${page.url()}\n${text}\n`);
  await page.screenshot({ path: path.join(artifacts, `${name}.png`), fullPage: true });
}

async function loginAs(browser, username, label, opts = {}) {
  const context = await browser.newContext(opts);
  const page = await context.newPage();
  page.on('response', (res) => {
    if (res.url().includes('/static/') && res.status() >= 400) failedAssets.push(`${res.status()} ${res.url()}`);
  });
  await page.goto(`${base}/login/`, { waitUntil: 'domcontentloaded' });
  await snap(page, `${label}-login-before`);
  await page.getByLabel('Usuário').fill(username);
  await page.getByLabel('Senha', { exact: true }).fill(PASSWORD);
  await page.getByRole('button', { name: 'Entrar' }).click();
  await page.waitForURL((url) => !url.pathname.endsWith('/login/'), { timeout: 15000 });
  return { context, page };
}

let browser;
try {
  browser = await chromium.launch({ headless: true });
} catch (err) {
  console.error(`infra BLOCKED: Chromium pinado do Playwright indisponível (${err.message}) — nenhum motor substituto foi usado.`);
  process.exit(2);
}

const observed = { runId: manifest.runId, base, database: manifest.database, steps: [] };

try {
  // 1) Manager: painel no padrão ats-web (toolbar + cards + case-cards).
  {
    const { context, page } = await loginAs(browser, 'verify-manager', 'manager-painel');
    check('manager-lands-on-panel', page.url().includes('/dashboard/'), page.url());
    check('panel-heading', (await page.getByRole('heading', { name: 'Painel gerencial' }).count()) >= 1);
    check('panel-toolbar-card', (await page.locator('.metrics-period-card').count()) === 1);
    check('panel-toolbar-selector', (await page.locator('#metrics-period-selector').count()) === 1);
    const periodOptions = page.locator('.metrics-period-options .metrics-period-option');
    check('panel-period-options', (await periodOptions.count()) === 4, `${await periodOptions.count()} opções`);
    check('panel-period-active-on-today', (await page.locator('.metrics-period-option.is-active').count()) === 1);
    check('panel-summary-cards', (await page.locator('.metrics-summary-card').count()) === 6, `${await page.locator('.metrics-summary-card').count()} cards`);
    const body = await page.locator('body').innerText();
    // innerText respeita text-transform: os rótulos dos cards são UPPERCASE.
    const bodyUpper = body.toUpperCase();
    check('panel-summary-labels', bodyUpper.includes('TOTAL') && bodyUpper.includes('AGENDADOS') && bodyUpper.includes('NEGADOS') && bodyUpper.includes('EM ANDAMENTO'));
    check('panel-by-type-table', body.includes('Procedimentos por tipo'));
    check('panel-by-unit', body.includes('Agendamentos por unidade'));
    check('panel-avg-time', bodyUpper.includes('TEMPO MÉDIO ATÉ DECISÃO MÉDICA'));
    check('panel-list-heading', body.includes('Casos do período'));
    check('panel-case-cards', (await page.locator('.case-card').count()) === 2, `${await page.locator('.case-card').count()} case-cards`);
    check('panel-lists-VERIFY-001', body.includes('VERIFY-001') && body.includes('Paciente Sintético Um'));
    check('panel-lists-VERIFY-002', body.includes('VERIFY-002') && body.includes('Paciente Sintético Dois'));
    check('panel-details-links', (await page.locator('.case-card a[href*="/dashboard/case/"]').count()) === 2);
    // Identificação sem truncamento (slice-004 fix-1, P1): o contêiner do
    // nome/idade não pode ter text-truncate (nome longo/viewport estreita
    // ocultaria a identificação). Nome longo em si é provado pelo pytest.
    const truncated = await page.locator('.case-card .case-patient-name').evaluateAll((els) =>
      els.some((el) => el.parentElement?.classList.contains('text-truncate')));
    check('panel-case-identification-not-truncated', !truncated, `truncated=${truncated}`);
    await snap(page, 'manager-painel');
    observed.steps.push({ step: 'manager-painel', url: page.url() });

    // 2) Troca de período pela toolbar (mesmos números, outra régua).
    await page.locator('.metrics-period-options .metrics-period-option', { hasText: '7 dias' }).click();
    await page.waitForLoadState('domcontentloaded');
    const url7d = page.url();
    check('panel-period-7d-url', url7d.includes('period=7d'), url7d);
    const activeText = (await page.locator('.metrics-period-option.is-active').innerText()).trim();
    check('panel-period-7d-active', activeText === '7 dias', activeText);
    check('panel-period-7d-cards', (await page.locator('.case-card').count()) === 2);
    await snap(page, 'manager-painel-7d');
    observed.steps.push({ step: 'manager-painel-7d', url: page.url() });

    // 3) Filtro de busca server-side preservado no padrão visual.
    await page.getByLabel('Busca').fill('VERIFY-001');
    await page.getByRole('button', { name: 'Filtrar' }).click();
    await page.waitForLoadState('domcontentloaded');
    const filtered = await page.locator('body').innerText();
    check('panel-filter-narrows', (await page.locator('.case-card').count()) === 1, `${await page.locator('.case-card').count()} cards`);
    check('panel-filter-keeps-target', filtered.includes('VERIFY-001') && filtered.includes('Paciente Sintético Um'));
    check('panel-filter-drops-other', !filtered.includes('VERIFY-002'));
    await snap(page, 'manager-painel-filtered');
    observed.steps.push({ step: 'manager-painel-filtered', url: page.url() });

    // 4) Sem filtro: os dois casos sintéticos voltam (navegação nova).
    await page.goto(`${base}/dashboard/`, { waitUntil: 'domcontentloaded' });
    check('panel-reset-restores', (await page.locator('.case-card').count()) === 2);
    await context.close();
  }

  // 5) Admin: mesma superfície do painel (mesma rota por papel ativo).
  {
    const { context, page } = await loginAs(browser, 'verify-admin', 'admin-painel');
    check('admin-lands-on-panel', page.url().includes('/dashboard/'), page.url());
    check('admin-panel-heading', (await page.getByRole('heading', { name: 'Painel gerencial' }).count()) >= 1);
    check('admin-panel-toolbar', (await page.locator('.metrics-period-card').count()) === 1);
    check('admin-panel-case-cards', (await page.locator('.case-card').count()) === 2);
    await snap(page, 'admin-painel');
    observed.steps.push({ step: 'admin-painel', url: page.url() });
    await context.close();
  }
} catch (err) {
  console.error(`infra/driver: ${err.message}`);
  writeFileSync(path.join(artifacts, 'observed-state.json'), JSON.stringify(observed, null, 2));
  await browser.close();
  process.exit(2);
}

check('no-failed-static-assets', failedAssets.length === 0, failedAssets.join('; ') || 'nenhum /static/* >= 400');

writeFileSync(path.join(artifacts, 'report.json'), JSON.stringify({ runId: manifest.runId, checks, failures, pass: failures.length === 0 }, null, 2));
writeFileSync(path.join(artifacts, 'observed-state.json'), JSON.stringify(observed, null, 2));
await browser.close();
console.log(failures.length === 0 ? 'DRIVE PASS' : `DRIVE FAIL: ${failures.join(', ')}`);
process.exit(failures.length === 0 ? 0 : 1);

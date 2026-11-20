// Drive Playwright pinado do verify-hmd — confirm-nir-procedure-review slice 002.
// Jornada da revisão NIR do procedimento: card declarado×detectado com
// select único dos 13 tipos (gating), manutenção 990101 e troca para não
// detectado 990102 com reprocessamento real pelo servidor, campos/teclado,
// erro reexibido escapado sem consentimento automático, select nativo sem
// JS (990103), viewports 390/1280 sem overflow e zero transporte externo
// (não-loopback bloqueado por rota; nenhuma tentativa a hosts de LLM).
// Pré-condição: seed_verify --procedure-review executado sob a identidade
// (banco/media) da run — o drive nunca semeia nem muta banco diretamente.
// Evidência em <run>/artifacts/.
// Exit: 0 PASS · 1 falha observada · 2 infra/driver.
import { execSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync, readlinkSync, writeFileSync } from 'node:fs';
import path from 'node:path';

import { chromium } from 'playwright';

const runArg = process.argv.indexOf('--run');
if (runArg < 0 || !process.argv[runArg + 1]) {
  console.error('uso: drive-procedure-review.mjs --run <tmp/verification/hmd-verify-...>');
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

const REQUIRED_ASSETS = ['/static/css/app.css', '/static/js/procedure_combobox.js'];
const failedAssets = [];
const externalAttempts = [];
const llmAttempts = [];
const externalSuccess = [];
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

async function trackNetwork(context) {
  await context.route('**/*', (route) => {
    const raw = route.request().url();
    let host = '';
    try { host = new URL(raw).hostname; } catch { return route.continue(); }
    if (host === '127.0.0.1' || host === 'localhost') return route.continue();
    externalAttempts.push(`${route.request().method()} ${raw}`);
    if (/openrouter|api\./i.test(raw)) llmAttempts.push(raw);
    return route.abort();
  });
  context.on('response', (res) => {
    let host = '';
    try { host = new URL(res.url()).hostname; } catch { return; }
    if (host !== '127.0.0.1' && host !== 'localhost' && res.status() < 400) {
      externalSuccess.push(`${res.status()} ${res.url()}`);
    }
    if (res.url().includes('/static/') && res.status() >= 400) failedAssets.push(`${res.status()} ${res.url()}`);
  });
}

async function loginAs(browser, username, label, opts = {}) {
  const context = await browser.newContext(opts);
  await trackNetwork(context);
  const page = await context.newPage();
  await page.goto(`${base}/login/`, { waitUntil: 'domcontentloaded' });
  await snap(page, `${label}-login-before`);
  await page.getByLabel('Usuário').fill(username);
  await page.getByLabel('Senha', { exact: true }).fill(PASSWORD);
  await page.getByRole('button', { name: 'Entrar' }).click();
  await page.waitForURL((url) => !url.pathname.endsWith('/login/'), { timeout: 15000 });
  return { context, page };
}

async function openFixtureDetail(page, number) {
  await page.goto(`${base}/intake/cases/`, { waitUntil: 'domcontentloaded' });
  const card = page.locator('[data-my-cases-card]', { hasText: number }).first();
  await card.waitFor({ timeout: 15000 });
  const link = card.getByRole('link', { name: 'Ver detalhes' });
  const href = await link.getAttribute('href');
  await link.click();
  await page.waitForLoadState('domcontentloaded');
  return href;
}

async function selectOptionCount(page) {
  return page.evaluate(() => document.querySelectorAll('#procedure-review-form select[name="procedure_type"] option').length);
}

const observed = { runId: manifest.runId, base, database: manifest.database, steps: [] };

let browser;
try {
  browser = await chromium.launch({ headless: true });
} catch (err) {
  console.error(`infra BLOCKED: Chromium pinado do Playwright indisponível (${err.message}).`);
  process.exit(2);
}

try {
  // ── R1–R3: gating do card em 990101 (marcado, retido) ──
  {
    const { context, page } = await loginAs(browser, 'verify-nir', 'nir-gating');
    for (const asset of REQUIRED_ASSETS) {
      const status = await page.evaluate(async (u) => (await fetch(u, { cache: 'no-store' })).status, asset);
      check(`static-asset-${asset.split('/').pop()}`, status === 200, `${asset} HTTP ${status}`);
    }
    await openFixtureDetail(page, '990101');
    check('gating-review-card-visible', await page.getByRole('heading', { name: 'Revisão do procedimento' }).isVisible());
    check('gating-no-legacy-release', !(await page.locator('body').innerText()).includes('Liberar caso'));
    check('gating-form-present', (await page.locator('#procedure-review-form').count()) === 1);
    check('gating-select-13-options', (await selectOptionCount(page)) === 13, `${await selectOptionCount(page)} opções`);
    check('gating-current-preselected', (await page.locator('#procedure-review-form select[name="procedure_type"]').inputValue()) === 'art_perif');
    check('gating-no-file-input', (await page.locator('#procedure-review-form input[type="file"]').count()) === 0);
    check('gating-hidden-snapshots', (await page.locator('#procedure-review-form input[name="review_event_id"][type="hidden"]').count()) === 1
      && (await page.locator('#procedure-review-form input[name="source_fingerprint"][type="hidden"]').count()) === 1);
    check('gating-declared-detected-summary', (await page.locator('body').innerText()).includes('Declarados:'));
    await snap(page, 'nir-review-gating-990101');
    observed.steps.push({ step: 'nir-review-gating', url: page.url() });
    await context.close();
  }

  // ── R3/R4: teclado + erro reexibido escapado sem consentimento (990103, sem mutar) ──
  {
    const { context, page } = await loginAs(browser, 'verify-nir', 'nir-keyboard');
    await openFixtureDetail(page, '990103');
    check('keyboard-card-visible', (await page.locator('#procedure-review-form').count()) === 1);
    // Caminho de teclado real com JS: filtra no combobox e confirma com Enter.
    await page.locator('#id_procedure_type-combobox').click();
    await page.locator('#id_procedure_type-combobox').fill('');
    await page.locator('#id_procedure_type-combobox').pressSequentially('nefro', { delay: 20 });
    await page.waitForFunction(() => document.querySelector('#id_procedure_type-list') && !document.querySelector('#id_procedure_type-list').hidden);
    await page.locator('#id_procedure_type-combobox').press('Enter');
    check('keyboard-combobox-confirms', (await page.locator('#procedure-review-form select[name="procedure_type"]').inputValue()) === 'nefrostomia');
    await page.locator('#id_procedure_type-combobox').focus();
    await page.keyboard.press('Tab');
    const afterFirstTab = await page.evaluate(() => document.activeElement?.getAttribute('name'));
    check('keyboard-tab-select-to-justification', afterFirstTab === 'review_justification', String(afterFirstTab));
    await page.keyboard.press('Tab');
    const afterSecondTab = await page.evaluate(() => document.activeElement?.getAttribute('name'));
    check('keyboard-tab-justification-to-checkbox', afterSecondTab === 'review_acknowledged', String(afterSecondTab));
    await page.locator('#procedure-review-form textarea[name="review_justification"]').fill('<script>alert("xss")</script>');
    // Sem marcar a leitura: desliga a validação nativa para provar o 400 do
    // servidor (erros, tentativa escapada, checkbox solto).
    await page.evaluate(() => { document.querySelector('#procedure-review-form').noValidate = true; });
    await page.getByRole('button', { name: 'Confirmar procedimento' }).click();
    await page.waitForLoadState('domcontentloaded');
    check('keyboard-error-returns-400-content', (await page.locator('body').innerText()).includes('Confirme que leu'));
    const html = await page.content();
    check('keyboard-xss-escaped', html.includes('&lt;script&gt;') && !html.includes('<script>alert("xss")</script>'), 'tentativa escapada');
    check('keyboard-no-auto-consent', (await page.locator('#procedure-review-form input[name="review_acknowledged"]').isChecked()) === false);
    await snap(page, 'nir-review-keyboard-error');
    observed.steps.push({ step: 'nir-review-keyboard-error', url: page.url() });
    await context.close();
  }

  // ── R3/R5: manter 990101 com cadeia real do servidor ──
  {
    const { context, page } = await loginAs(browser, 'verify-nir', 'nir-maintain');
    await openFixtureDetail(page, '990101');
    await page.locator('#procedure-review-form textarea[name="review_justification"]').fill('Confirmo a manutencao apos leitura do relatorio principal.');
    await page.locator('#procedure-review-form input[name="review_acknowledged"]').check();
    await snap(page, 'nir-review-maintain-before');
    await page.getByRole('button', { name: 'Confirmar procedimento' }).click();
    await page.waitForLoadState('domcontentloaded');
    const flash = await page.locator('body').innerText();
    check('maintain-flash-human-reference', flash.includes('Caso 990101 confirmado'), 'flash com nº');
    check('maintain-flash-no-uid', !/Caso [0-9a-f-]{36} confirmado/.test(flash), 'sem uid no flash');
    check('maintain-card-gone', (await page.locator('#procedure-review-form').count()) === 0, 'caso avançou');
    await snap(page, 'nir-review-maintain-after');
    observed.steps.push({ step: 'nir-review-maintain', url: page.url() });
    await context.close();
  }

  // ── R3/R5: trocar 990102 para tipo não detectado via teclado ──
  {
    const { context, page } = await loginAs(browser, 'verify-nir', 'nir-switch');
    await openFixtureDetail(page, '990102');
    check('switch-gating-current', (await page.locator('#procedure-review-form select[name="procedure_type"]').inputValue()) === 'flebografia');
    await page.locator('#procedure-review-form select[name="procedure_type"]').focus();
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('Enter');
    await page.locator('#procedure-review-form select[name="procedure_type"]').selectOption('angio_art_perif');
    check('switch-select-changed', (await page.locator('#procedure-review-form select[name="procedure_type"]').inputValue()) === 'angio_art_perif');
    await page.locator('#procedure-review-form textarea[name="review_justification"]').fill('Troco para o procedimento sustentado apos leitura do relatorio.');
    await page.locator('#procedure-review-form input[name="review_acknowledged"]').check();
    await snap(page, 'nir-review-switch-before');
    await page.getByRole('button', { name: 'Confirmar procedimento' }).click();
    await page.waitForLoadState('domcontentloaded');
    check('switch-flash-human-reference', (await page.locator('body').innerText()).includes('Caso 990102 confirmado'));
    check('switch-card-gone', (await page.locator('#procedure-review-form').count()) === 0);
    await snap(page, 'nir-review-switch-after');
    observed.steps.push({ step: 'nir-review-switch', url: page.url() });
    await context.close();
  }

  // ── R3/R4 sem JS: select nativo confirma 990103 ──
  {
    const { context, page } = await loginAs(browser, 'verify-nir', 'nir-nojs', { javaScriptEnabled: false });
    await openFixtureDetail(page, '990103');
    check('nojs-select-visible', await page.locator('#procedure-review-form select[name="procedure_type"]').isVisible());
    check('nojs-no-combobox-input', (await page.locator('#id_procedure_type-combobox').count()) === 0);
    check('nojs-select-13-options', (await selectOptionCount(page)) === 13);
    await page.locator('#procedure-review-form select[name="procedure_type"]').selectOption('nefrostomia');
    await page.locator('#procedure-review-form textarea[name="review_justification"]').fill('Confirmo sem JavaScript apos leitura.');
    await page.locator('#procedure-review-form input[name="review_acknowledged"]').check();
    await snap(page, 'nir-review-nojs-before');
    await page.getByRole('button', { name: 'Confirmar procedimento' }).click();
    await page.waitForLoadState('domcontentloaded');
    check('nojs-flash-human-reference', (await page.locator('body').innerText()).includes('Caso 990103 confirmado'));
    await snap(page, 'nir-review-nojs-after');
    observed.steps.push({ step: 'nir-review-nojs', url: page.url() });
    await context.close();
  }

  // ── R5: viewports 390/1280 sem overflow (card em 990104 + detalhe confirmado) ──
  for (const width of [390, 1280]) {
    const { context, page } = await loginAs(browser, 'verify-nir', `nir-vp${width}`, { viewport: { width, height: 800 } });
    await openFixtureDetail(page, '990104');
    check(`viewport-${width}-review-card`, (await page.locator('#procedure-review-form').count()) === 1);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    check(`viewport-${width}-no-overflow`, overflow <= 0, `overflow=${overflow}px`);
    await snap(page, `nir-review-viewport-${width}`);
    observed.steps.push({ step: `nir-review-viewport-${width}`, url: page.url() });
    await context.close();
  }

  // ── R5: fila médica com os confirmados (análise produzida pelo servidor) ──
  {
    const { context, page } = await loginAs(browser, 'verify-doctor', 'doctor-queue');
    await page.goto(`${base}/doctor/`, { waitUntil: 'domcontentloaded' });
    const queueBody = await page.locator('body').innerText();
    check('doctor-queue-has-990101', queueBody.includes('990101'), 'manter chegou à fila');
    check('doctor-queue-has-990102', queueBody.includes('990102'), 'troca chegou à fila');
    await snap(page, 'doctor-review-queue');
    observed.steps.push({ step: 'doctor-review-queue', url: page.url() });
    await context.close();
  }
} catch (err) {
  console.error(`infra/driver: ${err.message}`);
  writeFileSync(path.join(artifacts, 'observed-state.json'), JSON.stringify(observed, null, 2));
  await browser.close();
  process.exit(2);
}

check('no-failed-static-assets', failedAssets.length === 0, failedAssets.join('; ') || 'nenhum /static/* >= 400');
check('zero-llm-transport', llmAttempts.length === 0, llmAttempts.join('; ') || 'nenhuma tentativa a LLM externo');
check('zero-external-success', externalSuccess.length === 0, externalSuccess.join('; ') || 'nenhuma resposta não-loopback');

writeFileSync(path.join(artifacts, 'procedure-review-report.json'), JSON.stringify({ runId: manifest.runId, checks, failures, pass: failures.length === 0 }, null, 2));
writeFileSync(path.join(artifacts, 'procedure-review-observed-state.json'), JSON.stringify({ ...observed, externalAttempts, externalSuccess }, null, 2));
await browser.close();
console.log(failures.length === 0 ? 'DRIVE PASS' : `DRIVE FAIL: ${failures.join(', ')}`);
process.exit(failures.length === 0 ? 0 : 1);

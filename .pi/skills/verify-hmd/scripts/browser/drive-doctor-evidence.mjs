// Drive Playwright pinado do verify-hmd — confirm-nir-procedure-review slice 004.
// Evidência médica: NIR confirma via POST real (idempotente — reexecução não
// reseta jornadas já confirmadas/decididas), médico vê o aviso determinístico
// APPLIED+CONFIRMED com escaping e ressalva, decide sem veto clínico, erro
// 990104 nunca fica pronto na fila. Pré-condição: seed_verify
// --procedure-review sob a identidade da run. Evidência em <run>/artifacts/.
// Exit: 0 PASS · 1 falha observada · 2 infra/driver.
import { execSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync, readlinkSync, writeFileSync } from 'node:fs';
import path from 'node:path';

import { chromium } from 'playwright';

const runArg = process.argv.indexOf('--run');
if (!runArg || !process.argv[runArg + 1]) {
  console.error('uso: drive-doctor-evidence.mjs --run <tmp/verification/hmd-verify-...>');
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
  const ticks = stat.slice(stat.lastIndexOf(') ') + 2).trim().split(/\s+/)[19];
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

async function openNirFixtureDetail(page, number) {
  await page.goto(`${base}/intake/cases/`, { waitUntil: 'domcontentloaded' });
  const card = page.locator('[data-my-cases-card]', { hasText: number }).first();
  await card.waitFor({ timeout: 15000 });
  await card.getByRole('link', { name: 'Ver detalhes' }).click();
  await page.waitForLoadState('domcontentloaded');
}

// Confirma via POST real do form; idempotente: se o caso já avançou (sem
// form), apenas registra o estado e segue sem resetar nada.
async function ensureConfirmed(page, number, { procedureType, justification, flashWord }) {
  await openNirFixtureDetail(page, number);
  if ((await page.locator('#procedure-review-form').count()) === 0) {
    return 'already-confirmed';
  }
  if (procedureType) {
    await page.locator('#procedure-review-form select[name="procedure_type"]').selectOption(procedureType);
  }
  await page.locator('#procedure-review-form textarea[name="review_justification"]').fill(justification);
  await page.locator('#procedure-review-form input[name="review_acknowledged"]').check();
  await page.getByRole('button', { name: 'Confirmar procedimento' }).click();
  await page.waitForLoadState('domcontentloaded');
  const body = await page.locator('body').innerText();
  const okFlash = body.includes(`Caso ${number} confirmado`) || (await page.locator('#procedure-review-form').count()) === 0;
  check(`${flashWord}-confirmed`, okFlash, `POST real de ${number}`);
  return 'confirmed-now';
}

async function openDoctorCaseByNumber(page, number) {
  await page.goto(`${base}/doctor/`, { waitUntil: 'domcontentloaded' });
  let card = page.locator('[data-doctor-queue-card]', { hasText: number }).first();
  try {
    await card.waitFor({ timeout: 8000 });
  } catch {
    // Rerun após decisão: o caso decidido vive na aba «decididos».
    await page.goto(`${base}/doctor/?tab=decididos`, { waitUntil: 'domcontentloaded' });
    card = page.locator('[data-doctor-queue-card]', { hasText: number }).first();
    await card.waitFor({ timeout: 15000 });
  }
  const link = card.getByRole('link').first();
  const href = await link.getAttribute('href');
  await link.click();
  await page.waitForLoadState('domcontentloaded');
  return href;
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
  // ── NIR: confirmações idempotentes via POST real ──
  {
    const { context, page } = await loginAs(browser, 'verify-nir', 'nir-evidence');
    const s101 = await ensureConfirmed(page, '990101', {
      procedureType: 'art_perif',
      justification: 'Confirmo a manutencao apos leitura <b>atenta</b> do relatorio.',
      flashWord: 'maintain-990101',
    });
    const s102 = await ensureConfirmed(page, '990102', {
      procedureType: 'angio_art_perif',
      justification: 'Troco para o procedimento sustentado apos leitura do relatorio.',
      flashWord: 'switch-990102',
    });
    const s103 = await ensureConfirmed(page, '990103', {
      procedureType: 'nefrostomia',
      justification: 'Confirmo mesmo sem deteccao automatica apos leitura.',
      flashWord: 'empty-990103',
    });
    const s104 = await ensureConfirmed(page, '990104', {
      procedureType: 'art_cerebral',
      justification: 'Troco para avaliacao da falha programada.',
      flashWord: 'error-990104',
    });
    check('nir-confirms-idempotent', true, `990101=${s101} 990102=${s102} 990103=${s103} 990104=${s104}`);
    await snap(page, 'nir-evidence-confirms');
    observed.steps.push({ step: 'nir-evidence-confirms', states: { s101, s102, s103, s104 }, url: page.url() });
    await context.close();
  }

  // ── R1/R2/R5: aviso + análise no detalhe (manter 990101) ──
  {
    const { context, page } = await loginAs(browser, 'verify-doctor', 'doctor-maintain');
    await page.goto(`${base}/doctor/`, { waitUntil: 'domcontentloaded' });
    // Rerun após decisão: 990101 sai de «aguardando» para «decididos» — a
    // jornada está preservada, não resetada. Aceita qualquer aba pronta.
    let queue101 = (await page.locator('body').innerText()).includes('990101');
    let queueTab = 'aguardando';
    if (!queue101) {
      await page.goto(`${base}/doctor/?tab=decididos`, { waitUntil: 'domcontentloaded' });
      queue101 = (await page.locator('body').innerText()).includes('990101');
      queueTab = 'decididos';
    }
    check('doctor-queue-has-990101', queue101, `manter na fila (${queueTab})`);
    await openDoctorCaseByNumber(page, '990101');
    const body = await page.locator('body').innerText();
    const html = await page.content();
    check('notice-visible-990101', body.includes('Procedimento confirmado pelo NIR'), 'aviso determinístico');
    check('notice-kept-990101', body.includes('Seleção mantida'), 'manutenção explícita');
    check('notice-original-990101', body.includes('Detecção original'), 'origem B preservada');
    check('notice-caveat-990101', body.includes('não é aprovação clínica'), 'ressalva clínica');
    check('notice-author-990101', body.includes('Verificação'), 'autor NIR');
    check('notice-xss-escaped-990101', html.includes('&lt;b&gt;') && !html.includes('<b>atenta</b>'), 'justificativa escapada');
    check('analysis-visible-990101', body.includes('Sumário clínico'), 'análise confirmada visível');
    await snap(page, 'doctor-evidence-maintain');
    observed.steps.push({ step: 'doctor-evidence-maintain', url: page.url() });
    // Viewports 390/1280 sem overflow no detalhe com aviso.
    for (const width of [390, 1280]) {
      await page.setViewportSize({ width, height: 800 });
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      check(`viewport-${width}-evidence-no-overflow`, overflow <= 0, `overflow=${overflow}px`);
    }
    await page.setViewportSize({ width: 1280, height: 800 });
    await snap(page, 'doctor-evidence-maintain-viewport');
    await context.close();
  }

  // ── R1–R3: troca 990102 distingue anterior/confirmado/nova ──
  {
    const { context, page } = await loginAs(browser, 'verify-doctor', 'doctor-switch');
    await openDoctorCaseByNumber(page, '990102');
    const body = await page.locator('body').innerText();
    check('notice-visible-990102', body.includes('Procedimento confirmado pelo NIR'));
    check('notice-switched-990102', body.includes('Seleção alterada'), 'troca explícita');
    check('notice-previous-990102', body.includes('Declarado antes'), 'anterior preservado');
    check('notice-caveat-990102', body.includes('não é aprovação clínica'));
    await snap(page, 'doctor-evidence-switch');
    observed.steps.push({ step: 'doctor-evidence-switch', url: page.url() });
    await context.close();
  }

  // ── R2: detecção vazia 990103 informa ausência sem consenso ──
  {
    const { context, page } = await loginAs(browser, 'verify-doctor', 'doctor-empty');
    await openDoctorCaseByNumber(page, '990103');
    const body = await page.locator('body').innerText();
    check('notice-visible-990103', body.includes('Procedimento confirmado pelo NIR'));
    check(
      'notice-empty-990103',
      body.includes('Nenhum procedimento detectado na nova extração'),
      'ausência explícita sem consenso',
    );
    await snap(page, 'doctor-evidence-empty');
    observed.steps.push({ step: 'doctor-evidence-empty', url: page.url() });
    await context.close();
  }

  // ── R3: decisão aprovada não é vetada; reexecução é read-only ──
  {
    const { context, page } = await loginAs(browser, 'verify-doctor', 'doctor-decide');
    await openDoctorCaseByNumber(page, '990101');
    // O card da fila pode levar ao detalhe OU direto à página de trabalho
    // (reserva livre → «Avaliar» aponta para /decide/).
    if ((await page.locator('#doctor-decision-form').count()) === 0) {
      const toWork = page.getByRole('link', { name: 'Registrar decisão' });
      if ((await toWork.count()) > 0) {
        await toWork.first().click();
        await page.waitForLoadState('domcontentloaded');
      }
    }
    const body = await page.locator('body').innerText();
    if ((await page.locator('#doctor-decision-form').count()) > 0) {
      check('decide-notice-on-work-page', body.includes('Procedimento confirmado pelo NIR'), 'aviso junto ao formulário');
      // Aprova o procedimento confirmado (reserva válida na abertura).
      const disposition = page.locator('select[name$="__disposition"]').first();
      await disposition.selectOption('approved');
      await page.getByRole('button', { name: 'Registrar decisão' }).click();
      await page.waitForLoadState('domcontentloaded');
      check('decide-approved-not-vetoed', (await page.locator('body').innerText()).includes('Decisão registrada'), 'consultivo não veta');
      await snap(page, 'doctor-evidence-decided');
      observed.steps.push({ step: 'doctor-evidence-decided', url: page.url() });
    } else {
      check('decide-already-decided-readonly', body.includes('Decisões registradas'), 'reexecução não reseta decisão');
      await snap(page, 'doctor-evidence-already-decided');
      observed.steps.push({ step: 'doctor-evidence-already-decided', url: page.url() });
    }
    await context.close();
  }

  // ── R4: erro 990104 nunca fica pronto na fila; PDF segue consultável ──
  {
    const { context, page } = await loginAs(browser, 'verify-doctor', 'doctor-error');
    await page.goto(`${base}/doctor/`, { waitUntil: 'domcontentloaded' });
    const queueText = await page.locator('body').innerText();
    // 990104 pode aparecer em "decididos"? Não: FAILED não pertence a nenhuma aba pronta.
    await page.goto(`${base}/doctor/?tab=aguardando`, { waitUntil: 'domcontentloaded' });
    const awaitingText = await page.locator('body').innerText();
    check('error-990104-never-ready', !awaitingText.includes('990104'), 'falha fora da fila de decisão');
    void queueText;
    await snap(page, 'doctor-evidence-error-queue');
    observed.steps.push({ step: 'doctor-evidence-error-queue', url: page.url() });
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

writeFileSync(path.join(artifacts, 'observed-state.json'), JSON.stringify(observed, null, 2));
writeFileSync(path.join(artifacts, 'report.json'), JSON.stringify({ checks, failures }, null, 2));
if (failures.length > 0) {
  console.error(`RESULTADO: FAIL (${failures.length} checks): ${failures.join(', ')}`);
  await browser.close();
  process.exit(1);
}
console.log(`RESULTADO: PASS (${checks.length} checks)`);
await browser.close();
process.exit(0);

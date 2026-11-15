// Drive Playwright pinado do verify-hmd — ui-parity-intake-detail slice 003.
// Jornada detalhe-papeis: médico decide (aprova) o VERIFY-001 sintético na
// página de trabalho no novo layout ats-web → agendador confirma unit 1 no
// detalhe no novo layout (reserva/heartbeat intactos) → NIR confirma o
// recebimento no detalhe (CLEANED). Tudo pela UI real (login pelo form,
// selects/inputs/botões do browser), sem mutação direta de banco nem
// mudança de seed/backend. Evidência em <run>/artifacts/.
// Exit: 0 PASS · 1 falha observada · 2 infra/driver.
import { execSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync, readlinkSync, writeFileSync } from 'node:fs';
import path from 'node:path';

import { chromium } from 'playwright';

const runArg = process.argv.indexOf('--run');
if (runArg < 0 || !process.argv[runArg + 1]) {
  console.error('uso: drive-detalhe-papeis.mjs --run <tmp/verification/hmd-verify-...>');
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
let caseId = null;

try {
  // 1) Médico: fila → página de trabalho (novo layout) → aprova tudo.
  {
    const { context, page } = await loginAs(browser, 'verify-doctor', 'doctor-detalhe');
    check('doctor-queue-heading', (await page.getByRole('heading', { name: 'Casos aguardando decisão' }).count()) >= 1);
    const doctorBody = await page.locator('body').innerText();
    check('doctor-sees-VERIFY-001', doctorBody.includes('VERIFY-001'));
    const decideHref = await page.locator('[data-agency-record-number="VERIFY-001"] a.stretched-link').getAttribute('href').catch(() => null);
    const decideMatch = (decideHref || '').match(/\/doctor\/case\/([0-9a-f-]+)\/decide\//);
    check('doctor-case-found-in-queue', !!decideMatch, decideHref || 'sem link');
    if (decideMatch) {
      caseId = decideMatch[1];
      await page.goto(`${base}/doctor/case/${caseId}/decide/`, { waitUntil: 'domcontentloaded' });
      check('doctor-decide-layout', (await page.locator('.doctor-decision-layout').count()) === 1);
      check('doctor-report-cards', (await page.locator('.doctor-report-card').count()) >= 5, `${await page.locator('.doctor-report-card').count()} cards`);
      check('doctor-report-grid', (await page.locator('.doctor-report-grid').count()) === 1);
      check('doctor-report-block-body', (await page.locator('.report-block-body').count()) >= 1);
      check('doctor-decision-form-card', (await page.locator('.doctor-decision-form-card').count()) === 1);
      check('doctor-lock-config', (await page.locator('#work-lock-config').count()) === 1);
      check('doctor-lock-token', (await page.locator('input[name="lock_token"][value]:not([value=""])').count()) >= 1);
      // Ordem clínica contratada (doctor-detail-context) no DOM.
      const titles = await page.locator('h2.h5').evaluateAll((els) => els.map((el) => el.textContent.trim()));
      const expected = ['Procedimentos do caso', 'Sumário clínico', 'Estrutura extraída', 'Prior-case', 'Documentos', 'Requisitos gerais acionáveis', 'Alertas consultivos'];
      const positions = expected.map((t) => titles.indexOf(t));
      check('doctor-clinical-order', positions.every((p, i) => p >= 0 && (i === 0 || p > positions[i - 1])), titles.join(' | '));
      check('doctor-decide-content', (await page.locator('body').innerText()).includes('Paciente Sintético Um'));
      await snap(page, 'doctor-detalhe-decide');
      observed.steps.push({ step: 'doctor-detalhe-decide', url: page.url() });
      const dispositions = page.locator('select[name$="__disposition"]');
      const reasons = page.locator('textarea[name$="__reason"]');
      const dispositionCount = await dispositions.count();
      check('doctor-decide-procedures-present', dispositionCount >= 1, `${dispositionCount} decisões`);
      for (let i = 0; i < dispositionCount; i++) {
        await dispositions.nth(i).selectOption('approved');
      }
      const reasonCount = await reasons.count();
      for (let i = 0; i < reasonCount; i++) {
        await reasons.nth(i).fill('aprovado na jornada detalhe-papeis');
      }
      await snap(page, 'doctor-detalhe-decide-filled');
      await page.getByRole('button', { name: 'Registrar decisão' }).click();
      await page.waitForURL(new RegExp(`/doctor/case/${caseId}/`), { timeout: 20000 }).catch(() => {});
      const decidedBody = await page.locator('body').innerText();
      check('doctor-decision-recorded', page.url().includes(`/doctor/case/${caseId}`) && decidedBody.includes('Decisões registradas'), page.url());
      check('doctor-decided-approved', decidedBody.includes('Aprovado'));
      check('doctor-detail-layout', (await page.locator('.doctor-decision-layout').count()) === 1);
      await snap(page, 'doctor-detalhe-decided');
      observed.steps.push({ step: 'doctor-detalhe-decided', url: page.url() });
    }
    await context.close();
  }

  // 2) Agendador: fila → detalhe (novo layout) → confirma unit 1.
  {
    if (!caseId) {
      check('scheduler-case-found', false, 'caseId não encontrado');
    } else {
      const { context, page } = await loginAs(browser, 'verify-scheduler', 'scheduler-detalhe');
      check('scheduler-queue-heading', (await page.getByRole('heading', { name: 'Fila de agendamento' }).count()) >= 1);
      const schedHref = await page.locator('[data-agency-record-number="VERIFY-001"] a.list-group-item-action').getAttribute('href').catch(() => null);
      check('scheduler-case-found-in-queue', !!schedHref && schedHref.includes(caseId), schedHref || 'sem link');
      await page.goto(`${base}/scheduler/case/${caseId}/`, { waitUntil: 'domcontentloaded' });
      check('scheduler-detail-layout', (await page.locator('.detail-layout').count()) === 1);
      check('scheduler-report-cards', (await page.locator('.report-card').count()) >= 4, `${await page.locator('.report-card').count()} cards`);
      check('scheduler-summary-box', (await page.locator('.summary-box').count()) >= 1);
      check('scheduler-decision-form-cards', (await page.locator('.scheduler-decision-form-card').count()) >= 2, `${await page.locator('.scheduler-decision-form-card').count()} cards`);
      check('scheduler-lock-config', (await page.locator('#work-lock-config').count()) === 1);
      check('scheduler-lock-token', (await page.locator('input[name="lock_token"][value]:not([value=""])').count()) >= 1);
      const schedBody = await page.locator('body').innerText();
      check('scheduler-detail-content', schedBody.includes('Paciente Sintético Um') && schedBody.includes('Aprovado') && schedBody.includes('Comunicações'));
      await snap(page, 'scheduler-detalhe-confirm');
      observed.steps.push({ step: 'scheduler-detalhe-confirm', url: page.url() });
      // Confirmação: unidade 1 + data/hora futuras + local (inputs nativos).
      const tomorrow = new Date(Date.now() + 86400000);
      const yyyy = tomorrow.getFullYear();
      const mm = String(tomorrow.getMonth() + 1).padStart(2, '0');
      const dd = String(tomorrow.getDate()).padStart(2, '0');
      const confirmCard = page.locator('.scheduler-decision-form-card').first();
      await confirmCard.locator('select[name="unit"]').selectOption('1');
      await confirmCard.locator('input[name="scheduled_date"]').fill(`${yyyy}-${mm}-${dd}`);
      await confirmCard.locator('input[name="scheduled_time"]').fill('09:00');
      await confirmCard.locator('input[name="scheduled_location"]').fill('Sala de hemodinâmica — jornada detalhe-papeis');
      await snap(page, 'scheduler-detalhe-confirm-filled');
      await confirmCard.getByRole('button', { name: 'Confirmar' }).click();
      await page.waitForLoadState('domcontentloaded');
      const confirmedBody = await page.locator('body').innerText();
      check('scheduler-confirmed', confirmedBody.includes('Agendamento') && confirmedBody.includes('Sala de hemodinâmica'), page.url());
      await snap(page, 'scheduler-detalhe-confirmed');
      observed.steps.push({ step: 'scheduler-detalhe-confirmed', url: page.url() });
      await context.close();
    }
  }

  // 3) NIR: detalhe (novo layout) → confirma recebimento (CLEANED).
  {
    if (!caseId) {
      check('nir-case-found', false, 'caseId não encontrado');
    } else {
      const { context, page } = await loginAs(browser, 'verify-nir', 'nir-detalhe');
      await page.goto(`${base}/intake/cases/${caseId}/`, { waitUntil: 'domcontentloaded' });
      check('nir-detail-layout', (await page.locator('.detail-layout').count()) === 1);
      check('nir-report-cards', (await page.locator('.report-card').count()) >= 4, `${await page.locator('.report-card').count()} cards`);
      check('nir-report-block-body', (await page.locator('.report-block-body').count()) >= 1);
      const nirBody = await page.locator('body').innerText();
      check('nir-detail-content', nirBody.includes('Paciente Sintético Um') && nirBody.includes('Resultado') && nirBody.includes('Arteriografia periférica'));
      const ackButton = page.getByRole('button', { name: 'Confirmar recebimento' });
      check('nir-ack-visible', (await ackButton.count()) === 1);
      await snap(page, 'nir-detalhe-before-ack');
      observed.steps.push({ step: 'nir-detalhe-before-ack', url: page.url() });
      await ackButton.click();
      await page.waitForLoadState('domcontentloaded');
      const cleanedBody = await page.locator('body').innerText();
      const cleanedLinkCount = await page.locator('a[href*="/resubmit/"]').count();
      check('nir-cleaned', cleanedBody.includes('Caso concluído') || cleanedLinkCount > 0, page.url());
      check('nir-resubmit-link-visible', cleanedLinkCount >= 1);
      await snap(page, 'nir-detalhe-cleaned');
      observed.steps.push({ step: 'nir-detalhe-cleaned', url: page.url() });
      await context.close();
    }
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

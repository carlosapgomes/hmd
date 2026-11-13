// Driver Playwright pinado do verify-hmd — slice 005 páginas de sessão.
// Login REAL pelo form (sem addCookies): toggle de senha no login (R1),
// perfil/manual no padrão (R3) e jornada de notificações (R2): 3 não
// lidas → marca 1 sem abrir → sino 3→2 → abrir caso → marcar todas →
// sino zerado; item alheio retorna 404. Evidência em <run>/artifacts/.
// Exit: 0 PASS · 1 falha observada · 2 infra/driver.
import { execSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync, readlinkSync, writeFileSync } from 'node:fs';
import path from 'node:path';

import { chromium } from 'playwright';

const runArg = process.argv.indexOf('--run');
if (runArg < 0 || !process.argv[runArg + 1]) {
  console.error('uso: drive-session-pages.mjs --run <tmp/verification/hmd-verify-...>');
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
  console.error(`infra: artefatos ausentes em ${artifacts}.`);
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
  if (readFileSync('/proc/sys/kernel/random/boot_id', 'utf8').trim() !== manifest.bootId) {
    refuseOwned('instância de outro boot');
  }
  const environ = readFileSync(`/proc/${pid}/environ`, 'utf8').split('\0');
  if (!environ.includes(`HMD_VERIFY_MARKER=${manifest.marker}`)) refuseOwned('sem marcador');
  if (!environ.includes(`HMD_VERIFY_BUILD=${manifest.build}`)) refuseOwned('sem build');
  if (!manifest.staticRoot) refuseOwned('staticRoot ausente');
  if (!String(manifest.staticRoot).startsWith(`${runDir}/`)) refuseOwned('staticRoot fora da run');
  if (!environ.includes(`HMD_VERIFY_STATIC_ROOT=${manifest.staticRoot}`)) refuseOwned('staticRoot divergente');
  const cmdline = readFileSync(`/proc/${pid}/cmdline`, 'utf8').replaceAll('\0', ' ');
  if (!cmdline.includes('config.settings.verify') || !cmdline.includes(`127.0.0.1:${manifest.port}`)) {
    refuseOwned('cmdline sem verify/loopback');
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
  if (!ownsSocket) refuseOwned('socket LISTEN não detido');
  const git = (args) => execSync(`git ${args}`, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
  const head = git('rev-parse HEAD').trim();
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
    hash.update(`${file} ${createHash('sha256').update(readFileSync(file)).digest('hex')}\n`);
  }
  if (`${head}-${hash.digest('hex').slice(0, 12)}` !== manifest.build) refuseOwned('build divergiu');
}
assertOwnedInstance();

const REQUIRED_ASSETS = [
  '/static/css/app.css',
  '/static/js/sw.js',
  '/static/js/bell_poll.js',
  '/static/js/password_toggle.js',
];
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
async function loginAs(browser, username, label, viewport = null) {
  const opts = viewport ? { viewport } : {};
  const context = await browser.newContext(opts);
  const page = await context.newPage();
  page.on('response', (res) => {
    if (res.url().includes('/static/') && res.status() >= 400) failedAssets.push(`${res.status()} ${res.url()}`);
  });
  await page.goto(`${base}/login/`, { waitUntil: 'domcontentloaded' });
  await page.getByLabel('Usuário').fill(username);
  await page.getByLabel('Senha', { exact: true }).fill(PASSWORD);
  await page.getByRole('button', { name: 'Entrar' }).click();
  // Espera a saída do /login/ no próprio documento (resiliente ao
  // redirect em cadeia POST→/→home, que o waitForURL pode perder).
  await page.waitForFunction(() => !location.pathname.endsWith('/login/'), { timeout: 15000 });
  return { context, page };
}
const visibleBellCount = (page) =>
  page.evaluate(() => {
    const bells = [...document.querySelectorAll('[data-notifications-badge]')];
    const bell = bells.find((el) => el.offsetParent !== null) || bells[0];
    return bell ? bell.getAttribute('data-count') : null;
  });
const runVerifyPy = (code) => {
  const py = existsSync('.venv/bin/python') ? '.venv/bin/python' : 'uv run python';
  return execSync(`${py} manage.py shell --settings=config.settings.verify -c "${code.replace(/"/g, '\\"')}"`, {
    env: {
      ...process.env,
      DJANGO_SETTINGS_MODULE: 'config.settings.verify',
      VERIFY_DB_HOST: manifest.dbHost || '127.0.0.1',
      VERIFY_DB_PORT: String(manifest.dbPort || process.env.TEST_DB_PORT || '55435'),
      VERIFY_DB_NAME: manifest.database,
      VERIFY_DB_USER: process.env.VERIFY_DB_USER || 'hmd',
      VERIFY_DB_PASSWORD: process.env.VERIFY_DB_PASSWORD || 'hmd_dev',
    },
    stdio: ['ignore', 'pipe', 'pipe'],
    encoding: 'utf8',
  });
};
const createNotification = (username, ref, title) =>
  runVerifyPy(
    "from apps.accounts.models import UserNotification; " +
      "from django.contrib.auth import get_user_model; " +
      "from apps.cases.models import Case; " +
      `u=get_user_model().objects.get(username='${username}'); ` +
      `c=Case.objects.filter(agency_record_number='${ref}').first(); ` +
      `n=UserNotification.objects.create(recipient=u, case=c, event=None, notification_type='final_reply_posted', title='${title}', body_preview='Aviso de verificacao'); ` +
      "print(n.notification_id)",
  );

let browser;
try {
  browser = await chromium.launch({ headless: true });
} catch (err) {
  console.error(`infra BLOCKED: Chromium pinado indisponível (${err.message}).`);
  process.exit(2);
}
const observed = { runId: manifest.runId, base, database: manifest.database, steps: [] };
const notificationsJourney = {};
const sessoesJourney = {};
try {
  // R1: toggle de senha no login anônimo (clique real, troca de type).
  {
    const context = await browser.newContext();
    const page = await context.newPage();
    page.on('response', (res) => {
      if (res.url().includes('/static/') && res.status() >= 400) failedAssets.push(`${res.status()} ${res.url()}`);
    });
    await page.goto(`${base}/login/`, { waitUntil: 'domcontentloaded' });
    const toggle = page.getByRole('button', { name: 'Mostrar senha' });
    check('login-toggle-present', (await toggle.count()) === 1, 'botão Mostrar senha');
    check('login-input-group', (await page.locator('.input-group').count()) >= 1, 'input-group');
    const input = page.getByLabel('Senha', { exact: true });
    check('login-password-type-initial', (await input.getAttribute('type')) === 'password', 'type=password');
    await toggle.click();
    check('login-password-type-shown', (await input.getAttribute('type')) === 'text', 'type=text após clique');
    check('login-toggle-ocultar', (await page.getByRole('button', { name: 'Ocultar senha' }).count()) === 1, 'botão Ocultar senha');
    await page.getByRole('button', { name: 'Ocultar senha' }).click();
    check('login-password-type-hidden', (await input.getAttribute('type')) === 'password', 'type=password após 2º clique');
    const body = await page.locator('body').innerText();
    const html = await page.content();
    check('login-transient-notice', body.includes('Acesso local transitório'), 'aviso transitório');
    check('login-no-reset-link', !html.toLowerCase().includes('esqueci') && !html.toLowerCase().includes('password_reset'), 'sem reset');
    for (const asset of REQUIRED_ASSETS) {
      const status = await page.evaluate(async (u) => (await fetch(u, { cache: 'no-store' })).status, asset);
      check(`static-login-${asset.split('/').pop()}`, status === 200, `${asset} HTTP ${status}`);
    }
    await snap(page, 'login-before-toggle');
    sessoesJourney.toggle = { typeAfterClick: 'text', notice: true, noReset: true };
    observed.steps.push({ step: 'login-password-toggle', url: page.url() });
    await context.close();
  }

  // R3: perfil e manual no padrão (médico logado pelo form).
  {
    const { context, page } = await loginAs(browser, 'verify-doctor', 'doctor');
    check('doctor-home-url', page.url().includes('/doctor/'), page.url());
    await page.goto(`${base}/profile/`, { waitUntil: 'domcontentloaded' });
    check('profile-page-title', (await page.getByRole('heading', { name: 'Meu perfil' }).count()) >= 1, 'h1 Meu perfil');
    check('profile-cards', (await page.locator('.card').count()) >= 2, `cards=${await page.locator('.card').count()}`);
    check('profile-back-link', (await page.getByRole('link', { name: '← Voltar' }).count()) >= 1, 'Voltar');
    await snap(page, 'doctor-profile');
    observed.steps.push({ step: 'doctor-profile', url: page.url() });
    await page.goto(`${base}/manual/`, { waitUntil: 'domcontentloaded' });
    check('manual-page-title', (await page.getByRole('heading', { name: 'Manual de uso' }).count()) >= 1, 'h1 Manual de uso');
    check('manual-section-cards', (await page.locator('section.card').count()) >= 5, `seções em card=${await page.locator('section.card').count()}`);
    const manualHtml = await page.content();
    check('manual-printable', manualHtml.includes('@media print'), '@media print');
    check('manual-role-sections', (await page.getByRole('heading', { name: /NIR — envio/ }).count()) >= 1 && (await page.getByRole('heading', { name: /Médico — decisão/ }).count()) >= 1 && (await page.getByRole('heading', { name: /Agendador — confirmação/ }).count()) >= 1, 'seções por papel');
    await snap(page, 'doctor-manual');
    observed.steps.push({ step: 'doctor-manual', url: page.url() });
    sessoesJourney.profileManual = { profileCards: true, manualCards: true, printable: true };
    await context.close();
  }

  // R2: 3 não lidas → marca 1 sem abrir → sino 2 → abrir → marcar todas → 0.
  createNotification('verify-doctor', 'VERIFY-001', 'Aviso de verificacao 1');
  createNotification('verify-doctor', 'VERIFY-001', 'Aviso de verificacao 2');
  createNotification('verify-doctor', 'VERIFY-002', 'Aviso de verificacao 3');
  const nirNotificationId = createNotification('verify-nir', 'VERIFY-001', 'Aviso de verificacao NIR').trim().split('\n').pop().trim();
  {
    const { context, page } = await loginAs(browser, 'verify-doctor', 'doctor-notificacoes');
    check('badge-three', (await visibleBellCount(page)) === '3', `sino=${await visibleBellCount(page)} SSR`);
    await page.goto(`${base}/notifications/`, { waitUntil: 'domcontentloaded' });
    const listBody = await page.locator('body').innerText();
    check('list-case-reference', listBody.includes('VERIFY-001') && listBody.includes('VERIFY-002'), 'referência do caso');
    check('list-mark-read-count', (await page.getByRole('button', { name: 'Marcar como lida' }).count()) === 3, `marca-1=${await page.getByRole('button', { name: 'Marcar como lida' }).count()}`);
    check('list-open-count', (await page.getByRole('button', { name: 'Abrir caso' }).count()) === 3, 'abrir=3');
    check('list-back-link', (await page.getByRole('link', { name: '← Voltar' }).count()) >= 1, 'Voltar');
    const pollUrl = await page.evaluate(() => document.querySelector('[data-notifications-badge]')?.getAttribute('data-unread-count-url'));
    check('list-polling-marker', pollUrl === '/notifications/unread-count/', String(pollUrl));
    await snap(page, 'doctor-notifications');
    notificationsJourney.badgeBefore = '3';

    // Marca 1 SEM abrir: volta à lista, só ela muda, sino 3→2.
    await page.getByRole('button', { name: 'Marcar como lida' }).first().click();
    await page.waitForURL((url) => url.pathname === '/notifications/', { timeout: 15000 });
    check('after-mark1-badge-two', (await visibleBellCount(page)) === '2', `sino=${await visibleBellCount(page)}`);
    check('after-mark1-two-unread-left', (await page.getByRole('button', { name: 'Marcar como lida' }).count()) === 2, '2 não lidas restantes');
    await snap(page, 'doctor-notifications-after-mark1');
    notificationsJourney.badgeAfterMark1 = '2';

    // Abre outro item (NÃO lido): o item já lido segue com "Abrir caso",
    // então o clique mira o botão dentro do primeiro item ainda com
    // "Marcar como lida". Aterrissa no detalhe do médico, sino cai para 1.
    await page
      .locator('.list-group-item', {
        has: page.getByRole('button', { name: 'Marcar como lida' }),
      })
      .first()
      .getByRole('button', { name: 'Abrir caso' })
      .click();
    await page.waitForURL((url) => /\/doctor\/case\//.test(url.pathname), { timeout: 15000 });
    check('after-open-case-url', /\/doctor\/case\//.test(page.url()), page.url());
    check('after-open-badge-one', (await visibleBellCount(page)) === '1', `sino=${await visibleBellCount(page)}`);
    await snap(page, 'doctor-notifications-after-open');
    notificationsJourney.badgeAfterOpen = '1';

    // Item alheio (notificação do NIR) via POST com CSRF → 404, sem marcar.
    const foreignStatus = await page.evaluate(async (id) => {
      const token = document.querySelector('meta[name="csrf-token"]')?.content || '';
      const res = await fetch(`/notifications/${id}/read/`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'X-CSRFToken': token, 'X-Requested-With': 'XMLHttpRequest' },
      });
      return res.status;
    }, nirNotificationId);
    check('foreign-read-404', foreignStatus === 404, `HTTP ${foreignStatus}`);
    notificationsJourney.foreignStatus = foreignStatus;

    // Marcar todas → sino zerado.
    await page.goto(`${base}/notifications/`, { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: 'Marcar todas como lidas' }).click();
    await page.waitForURL((url) => url.pathname === '/notifications/', { timeout: 15000 });
    check('after-markall-badge-zero', (await visibleBellCount(page)) === '0', `sino=${await visibleBellCount(page)}`);
    check('after-markall-no-unread-forms', (await page.getByRole('button', { name: 'Marcar como lida' }).count()) === 0, 'sem formulários de leitura');
    await snap(page, 'doctor-notifications-after-markall');
    notificationsJourney.badgeAfterMarkAll = '0';
    observed.steps.push({ step: 'notifications-3-to-2-to-0', url: page.url() });
    await context.close();
  }
  check('no-failed-static-assets', failedAssets.length === 0, failedAssets.join('; '));
} finally {
  await browser.close();
}
writeFileSync(path.join(artifacts, 'sessoes.json'), JSON.stringify(sessoesJourney, null, 2));
writeFileSync(path.join(artifacts, 'notifications.json'), JSON.stringify(notificationsJourney, null, 2));
const report = { ...observed, checks, failures, failedAssets };
writeFileSync(path.join(artifacts, 'report-session-pages.json'), JSON.stringify(report, null, 2));
if (failures.length > 0) {
  console.error(`FAIL ${failures.length} checks: ${failures.join(', ')}`);
  process.exit(1);
}
console.log(`PASS ${checks.length} checks`);

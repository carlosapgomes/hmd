// Driver Playwright pinado do verify-hmd — fila do agendador busca+refresh.
// Login REAL pelo form (sem addCookies): fila do agendador (slice 002)
// ui-parity-queues): busca client-side sem reload, estados de reserva e
// partial htmx 20 s com reaplicação da busca.
// Evidência em <run>/artifacts/.
// Exit: 0 PASS · 1 falha observada · 2 infra/driver.
import { execSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync, readlinkSync, writeFileSync } from 'node:fs';
import path from 'node:path';

import { chromium } from 'playwright';

const runArg = process.argv.indexOf('--run');
if (runArg < 0 || !process.argv[runArg + 1]) {
  console.error('uso: drive-fila-agendamento-busca-refresh.mjs --run <tmp/verification/hmd-verify-...>');
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
  '/static/js/scheduler_queue_filter.js',
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
const runVerifyPy2 = (code) => {
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
const visibleCards = (page) =>
  page.evaluate(() => [...document.querySelectorAll('[data-scheduler-queue-card]')].filter((el) => !el.hidden && el.offsetParent !== null).length);

let browser;
try {
  browser = await chromium.launch({ headless: true });
} catch (err) {
  console.error(`infra BLOCKED: Chromium pinado indisponível (${err.message}).`);
  process.exit(2);
}
const observed = { runId: manifest.runId, base, database: manifest.database, steps: [] };
const buscaJourney = {};
try {
  // Setup (via verify settings): o seed só cria casos AWAITING_DOCTOR —
  // aqui eles avançam até SCHEDULER_REQUESTED (fila do agendador), com
  // 17 de enchimento para encher a página 1 (20), 1 fora da página
  // (days_on_screen null → última posição) e 2 com reserva (própria e de
  // outro agendador). Criados antes do login: o load inicial já os inclui.
  runVerifyPy2(
    "from django.contrib.auth import get_user_model\n" +
    "from apps.accounts.models import Role\n" +
    "from apps.cases.locks import claim_case_lock\n" +
    "from apps.cases.models import Case, CaseProcedure\n" +
    "from apps.cases.procedures import record_doctor_procedure_decisions\n" +
    "u = get_user_model().objects.get(username='verify-nir')\n" +
    "doc = get_user_model().objects.get(username='verify-doctor')\n" +
    "sched = get_user_model().objects.get(username='verify-scheduler')\n" +
    "other, _ = get_user_model().objects.get_or_create(username='verify-scheduler-outro', defaults={'first_name': 'Verificação', 'last_name': 'Sintético scheduler', 'is_active': True, 'account_status': 'active'})\n" +
    "role, _ = Role.objects.get_or_create(name='scheduler')\n" +
    "other.roles.add(role)\n" +
    "def make(rec, name, types, days):\n" +
    "    c = Case.objects.create(created_by=u)\n" +
    "    for t in types:\n" +
    "        CaseProcedure.objects.create(case=c, procedure_type=t, declared_by_nir=True)\n" +
    "    c.start_pdf_extraction(user=None, role='system')\n" +
    "    c.complete_pdf_extraction(user=None, role='system')\n" +
    "    c.complete_anonymization(user=None, role='system')\n" +
    "    c.complete_llm_extraction(user=None, role='system')\n" +
    "    c.complete_llm_summarization(user=None, role='system')\n" +
    "    record_doctor_procedure_decisions(c, {t: ('approved', '') for t in types}, user=doc, role='doctor')\n" +
    "    Case.objects.filter(pk=c.pk).update(agency_record_number=rec, patient_name=name, patient_age=50, days_on_screen=days)\n" +
    "    return c\n" +
    "make('VERIFY-S01', 'Paciente Sintético Agendamento Um', ('art_perif', 'cat_cardiaco'), 3)\n" +
    "make('VERIFY-S02', 'Paciente Sintético Agendamento Dois', ('art_perif',), 1)\n" +
    "mine = make('VERIFY-S10', 'Paciente Sintético Agendamento Meu', ('art_perif',), 2)\n" +
    "claim_case_lock(mine, user=sched, context='scheduler_confirm', role='scheduler')\n" +
    "other_case = make('VERIFY-S11', 'Paciente Sintético Agendamento Outro', ('art_perif',), 2)\n" +
    "claim_case_lock(other_case, user=other, context='scheduler_confirm', role='scheduler')\n" +
    "for i in range(1, 18):\n" +
    "    make('VERIFY-SF%02d' % i, 'Paciente Sintético Agendamento Relleno %02d' % i, ('art_perif',), 0)\n" +
    "make('VERIFY-S99', 'Paciente Sintético Fora da Página Agendamento', ('art_perif',), None)\n" +
    "print('fixtures ok')",
  );
  const { context, page } = await loginAs(browser, 'verify-scheduler', 'scheduler-busca-refresh');
  check('scheduler-home-url', page.url().includes('/scheduler/'), page.url());
  check('queue-heading', (await page.getByRole('heading', { name: 'Fila de agendamento' }).count()) === 1, 'h1 Fila de agendamento');
  const html = await page.content();
  check('queue-no-nav-tabs', !html.includes('nav-tabs'), 'sem bloco legado nav-tabs');
  check('queue-pills', html.includes('>Pendentes') && html.includes('>Processados'), 'pills Pendentes/Processados');
  check('queue-case-card', (await page.locator('.case-card').count()) >= 2, `case-card=${await page.locator('.case-card').count()}`);
  check('queue-summary-box', (await page.locator('.summary-box').count()) >= 2, 'summary-box');
  check('queue-waiting-time', (await page.locator('.waiting-time').count()) >= 2, 'waiting-time');
  check('queue-status-badge', (await page.locator('.status-badge').count()) >= 2, 'status-badge');
  const pulseStyle = await page.evaluate(() => {
    const el = document.querySelector('.pulse-dot');
    if (!el) return null;
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return { display: style.display, width: style.width, height: style.height, backgroundColor: style.backgroundColor, animationName: style.animationName, widthPx: rect.width, heightPx: rect.height };
  });
  // O span é item flex do `.alert.d-flex` (display blockificado) — o visível
  // é provado por tamanho 8×8, cor do token de perigo e animação pulsante.
  check('queue-pulse-dot-visible', !!pulseStyle && pulseStyle.width === '8px' && pulseStyle.height === '8px' && pulseStyle.backgroundColor === 'rgb(181, 53, 53)' && pulseStyle.animationName.includes('pulse-dot') && pulseStyle.widthPx === 8 && pulseStyle.heightPx === 8, JSON.stringify(pulseStyle));
  check('queue-sees-VERIFY-S01', html.includes('VERIFY-S01') && html.includes('Paciente Sintético Agendamento Um'), 'fixture VERIFY-S01');
  check('queue-days-on-screen', html.includes('3 d em tela'), '3 d em tela');
  check('queue-lock-own-continuar', await page.locator('[data-patient-name="Paciente Sintético Agendamento Meu"]').innerText().then((t) => t.includes('Continuar')), 'reserva própria → Continuar');
  const otherCardText = await page.locator('[data-patient-name="Paciente Sintético Agendamento Outro"]').innerText();
  check('queue-lock-other-reserved', otherCardText.includes('Reservado'), 'reserva de outro → Reservado');
  check('queue-lock-other-holder', otherCardText.includes('Sintético'), 'portador exibido');
  check('queue-lock-other-no-action', (await page.locator('[data-patient-name="Paciente Sintético Agendamento Outro"] a[href*="/scheduler/case/"]').count()) === 0, 'sem ação de abrir no card reservado');
  const hxGet = await page.locator('#scheduler-queue-content').getAttribute('hx-get');
  check('queue-htmx-partial', typeof hxGet === 'string' && hxGet.includes('partials/queue/'), String(hxGet));
  check('queue-htmx-20s', (await page.locator('#scheduler-queue-content').getAttribute('hx-trigger')) === 'every 20s', 'every 20s');
  check('queue-search-input', (await page.locator('[data-scheduler-queue-search]').count()) === 1, 'busca client-side');
  check('queue-filter-status-aria', (await page.locator('[data-scheduler-queue-filter-status]').getAttribute('aria-live')) === 'polite', 'aria-live polite');
  check('queue-visible-initial', (await visibleCards(page)) >= 2, `visíveis=${await visibleCards(page)}`);
  check('queue-page-one-full', (await page.locator('[data-scheduler-queue-card]').count()) === 20, `cards=${await page.locator('[data-scheduler-queue-card]').count()}`);
  await snap(page, 'scheduler-queue-busca-inicial');
  observed.steps.push({ step: 'scheduler-queue-busca-inicial', url: page.url() });

  // Busca por nome: filtra sem reload (mesma URL), ARIA atualiza.
  const urlBefore = page.url();
  await page.locator('[data-scheduler-queue-search]').fill('Agendamento Um');
  check('search-name-filters', (await visibleCards(page)) === 1, `visíveis=${await visibleCards(page)}`);
  check('search-name-no-reload', page.url() === urlBefore, page.url());
  check('search-name-status', (await page.locator('[data-scheduler-queue-filter-status]').innerText()).includes('1 de'), 'status ARIA');
  await snap(page, 'scheduler-queue-busca-nome');

  // Busca por ocorrência.
  await page.locator('[data-scheduler-queue-search]').fill('VERIFY-S02');
  check('search-occurrence-filters', (await visibleCards(page)) === 1, `visíveis=${await visibleCards(page)}`);
  check('search-occurrence-other-hidden', !(await page.locator('body').innerText()).includes('Paciente Sintético Agendamento Um'), 'Um oculto');

  // Termo sem resultado: alerta no-results.
  await page.locator('[data-scheduler-queue-search]').fill('zzz-sem-resultado');
  check('search-no-results', (await visibleCards(page)) === 0, '0 visíveis');
  check('search-no-results-alert', await page.locator('[data-scheduler-queue-no-results]').isVisible(), 'alerta no-results');
  await snap(page, 'scheduler-queue-busca-sem-resultado');

  // Limpar restaura.
  await page.locator('[data-scheduler-queue-clear]').click();
  check('search-clear-restores', (await visibleCards(page)) >= 2, `visíveis=${await visibleCards(page)}`);

  // Auto-refresh: cria caso compatível (setup via verify settings) e
  // aguarda o poll de 20 s — aparece sem reload. Caso fora da página
  // (VERIFY-S99, days_on_screen null → última posição) nem chega no
  // fragmento.
  runVerifyPy2(
    "from django.contrib.auth import get_user_model; " +
      "from apps.cases.models import Case, CaseProcedure; " +
      "from apps.cases.procedures import record_doctor_procedure_decisions; " +
      "u=get_user_model().objects.get(username='verify-nir'); " +
      "doc=get_user_model().objects.get(username='verify-doctor'); " +
      "c=Case.objects.create(created_by=u); " +
      "CaseProcedure.objects.create(case=c, procedure_type='art_perif', declared_by_nir=True); " +
      "c.start_pdf_extraction(user=None, role='system'); c.complete_pdf_extraction(user=None, role='system'); " +
      "c.complete_anonymization(user=None, role='system'); c.complete_llm_extraction(user=None, role='system'); " +
      "c.complete_llm_summarization(user=None, role='system'); " +
      "record_doctor_procedure_decisions(c, {'art_perif': ('approved', '')}, user=doc, role='doctor'); " +
      "Case.objects.filter(pk=c.pk).update(agency_record_number='VERIFY-S03', patient_name='Paciente Sintético Agendamento Três', patient_age=50, days_on_screen=2); " +
      "print(c.case_id)",
  );
  const pollPromise = page.waitForResponse((res) => res.url().includes('/partials/queue/'), { timeout: 40000 });
  // Busca ativa durante o swap: termo que casa só com o caso novo
  // (VERIFY-S03) — o htmx:afterSwap deve reaplicá-lo sobre o fragmento.
  await page.locator('[data-scheduler-queue-search]').fill('VERIFY-S03');
  await pollPromise;
  check('refresh-partial-polled', true, 'partial consultado pelo polling');
  check('refresh-search-term-preserved', (await page.locator('[data-scheduler-queue-search]').inputValue()) === 'VERIFY-S03', `termo=${await page.locator('[data-scheduler-queue-search]').inputValue()}`);
  check('refresh-search-matching-card-visible', await page.locator('[data-patient-name="Paciente Sintético Agendamento Três"]').isVisible(), 'caso compatível visível com busca ativa');
  check('refresh-search-nonmatching-hidden', !(await page.locator('[data-patient-name="Paciente Sintético Agendamento Um"]').isVisible()) && (await visibleCards(page)) === 1, `visíveis=${await visibleCards(page)}`);
  check('refresh-new-card-visible', await page.locator('[data-patient-name="Paciente Sintético Agendamento Três"]').isVisible(), 'caso compatível visível');
  check('refresh-out-of-page-absent', (await page.locator('[data-patient-name="Paciente Sintético Fora da Página Agendamento"]').count()) === 0, 'fora da página ausente');
  check('refresh-no-reload', page.url() === urlBefore, page.url());
  await snap(page, 'scheduler-queue-busca-refresh');
  observed.steps.push({ step: 'scheduler-queue-busca-refresh', url: page.url() });
  buscaJourney.search = true;
  buscaJourney.refresh = true;
  buscaJourney.lock = true;
  await context.close();
  check('no-failed-static-assets', failedAssets.length === 0, failedAssets.join('; '));
} finally {
  await browser.close();
}
writeFileSync(path.join(artifacts, 'fila-agendamento-busca-refresh.json'), JSON.stringify(buscaJourney, null, 2));
const report = { ...observed, checks, failures, failedAssets };
writeFileSync(path.join(artifacts, 'report-fila-agendamento-busca-refresh.json'), JSON.stringify(report, null, 2));
if (failures.length > 0) {
  console.error(`FAIL ${failures.length} checks: ${failures.join(', ')}`);
  process.exit(1);
}
console.log(`PASS ${checks.length} checks`);

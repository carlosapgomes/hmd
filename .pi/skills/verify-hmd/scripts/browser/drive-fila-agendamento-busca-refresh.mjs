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
const measures = { desktop1280: null, mobile390: null };
async function measure(page) {
  return page.evaluate(() => {
    const doc = document.documentElement;
    const headerBox = document.querySelector('header.app-header .container')?.getBoundingClientRect();
    const main = document.querySelector('main.app-shell');
    const mainBox = main?.getBoundingClientRect();
    const title = document.querySelector('h1.page-title');
    const card = document.querySelector('.case-card');
    const row = card?.querySelector(':scope > .row');
    const left = card?.querySelector('.col-lg-8');
    const right = card?.querySelector('.col-lg-4');
    const cta = card?.querySelector('a.stretched-link');
    const r = (el) => el ? (() => { const b = el.getBoundingClientRect(); return { x: Math.round(b.x), y: Math.round(b.y), width: Math.round(b.width), height: Math.round(b.height), left: Math.round(b.left), right: Math.round(b.right), top: Math.round(b.top) }; })() : null;
    const leftBox = r(left); const rightBox = r(right); const cardBox = r(card); const ctaBox = r(cta);
    return {
      viewport: { width: window.innerWidth, height: window.innerHeight },
      scrollWidth: doc.scrollWidth,
      clientWidth: doc.clientWidth,
      overflowX: doc.scrollWidth > doc.clientWidth,
      headerContainer: r(document.querySelector('header.app-header .container')),
      main: r(main),
      title: r(title),
      titleText: title?.textContent?.trim() || null,
      card: cardBox,
      rowIsDirectChild: !!row && row.parentElement === card,
      left: leftBox,
      right: rightBox,
      leftWidthRatio: leftBox && cardBox ? +(leftBox.width / cardBox.width).toFixed(3) : null,
      rightWidthRatio: rightBox && cardBox ? +(rightBox.width / cardBox.width).toFixed(3) : null,
      regionsSideBySide: !!(leftBox && rightBox && Math.abs(leftBox.top - rightBox.top) <= 2 && rightBox.left >= leftBox.left),
      regionsStacked: !!(leftBox && rightBox && rightBox.top >= leftBox.top + leftBox.height - 2),
      cta: ctaBox,
      ctaHeight: ctaBox ? ctaBox.height : null,
      ctaHref: cta?.getAttribute('href') || null,
      waitingCountInCard: card ? (card.textContent.match(/Aguardando há/g) || []).length : null,
      listGroupItemInCards: document.querySelectorAll('.case-card .list-group-item').length,
      alertWarningCount: document.querySelectorAll('.alert-warning').length,
      alertInfoCount: document.querySelectorAll('#scheduler-queue-content .alert-info').length,
    };
  });
}
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
    "CaseProcedure.objects.filter(case__agency_record_number='VERIFY-S01', procedure_type='cat_cardiaco').update(doctor_disposition='denied')\n" +
    "make('VERIFY-S02', 'Paciente Sintético Agendamento Dois', ('art_perif',), 1)\n" +
    "mine = make('VERIFY-S10', 'Paciente Sintético Agendamento Meu', ('art_perif',), 2)\n" +
    "claim_case_lock(mine, user=sched, context='scheduler_confirm', role='scheduler')\n" +
    "other_case = make('VERIFY-S11', 'Paciente Sintético Agendamento Outro', ('art_perif',), 2)\n" +
    "claim_case_lock(other_case, user=other, context='scheduler_confirm', role='scheduler')\n" +
    "for i in range(1, 18):\n" +
    "    make('VERIFY-SF%02d' % i, 'Paciente Sintético Agendamento Relleno %02d' % i, ('art_perif',), 0)\n" +
    "make('VERIFY-S99', 'Paciente Sintético Fora da Página Agendamento', ('art_perif',), None)\n" +
    "from apps.scheduler.services import confirm_case_scheduling\n" +
    "from django.utils import timezone\n" +
    "from datetime import timedelta\n" +
    "proc = make('VERIFY-S20', 'Paciente Sintético Agendamento Processado', ('art_perif',), 5)\n" +
    "confirm_case_scheduling(proc, unit=2, scheduled_datetime=timezone.now() + timedelta(days=2), scheduled_location='Sala de hemodinâmica', user=sched, role='scheduler')\n" +
    "print('fixtures ok')",
  );
  const { context, page } = await loginAs(browser, 'verify-scheduler', 'scheduler-busca-refresh', { width: 1280, height: 720 });
  check('scheduler-home-url', page.url().includes('/scheduler/'), page.url());
  check('queue-heading', (await page.getByRole('heading', { name: 'Solicitações de agendamento aguardando confirmação' }).count()) === 1, 'h1 Solicitações de agendamento aguardando confirmação');
  const html = await page.content();
  check('queue-no-col-lg-10', !html.includes('col-lg-10'), 'sem col-lg-10');
  check('queue-operational-scope', html.includes('operational-list-page'), 'escopo operacional');
  check('queue-no-nav-tabs', !html.includes('nav-tabs'), 'sem bloco legado nav-tabs');
  check('queue-pills', html.includes('>Pendentes') && html.includes('>Processados'), 'pills Pendentes/Processados');
  check('queue-case-card', (await page.locator('.case-card').count()) >= 2, `case-card=${await page.locator('.case-card').count()}`);
  check('queue-summary-box', (await page.locator('.summary-box').count()) >= 2, 'summary-box');
  check('queue-waiting-time', (await page.locator('.waiting-time').count()) >= 2, 'waiting-time');
  // OLI-002: pendentes sem badge genérico de status; badges de
  // procedimento/disposição preservados; vigar única; sem list-group.
  check('queue-no-generic-status-badge', (await page.locator('.status-badge').count()) === 0, 'sem status-badge genérico nos pendentes');
  check('queue-disposition-badges-remain', (await page.locator('.case-card .badge.rounded-pill').count()) >= 2, 'badges de procedimento/disposição preservados');
  check('queue-single-waiting-per-card', (await page.locator('.case-card').first().innerText()).match(/Aguardando há/g)?.length === 1, 'uma espera por card');
  check('queue-no-list-group-in-cards', (await page.locator('.case-card .list-group-item').count()) === 0, 'sem list-group-item no card');
  check('queue-card-row-direct', await page.evaluate(() => { const c = document.querySelector('.case-card'); return !!c && c.querySelector(':scope > .row') !== null; }), '.case-card > .row');
  check('queue-card-regions', (await page.locator('.case-card .col-lg-8').count()) >= 1 && (await page.locator('.case-card .col-lg-4').count()) >= 1, 'regiões 8/4 no DOM');
  check('queue-cta-stretched', (await page.locator('[data-scheduler-queue-card] a.stretched-link').count()) >= 1, 'CTA stretched-link');
  check('queue-info-alert', (await page.locator('#scheduler-queue-content > .alert-info').count()) === 1, 'contagem em alert-info');
  check('queue-no-warning-alert', (await page.locator('#scheduler-queue-content .alert-warning').count()) === 0, 'sem alert-warning na contagem');
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
  check('queue-days-on-screen', html.includes('Dias em tela: 3'), 'Dias em tela: 3');
  check('queue-no-compact-days', !html.includes('3 d em tela'), 'sem badge compacto');
  const s01Text = await page.locator('[data-patient-name="Paciente Sintético Agendamento Um"]').innerText();
  check('queue-s01-mixed-dispositions', s01Text.includes('Aprovado') && s01Text.includes('Negado'), 'aprovado + negado no mesmo caso');
  check('queue-s01-subtypes', s01Text.includes('angio') && s01Text.includes('cardio'), 'subtipos angio+cardio');
  check('queue-lock-free-agendar', await page.locator('[data-patient-name="Paciente Sintético Agendamento Dois"]').innerText().then((t) => t.includes('Agendar')), 'livre → Agendar');
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
  check('queue-type-filter-select', (await page.locator('select[name="procedure_type"]').count()) === 1, 'filtro server-side único');
  measures.desktop1280 = await measure(page);
  check('measure-desktop-header-aligned', measures.desktop1280.headerContainer && measures.desktop1280.main && Math.abs(measures.desktop1280.headerContainer.left - measures.desktop1280.main.left) <= 2, JSON.stringify(measures.desktop1280.main));
  check('measure-desktop-regions-8-4', measures.desktop1280.regionsSideBySide && measures.desktop1280.leftWidthRatio > 0.55 && measures.desktop1280.leftWidthRatio < 0.8 && measures.desktop1280.rightWidthRatio > 0.2 && measures.desktop1280.rightWidthRatio < 0.45, `left=${measures.desktop1280.leftWidthRatio} right=${measures.desktop1280.rightWidthRatio}`);
  check('measure-desktop-cta-right', measures.desktop1280.cta && measures.desktop1280.right && measures.desktop1280.cta.right <= measures.desktop1280.right.right + 2 && measures.desktop1280.cta.left >= measures.desktop1280.left.right, JSON.stringify(measures.desktop1280.cta));
  check('measure-desktop-cta-target', measures.desktop1280.ctaHeight >= 44, `cta=${measures.desktop1280.ctaHeight}px`);
  check('measure-desktop-no-overflow', !measures.desktop1280.overflowX, `scrollWidth=${measures.desktop1280.scrollWidth} clientWidth=${measures.desktop1280.clientWidth}`);
  check('measure-desktop-single-wait', measures.desktop1280.waitingCountInCard === 1, `waiting=${measures.desktop1280.waitingCountInCard}`);
  check('measure-desktop-no-list-group', measures.desktop1280.listGroupItemInCards === 0, 'sem list-group-item');
  check('measure-desktop-info-alert', measures.desktop1280.alertInfoCount >= 1 && measures.desktop1280.alertWarningCount === 0, `info=${measures.desktop1280.alertInfoCount} warning=${measures.desktop1280.alertWarningCount}`);
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

  // Filtro server-side por procedimento (antes da paginação): submeter o
  // form recarrega com ?procedure_type=; só casos com aquele tipo ficam.
  await page.locator('#procedure-type-filter').selectOption('cat_cardiaco');
  await page.locator('form[action*="/scheduler/"] button:has-text("Filtrar")').click();
  await page.waitForLoadState('domcontentloaded');
  check('server-filter-url', page.url().includes('procedure_type=cat_cardiaco'), page.url());
  const filteredHtml = await page.content();
  check('server-filter-cardio-only', filteredHtml.includes('Paciente Sintético Agendamento Um') && !filteredHtml.includes('Paciente Sintético Agendamento Dois'), 'só VERIFY-S01 no HTML');
  check('server-filter-single-card', (await page.locator('[data-scheduler-queue-card]').count()) === 1, `cards=${await page.locator('[data-scheduler-queue-card]').count()}`);
  const filteredHx = await page.locator('#scheduler-queue-content').getAttribute('hx-get');
  check('server-filter-hx-preserved', typeof filteredHx === 'string' && filteredHx.includes('procedure_type=cat_cardiaco'), String(filteredHx));
  await snap(page, 'scheduler-queue-filtro-server');
  await page.locator('#procedure-type-filter').selectOption('art_perif');
  await page.locator('form[action*="/scheduler/"] button:has-text("Filtrar")').click();
  await page.waitForLoadState('domcontentloaded');
  check('server-filter-paging-preserved', page.url().includes('procedure_type=art_perif') && (await page.locator('.pagination .page-link[href*="procedure_type=art_perif"]').count()) >= 1, page.url());
  // Valor inválido equivale a vazio (volta à fila completa).
  await page.goto(`${base}/scheduler/?tab=aguardando&procedure_type=invalido`, { waitUntil: 'domcontentloaded' });
  check('server-filter-invalid', (await page.locator('[data-scheduler-queue-card]').count()) === 20, `cards=${await page.locator('[data-scheduler-queue-card]').count()}`);
  check('server-filter-invalid-html', (await page.content()).includes('Paciente Sintético Agendamento Dois'), 'fila completa com inválido');

  // Auto-refresh COM FILTRO VÁLIDO + BUSCA: após a prova de inválido,
  // volta a um filtro válido compatível com o novo caso (art_perif).
  // O caso de outro tipo (cat_cardiaco puro) nasce antes, para provar
  // que o fragmento filtrado não o contém (ausente do DOM, não só oculto).
  runVerifyPy2(
    "from django.contrib.auth import get_user_model; " +
      "from apps.cases.models import Case, CaseProcedure; " +
      "from apps.cases.procedures import record_doctor_procedure_decisions; " +
      "u=get_user_model().objects.get(username='verify-nir'); " +
      "doc=get_user_model().objects.get(username='verify-doctor'); " +
      "c=Case.objects.create(created_by=u); " +
      "CaseProcedure.objects.create(case=c, procedure_type='cat_cardiaco', declared_by_nir=True); " +
      "c.start_pdf_extraction(user=None, role='system'); c.complete_pdf_extraction(user=None, role='system'); " +
      "c.complete_anonymization(user=None, role='system'); c.complete_llm_extraction(user=None, role='system'); " +
      "c.complete_llm_summarization(user=None, role='system'); " +
      "record_doctor_procedure_decisions(c, {'cat_cardiaco': ('approved', '')}, user=doc, role='doctor'); " +
      "Case.objects.filter(pk=c.pk).update(agency_record_number='VERIFY-S30', patient_name='Paciente Sintético Agendamento Cardio Excluido', patient_age=50, days_on_screen=9); " +
      "print(c.case_id)",
  );
  await page.goto(`${base}/scheduler/?tab=aguardando&procedure_type=art_perif`, { waitUntil: 'domcontentloaded' });
  check('refresh-filter-valid-url', page.url().includes('procedure_type=art_perif') && !page.url().includes('invalido'), page.url());
  const pollUrl = page.url();
  const pollHx = await page.locator('#scheduler-queue-content').getAttribute('hx-get');
  check('refresh-filter-valid-hx', typeof pollHx === 'string' && pollHx.includes('procedure_type=art_perif'), String(pollHx));
  check('refresh-other-type-absent-prepoll', (await page.locator('[data-patient-name="Paciente Sintético Agendamento Cardio Excluido"]').count()) === 0, 'outro tipo fora do filtro válido');

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
  const pollRes = await pollPromise;
  check('refresh-partial-polled', true, 'partial consultado pelo polling');
  check('refresh-partial-filtered', pollRes.url().includes('procedure_type=art_perif'), pollRes.url());
  check('refresh-filter-valid-url-after', page.url() === pollUrl && page.url().includes('procedure_type=art_perif') && !page.url().includes('invalido'), page.url());
  const afterHx = await page.locator('#scheduler-queue-content').getAttribute('hx-get');
  check('refresh-hx-filter-valid-after', typeof afterHx === 'string' && afterHx.includes('procedure_type=art_perif'), String(afterHx));
  check('refresh-search-term-preserved', (await page.locator('[data-scheduler-queue-search]').inputValue()) === 'VERIFY-S03', `termo=${await page.locator('[data-scheduler-queue-search]').inputValue()}`);
  check('refresh-search-matching-card-visible', await page.locator('[data-patient-name="Paciente Sintético Agendamento Três"]').isVisible(), 'caso compatível visível com busca ativa');
  check('refresh-search-nonmatching-hidden', !(await page.locator('[data-patient-name="Paciente Sintético Agendamento Um"]').isVisible()) && (await visibleCards(page)) === 1, `visíveis=${await visibleCards(page)}`);
  check('refresh-new-card-visible', await page.locator('[data-patient-name="Paciente Sintético Agendamento Três"]').isVisible(), 'caso compatível visível');
  check('refresh-other-type-absent', (await page.locator('[data-patient-name="Paciente Sintético Agendamento Cardio Excluido"]').count()) === 0, 'outro tipo ausente do fragmento');
  check('refresh-out-of-page-absent', (await page.locator('[data-patient-name="Paciente Sintético Fora da Página Agendamento"]').count()) === 0, 'fora da página ausente');
  check('refresh-no-reload', page.url() === pollUrl, page.url());
  await snap(page, 'scheduler-queue-busca-refresh');
  observed.steps.push({ step: 'scheduler-queue-busca-refresh', url: page.url() });
  // Aba Processados preservada fora do ramo pendente.
  await page.goto(`${base}/scheduler/?tab=processados`, { waitUntil: 'domcontentloaded' });
  const procHtml = await page.content();
  check('processed-heading', (await page.getByRole('heading', { name: 'Fila de agendamento' }).count()) >= 1, 'processados mantém título');
  check('processed-received-label', procHtml.includes('Recebido há') && !procHtml.includes('Aguardando há'), 'Recebido há sem Aguardando');
  check('processed-unit', procHtml.includes('Unidade 2') && procHtml.includes('Paciente Sintético Agendamento Processado'), 'unidade exibida');
  check('processed-no-procedure-filter', (await page.locator('select[name="procedure_type"]').count()) === 0, 'sem filtro novo em processados');
  check('processed-status-badge', (await page.locator('.status-badge').count()) >= 1, 'badge de status preservado');
  check('processed-card', (await page.locator('[data-patient-name="Paciente Sintético Agendamento Processado"]').count()) === 1, 'card processado');
  // Evita captura durante o fade-in (.app-shell, 220ms): aguarda o estado
  // visual estável por computed opacity, sem sleep fixo longo.
  await page.waitForFunction(() => {
    const el = document.querySelector('.app-shell');
    if (!el) return true;
    return parseFloat(getComputedStyle(el).opacity) >= 0.99;
  }, { timeout: 5000 }).catch(() => {});
  const shellOpacity = await page.evaluate(() => {
    const el = document.querySelector('.app-shell');
    return el ? getComputedStyle(el).opacity : '1';
  });
  check('processed-shell-stable', parseFloat(shellOpacity) >= 0.99, `opacity=${shellOpacity}`);
  await snap(page, 'scheduler-queue-processados');
  observed.steps.push({ step: 'scheduler-queue-processados', url: page.url() });
  // Mobile 390px: empilhamento, alvo >=44px, sem overflow.
  await page.goto(`${base}/scheduler/?tab=aguardando`, { waitUntil: 'domcontentloaded' });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(500);
  measures.mobile390 = await measure(page);
  check('measure-mobile-stacked', measures.mobile390.regionsStacked, JSON.stringify(measures.mobile390.left));
  check('measure-mobile-cta-target', measures.mobile390.ctaHeight >= 44, `cta=${measures.mobile390.ctaHeight}px`);
  check('measure-mobile-no-overflow', !measures.mobile390.overflowX, `scrollWidth=${measures.mobile390.scrollWidth} clientWidth=${measures.mobile390.clientWidth}`);
  check('measure-mobile-title', measures.mobile390.titleText === 'Solicitações de agendamento aguardando confirmação', measures.mobile390.titleText || 'sem título');
  await snap(page, 'scheduler-queue-mobile');
  buscaJourney.search = true;
  buscaJourney.refresh = true;
  buscaJourney.lock = true;
  await context.close();
  check('no-failed-static-assets', failedAssets.length === 0, failedAssets.join('; '));
} finally {
  await browser.close();
}
writeFileSync(path.join(artifacts, 'fila-agendamento-oli002-measures.json'), JSON.stringify(measures, null, 2));
writeFileSync(path.join(artifacts, 'fila-agendamento-busca-refresh.json'), JSON.stringify(buscaJourney, null, 2));
const report = { ...observed, checks, failures, failedAssets, measures };
writeFileSync(path.join(artifacts, 'report-fila-agendamento-busca-refresh.json'), JSON.stringify(report, null, 2));
if (failures.length > 0) {
  console.error(`FAIL ${failures.length} checks: ${failures.join(', ')}`);
  process.exit(1);
}
console.log(`PASS ${checks.length} checks`);

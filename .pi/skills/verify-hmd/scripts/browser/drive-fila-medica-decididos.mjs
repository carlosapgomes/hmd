// Driver Playwright pinado do verify-hmd — fila médica decididos (OLI-003).
// Login REAL pelo form (sem addCookies): aba decididos com cards orientados
// ao resultado (disposição/horário por procedimento, caso misto sem
// agregação), CTA Ver detalhes para o detalhe read-only, filtro server-side
// sem contagens, vazio em alert-info e medidas 1280/390.
// Evidência em <run>/artifacts/.
// Exit: 0 PASS · 1 falha observada · 2 infra/driver.
import { execSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync, readlinkSync, writeFileSync } from 'node:fs';
import path from 'node:path';

import { chromium } from 'playwright';

const runArg = process.argv.indexOf('--run');
if (runArg < 0 || !process.argv[runArg + 1]) {
  console.error('uso: drive-fila-medica-decididos.mjs --run <tmp/verification/hmd-verify-...>');
  process.exit(2);
}
const runDir = path.resolve(process.argv[runArg + 1]);
const manifest = JSON.parse(readFileSync(path.join(runDir, 'manifest.json'), 'utf8'));
const base = manifest.url;
const artifacts = path.join(runDir, 'artifacts');
const PASSWORD = manifest.seedPassword;
const DECIDED_URL = `${base}/doctor/?tab=decididos`;

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
  '/static/js/doctor_queue_filter.js',
];
const failedAssets = [];
const checks = [];
const failures = [];
function check(id, ok, detail) {
  checks.push({ id, ok: !!ok, detail: String(detail ?? '') });
  if (!ok) failures.push(id);
  console.log(`${ok ? 'PASS' : 'FAIL'} ${id} ${detail ?? ''}`);
}
// Estabilidade visual: `.app-shell` entra com fade-in (220ms) — aguardar
// opacity >= 0.99 antes de qualquer captura, sem sleep fixo (OLI-002).
async function waitForStableShell(page) {
  await page.waitForFunction(
    () => {
      const el = document.querySelector('.app-shell');
      return !!el && parseFloat(getComputedStyle(el).opacity) >= 0.99;
    },
    { timeout: 10000 },
  );
}
async function snap(page, name) {
  await waitForStableShell(page);
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
  page.evaluate(() => [...document.querySelectorAll('[data-doctor-queue-card]')].filter((el) => !el.hidden && el.offsetParent !== null).length);

let browser;
try {
  browser = await chromium.launch({ headless: true });
} catch (err) {
  console.error(`infra BLOCKED: Chromium pinado indisponível (${err.message}).`);
  process.exit(2);
}
const observed = { runId: manifest.runId, base, database: manifest.database, steps: [] };
const decididosJourney = {};
const measures = { desktop1280: null, mobile390: null };
async function measure(page) {
  return page.evaluate(() => {
    const doc = document.documentElement;
    const headerBox = document.querySelector('header.app-header .container')?.getBoundingClientRect();
    const main = document.querySelector('main.app-shell');
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
      ctaText: cta?.textContent?.trim() || null,
      ctaHref: cta?.getAttribute('href') || null,
      waitingCountInCard: card ? (card.textContent.match(/Aguardando há|Recebido há/g) || []).length : null,
      waitingTimeElements: document.querySelectorAll('#doctor-queue-content .waiting-time').length,
      statusBadgeCount: document.querySelectorAll('#doctor-queue-content .status-badge').length,
      listGroupItemInCards: document.querySelectorAll('.case-card .list-group-item').length,
      alertInfoCount: document.querySelectorAll('#doctor-queue-content .alert-info').length,
    };
  });
}
try {
  // Fixtures decididos (setup sintético, antes do login): misto D01
  // (aprovado+negado com horários distintos por row), negado D02, aprovado D03.
  runVerifyPy2(
    "from datetime import timedelta\n" +
    "from django.contrib.auth import get_user_model\n" +
    "from django.utils import timezone\n" +
    "from apps.cases.models import Case, CaseProcedure\n" +
    "from apps.cases.procedures import record_doctor_procedure_decisions\n" +
    "u = get_user_model().objects.get(username='verify-nir')\n" +
    "d = get_user_model().objects.get(username='verify-doctor')\n" +
    "def make(rec, name, types, decisions, days, age):\n" +
    "    c = Case.objects.create(created_by=u)\n" +
    "    for t in types:\n" +
    "        CaseProcedure.objects.create(case=c, procedure_type=t, declared_by_nir=True)\n" +
    "    c.start_pdf_extraction(user=None, role='system')\n" +
    "    c.complete_pdf_extraction(user=None, role='system')\n" +
    "    c.complete_anonymization(user=None, role='system')\n" +
    "    c.complete_llm_extraction(user=None, role='system')\n" +
    "    c.complete_llm_summarization(user=None, role='system')\n" +
    "    Case.objects.filter(pk=c.pk).update(agency_record_number=rec, patient_name=name, patient_age=age, days_on_screen=days)\n" +
    "    record_doctor_procedure_decisions(c, decisions, user=d, role='doctor')\n" +
    "    c.refresh_from_db()\n" +
    "    return c\n" +
    "m = make('VERIFY-D01', 'Paciente Sintético Decidido Misto', ('art_perif', 'cat_cardiaco'), {'art_perif': ('approved', ''), 'cat_cardiaco': ('denied', 'motivo sintetico no detalhe')}, 5, 66)\n" +
    "CaseProcedure.objects.filter(case=m, procedure_type='art_perif').update(doctor_decided_at=timezone.now() - timedelta(days=1, hours=2))\n" +
    "CaseProcedure.objects.filter(case=m, procedure_type='cat_cardiaco').update(doctor_decided_at=timezone.now() - timedelta(hours=5))\n" +
    "make('VERIFY-D02', 'Paciente Sintético Decidido Negado', ('cat_cardiaco',), {'cat_cardiaco': ('denied', '')}, 4, 58)\n" +
    "make('VERIFY-D03', 'Paciente Sintético Decidido Aprovado', ('art_perif',), {'art_perif': ('approved', '')}, 2, 73)\n" +
    "print('decided fixtures ok')",
  );
  const { context, page } = await loginAs(browser, 'verify-doctor', 'doctor-decididos', { width: 1280, height: 720 });
  await page.goto(DECIDED_URL, { waitUntil: 'domcontentloaded' });
  check('decided-heading', (await page.getByRole('heading', { name: 'Casos decididos' }).count()) === 1, 'h1 Casos decididos');
  check('decided-no-today-label', !(await page.content()).includes('Decididos Hoje'), 'sem Decididos Hoje');
  const html = await page.content();
  check('decided-no-col-lg-10', !html.includes('col-lg-10'), 'sem col-lg-10');
  check('decided-operational-scope', html.includes('operational-list-page'), 'escopo operacional');
  check('decided-no-nav-tabs', !html.includes('nav-tabs'), 'sem bloco legado nav-tabs');
  check('decided-pills', html.includes('>Pendentes') && html.includes('>Decididos'), 'pills Pendentes/Decididos');
  check('decided-card-count', (await page.locator('[data-doctor-queue-card]').count()) === 3, `cards=${await page.locator('[data-doctor-queue-card]').count()}`);
  check('decided-no-waiting-time', (await page.locator('#doctor-queue-content .waiting-time').count()) === 0, 'zero waiting-time');
  check('decided-no-status-badge', (await page.locator('#doctor-queue-content .status-badge').count()) === 0, 'zero status-badge');
  check('decided-no-status-done', !html.includes('status-done'), 'sem status-done verde genérico');
  check('decided-no-waiting-labels', !html.includes('Aguardando há') && !html.includes('Recebido há') && !html.includes('Recebido em'), 'sem espera/recebimento');
  check('decided-no-list-group-in-cards', (await page.locator('.case-card .list-group-item').count()) === 0, 'sem list-group-item no card');
  check('decided-card-row-direct', await page.evaluate(() => { const c = document.querySelector('.case-card'); return !!c && c.querySelector(':scope > .row') !== null; }), '.case-card > .row');
  check('decided-card-regions', (await page.locator('.case-card .col-lg-8').count()) === 3 && (await page.locator('.case-card .col-lg-4').count()) === 3, 'regiões 8/4 no DOM');
  // Caso misto: ambas as rows com disposição/horário próprios, sem agregação.
  const mixedCard = page.locator('[data-doctor-queue-card]', { hasText: 'Paciente Sintético Decidido Misto' });
  const mixedText = await mixedCard.innerText();
  check('decided-mixed-identity', mixedText.includes('VERIFY-D01') && mixedText.includes('66 a'), 'identidade/demografia');
  check('decided-mixed-labels-subtypes', mixedText.includes('Arteriografia periférica') && mixedText.includes('Cateterismo cardíaco') && mixedText.includes('angio') && mixedText.includes('cardio'), 'labels/subtipos');
  check('decided-mixed-badges', mixedText.includes('Aprovado') && mixedText.includes('Negado'), 'Aprovado+Negado');
  check('decided-mixed-badge-colors', (await mixedCard.locator('.text-bg-success').count()) === 1 && (await mixedCard.locator('.text-bg-danger').count()) === 1, 'verde/vermelho por row');
  check('decided-mixed-two-times', (mixedText.match(/Decidido em \d{2}\/\d{2}\/\d{4} \d{2}:\d{2}/g) || []).length === 2, 'dois horários distintos');
  check('decided-mixed-no-reason-in-card', !mixedText.includes('motivo sintetico no detalhe'), 'motivo só no detalhe');
  check('decided-mixed-no-awaiting-seed', !mixedText.includes('Paciente Sintético Um'), 'sem caso aguardando');
  const ctaTexts = await page.locator('[data-doctor-queue-card] a.stretched-link').allInnerTexts();
  check('decided-cta-ver-detalhes', ctaTexts.length === 3 && ctaTexts.every((t) => t.trim() === 'Ver detalhes'), JSON.stringify(ctaTexts));
  check('decided-cta-outline', (await page.locator('[data-doctor-queue-card] a.btn-outline-primary.stretched-link').count()) === 3, 'CTA outline stretched-link');
  const firstHref = await page.locator('[data-doctor-queue-card] a.stretched-link').first().getAttribute('href');
  check('decided-cta-detail-url', /^\/doctor\/case\/[0-9a-f-]+\/$/.test(firstHref || '') && !(firstHref || '').includes('/decide/'), String(firstHref));
  // Filtro único sem contagens nesta aba.
  check('decided-single-subtype-select', (await page.locator('select[name="subtype"]').count()) === 1, 'um único seletor server-side');
  const optHtml = await page.locator('select[name="subtype"]').innerHTML();
  check('decided-filter-no-counts', !optHtml.includes('Todas (') && !/\(\d+\)/.test(optHtml), 'opções sem contagens de pendentes');
  check('decided-no-pending-badges-row', !html.includes('Pendentes por subtipo'), 'sem fileira de badges');
  check('decided-search-input', (await page.locator('[data-doctor-queue-search]').count()) === 1, 'busca client-side');
  measures.desktop1280 = await measure(page);
  check('measure-desktop-header-aligned', measures.desktop1280.headerContainer && measures.desktop1280.main && Math.abs(measures.desktop1280.headerContainer.left - measures.desktop1280.main.left) <= 2, JSON.stringify(measures.desktop1280.main));
  check('measure-desktop-regions-8-4', measures.desktop1280.regionsSideBySide && measures.desktop1280.leftWidthRatio > 0.55 && measures.desktop1280.leftWidthRatio < 0.8 && measures.desktop1280.rightWidthRatio > 0.2 && measures.desktop1280.rightWidthRatio < 0.45, `left=${measures.desktop1280.leftWidthRatio} right=${measures.desktop1280.rightWidthRatio}`);
  check('measure-desktop-cta-right', measures.desktop1280.cta && measures.desktop1280.right && measures.desktop1280.cta.right <= measures.desktop1280.right.right + 2 && measures.desktop1280.cta.left >= measures.desktop1280.left.right, JSON.stringify(measures.desktop1280.cta));
  check('measure-desktop-cta-target', measures.desktop1280.ctaHeight >= 44, `cta=${measures.desktop1280.ctaHeight}px`);
  check('measure-desktop-cta-text', measures.desktop1280.ctaText === 'Ver detalhes', measures.desktop1280.ctaText || '');
  check('measure-desktop-no-overflow', !measures.desktop1280.overflowX, `scrollWidth=${measures.desktop1280.scrollWidth} clientWidth=${measures.desktop1280.clientWidth}`);
  check('measure-desktop-zero-waiting', measures.desktop1280.waitingTimeElements === 0 && measures.desktop1280.waitingCountInCard === 0, 'zero espera');
  await snap(page, 'doctor-decididos-inicial');
  observed.steps.push({ step: 'doctor-decididos-inicial', url: page.url() });

  // CTA abre o detalhe read-only (nunca a página de trabalho).
  await page.locator('[data-doctor-queue-card] a.stretched-link').first().click();
  await page.waitForURL(/\/doctor\/case\/[0-9a-f-]+\/$/, { timeout: 15000 });
  check('decided-cta-opens-detail', !page.url().includes('/decide/'), page.url());
  const detailText = await page.locator('body').innerText();
  check('decided-detail-read-only', detailText.includes('Paciente Sintético Decidido Misto') && detailText.includes('Aprovado') && detailText.includes('Negado'), 'detalhe com decisões registradas');
  await snap(page, 'doctor-case-decidido-detalhe');
  observed.steps.push({ step: 'doctor-case-decidido-detalhe', url: page.url() });

  // Busca client-side nos decididos.
  await page.goto(DECIDED_URL, { waitUntil: 'domcontentloaded' });
  const urlBefore = page.url();
  await page.locator('[data-doctor-queue-search]').fill('Decidido Misto');
  check('decided-search-filters', (await visibleCards(page)) === 1, `visíveis=${await visibleCards(page)}`);
  check('decided-search-no-reload', page.url() === urlBefore, page.url());
  await snap(page, 'doctor-decididos-busca');
  await page.locator('[data-doctor-queue-clear]').click();
  check('decided-search-clear-restores', (await visibleCards(page)) === 3, `visíveis=${await visibleCards(page)}`);

  // Filtro server-side: angio mantém o misto e remove o cardio-only do HTML.
  await page.locator('select[name="subtype"]').selectOption('angio');
  await page.getByRole('button', { name: 'Filtrar' }).click();
  await page.waitForURL(/subtype=angio/, { timeout: 15000 });
  const filteredHtml = await page.content();
  check('decided-server-filter-url', page.url().includes('subtype=angio'), page.url());
  check('decided-server-filter-mixed-visible', filteredHtml.includes('Paciente Sintético Decidido Misto'), 'misto (angio) visível');
  check('decided-server-filter-cardio-absent', !filteredHtml.includes('Paciente Sintético Decidido Negado'), 'cardio-only fora do HTML');
  check('decided-server-filter-hx-get-has-subtype', ((await page.locator('#doctor-queue-content').getAttribute('hx-get')) || '').includes('subtype=angio'), 'partial preserva subtype');
  await snap(page, 'doctor-decididos-filtro-server');
  observed.steps.push({ step: 'doctor-decididos-filtro-server', url: page.url() });

  // Vazio em alert-info (subtipo radio sem casos decididos).
  await page.goto(`${base}/doctor/?tab=decididos&subtype=radio`, { waitUntil: 'domcontentloaded' });
  check('decided-empty-card-count', (await page.locator('[data-doctor-queue-card]').count()) === 0, 'zero cards');
  check('decided-empty-info-alert', (await page.locator('#doctor-queue-content .alert-info').count()) === 1 && (await page.locator('#doctor-queue-content').innerText()).includes('Nenhum caso decidido'), 'alert-info único');
  check('decided-empty-no-warning', (await page.locator('#doctor-queue-content .alert-warning').count()) === 0, 'sem alert-warning');
  await snap(page, 'doctor-decididos-vazio');
  observed.steps.push({ step: 'doctor-decididos-vazio', url: page.url() });
  decididosJourney.result = true;
  decididosJourney.readOnlyDetail = true;
  decididosJourney.serverFilter = true;
  decididosJourney.emptyInfo = true;
  await context.close();
  check('no-failed-static-assets', failedAssets.length === 0, failedAssets.join('; '));

  // Mobile 390px: regiões empilham, CTA >=44px, sem overflow.
  const mobile = await loginAs(browser, 'verify-doctor', 'doctor-decididos-mobile', { width: 390, height: 844 });
  await mobile.page.goto(DECIDED_URL, { waitUntil: 'domcontentloaded' });
  measures.mobile390 = await measure(mobile.page);
  check('measure-mobile-stacked', measures.mobile390.regionsStacked, JSON.stringify(measures.mobile390.left));
  check('measure-mobile-cta-target', measures.mobile390.ctaHeight >= 44, `cta=${measures.mobile390.ctaHeight}px`);
  check('measure-mobile-cta-text', measures.mobile390.ctaText === 'Ver detalhes', measures.mobile390.ctaText || '');
  check('measure-mobile-no-overflow', !measures.mobile390.overflowX, `scrollWidth=${measures.mobile390.scrollWidth} clientWidth=${measures.mobile390.clientWidth}`);
  check('measure-mobile-heading', (await mobile.page.getByRole('heading', { name: 'Casos decididos' }).count()) === 1, 'h1 mobile');
  check('measure-mobile-zero-waiting', measures.mobile390.waitingTimeElements === 0, 'zero waiting-time mobile');
  await waitForStableShell(mobile.page);
  const shellOpacity = await mobile.page
    .locator('.app-shell')
    .evaluate((el) => parseFloat(getComputedStyle(el).opacity));
  check('measure-mobile-shell-stable', shellOpacity >= 0.99, `opacity=${shellOpacity}`);
  await snap(mobile.page, 'doctor-decididos-mobile');
  observed.steps.push({ step: 'doctor-decididos-mobile', url: mobile.page.url() });
  await mobile.context.close();
} finally {
  await browser.close();
}
writeFileSync(path.join(artifacts, 'fila-medica-decididos.json'), JSON.stringify(decididosJourney, null, 2));
writeFileSync(path.join(artifacts, 'fila-medica-oli003-measures.json'), JSON.stringify(measures, null, 2));
const report = { ...observed, checks, failures, failedAssets, measures };
writeFileSync(path.join(artifacts, 'report-fila-medica-decididos.json'), JSON.stringify(report, null, 2));
if (failures.length > 0) {
  console.error(`FAIL ${failures.length} checks: ${failures.join(', ')}`);
  process.exit(1);
}
console.log(`PASS ${checks.length} checks`);

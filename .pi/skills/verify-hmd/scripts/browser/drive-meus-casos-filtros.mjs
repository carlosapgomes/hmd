// Driver Playwright pinado do verify-hmd — meus casos filtros+refresh.
// Login REAL pelo form (sem addCookies): "meus casos" do NIR
// (OLI-004): filtros server-side (q/status/tipo, AND), navegação
// única no header com filter_qs, escopo por criador, cabeçalho neutro
// com uma contagem, cards compactos 4/3/3/2, exceções/vazios e partial
// htmx 20 s com reaplicação do live region oculto pós-swap. Medidas
// computadas em 1280px (4 regiões) e 390px (empilhamento/CTA).
// Evidência em <run>/artifacts/.
// Exit: 0 PASS · 1 falha observada · 2 infra/driver.
import { execSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync, readlinkSync, writeFileSync } from 'node:fs';
import path from 'node:path';

import { chromium } from 'playwright';

const runArg = process.argv.indexOf('--run');
if (runArg < 0 || !process.argv[runArg + 1]) {
  console.error('uso: drive-meus-casos-filtros.mjs --run <tmp/verification/hmd-verify-...>');
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
  '/static/js/nir_queue_filter.js',
];
const failedAssets = [];
const checks = [];
const failures = [];
function check(id, ok, detail) {
  checks.push({ id, ok: !!ok, detail: String(detail ?? '') });
  if (!ok) failures.push(id);
  console.log(`${ok ? 'PASS' : 'FAIL'} ${id} ${detail ?? ''}`);
}
// Fade-in do shell (.app-shell, 220ms): nenhuma captura antes de a
// opacidade computada estabilizar — sem sleep fixo (OLI-004).
async function waitShell(page) {
  await page.waitForFunction(
    () => {
      const shell = document.querySelector('.app-shell');
      return shell !== null && Number(getComputedStyle(shell).opacity) >= 0.99;
    },
    { timeout: 15000 },
  );
}
async function snap(page, name) {
  await waitShell(page);
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
  page.evaluate(() => [...document.querySelectorAll('[data-my-cases-card]')].filter((el) => !el.hidden && el.offsetParent !== null).length);

let browser;
try {
  browser = await chromium.launch({ headless: true });
} catch (err) {
  console.error(`infra BLOCKED: Chromium pinado indisponível (${err.message}).`);
  process.exit(2);
}
const observed = { runId: manifest.runId, base, database: manifest.database, steps: [] };
const filtrosJourney = {};
try {
  // Setup (via verify settings): o seed já cria VERIFY-001/VERIFY-002
  // (verify-nir, AWAITING_DOCTOR). Aqui: VERIFY-M01 (próprio, FAILED),
  // VERIFY-M02 (próprio, CLEANED, aba encerrados), VERIFY-M03 (próprio,
  // cat_cardiaco), VERIFY-M04 (próprio, encerramento administrativo real
  // via serviço, aba encerrados), VERIFY-M05 (próprio, retido p/ revisão),
  // VERIFY-X01 (OUTRO NIR, casa com os filtros). O outro NIR ganha a
  // senha sintética do seed para provar o vazio sem filtros pelo form.
  runVerifyPy2(
    "from django.contrib.auth import get_user_model\n" +
    "from apps.accounts.models import Role\n" +
    "from apps.cases.models import Case, CaseProcedure\n" +
    "from apps.cases.closure import administratively_close_case\n" +
    "u = get_user_model().objects.get(username='verify-nir')\n" +
    "manager = get_user_model().objects.get(username='verify-manager')\n" +
    "other, _ = get_user_model().objects.get_or_create(username='verify-nir-outro', defaults={'first_name': 'Verificação', 'last_name': 'Sintético nir', 'is_active': True, 'account_status': 'active'})\n" +
    "role, _ = Role.objects.get_or_create(name='nir')\n" +
    "other.roles.add(role)\n" +
    "other.is_active = True\n" +
    "other.account_status = 'active'\n" +
    "other.set_password('" + PASSWORD + "')\n" +
    "other.save()\n" +
    "empty_user, _ = get_user_model().objects.get_or_create(username='verify-nir-vazio', defaults={'first_name': 'Verificação', 'last_name': 'Sintético vazio', 'is_active': True, 'account_status': 'active'})\n" +
    "empty_user.roles.add(role)\n" +
    "empty_user.is_active = True\n" +
    "empty_user.account_status = 'active'\n" +
    "empty_user.set_password('" + PASSWORD + "')\n" +
    "empty_user.save()\n" +
    "def make(creator, rec, name, types, status):\n" +
    "    c = Case.objects.create(created_by=creator)\n" +
    "    for t in types:\n" +
    "        CaseProcedure.objects.create(case=c, procedure_type=t, declared_by_nir=True)\n" +
    "    Case.objects.filter(pk=c.pk).update(agency_record_number=rec, patient_name=name, patient_age=55, status=status)\n" +
    "    c.refresh_from_db()\n" +
    "    return c\n" +
    "make(u, 'VERIFY-M01', 'Paciente Sintético Meus Casos Falha', ('art_perif',), 'FAILED')\n" +
    "make(u, 'VERIFY-M02', 'Paciente Sintético Meus Casos Encerrado', ('art_perif',), 'CLEANED')\n" +
    "make(u, 'VERIFY-M03', 'Paciente Sintético Meus Casos Cardio', ('cat_cardiaco',), 'AWAITING_DOCTOR')\n" +
    "admin_case = make(u, 'VERIFY-M04', 'Paciente Sintético Meus Casos Admin', ('art_perif',), 'AWAITING_DOCTOR')\n" +
    "administratively_close_case(case=admin_case, user=manager, active_role='manager', reason_code='stuck_lock', reason_text='motivo sintetico de verificacao')\n" +
    "retained = make(u, 'VERIFY-M05', 'Paciente Sintético Meus Casos Retido', ('art_perif',), 'PDF_EXTRACTING')\n" +
    "Case.objects.filter(pk=retained.pk).update(manual_review_required=True, manual_review_reason='documento fora do padrao SESAB (motivo: missing_header)')\n" +
    "make(other, 'VERIFY-X01', 'Paciente Sintético Alheio', ('art_perif',), 'FAILED')\n" +
    "print('fixtures ok')",
  );
  const { context, page } = await loginAs(browser, 'verify-nir', 'nir-meus-casos-filtros', { width: 1280, height: 900 });
  await page.goto(`${base}/intake/cases/`, { waitUntil: 'domcontentloaded' });
  await waitShell(page);
  check('nir-my-cases-url', page.url().includes('/intake/cases/'), page.url());
  check('my-cases-heading', (await page.getByRole('heading', { name: 'Todos os seus encaminhamentos' }).count()) === 1, 'h1 Todos os seus encaminhamentos');
  const html = await page.content();
  check('my-cases-no-top-cta', !html.includes('Enviar relatório'), 'sem CTA Enviar relatório no topo');
  check('my-cases-no-internal-pills', (await page.locator('ul[role="tablist"]').count()) === 0 && !html.includes('>Ativos<'), 'sem pills internas');
  check('my-cases-header-nav', html.includes('?tab=active') && html.includes('?tab=closed') && html.includes('Meus Casos') && html.includes('Casos Encerrados'), 'header Meus Casos/Casos Encerrados');
  check('my-cases-operational-page', html.includes('operational-list-page') && !html.includes('col-12 col-lg-10'), 'container integral operational-list-page');
  check('my-cases-neutral-header', html.includes('Meus Casos') && html.includes('Atualizado automaticamente') && (await page.locator('.alert-warning').count()) === 0, 'cabeçalho neutro sem alert-warning');
  check('my-cases-single-visual-count', (await page.locator('[data-my-cases-status]').evaluate((el) => el.className)).includes('visually-hidden'), 'live region visualmente oculto');
  check('my-cases-case-card-row', (await page.locator('.case-card > .row').count()) >= 2, `case-card > row=${await page.locator('.case-card > .row').count()}`);
  check('my-cases-no-summary-waiting-listgroup', (await page.locator('.summary-box').count()) === 0 && (await page.locator('.waiting-time').count()) === 0 && (await page.locator('.list-group-item').count()) === 0, 'sem summary-box/waiting-time/list-group-item');
  check('my-cases-patient-name', (await page.locator('.patient-name').count()) >= 2, 'patient-name');
  check('my-cases-cta-ver-detalhes', (await page.getByRole('link', { name: 'Ver detalhes' }).count()) >= 2, 'CTA Ver detalhes');
  check('my-cases-status-badge', (await page.locator('.status-badge').count()) >= 2, 'status-badge');
  check('my-cases-exceptions-visible', html.includes('Falha de processamento') && html.includes('Retido para revisão'), 'falha + retenção visíveis');
  const desktopMeasures = await page.evaluate(() => {
    const shell = document.querySelector('.app-shell').getBoundingClientRect();
    const headerBox = document.querySelector('.app-header .container').getBoundingClientRect();
    const listCard = document.querySelector('#my-cases-content .card').getBoundingClientRect();
    const firstCard = document.querySelector('.case-card');
    const regions = [...firstCard.querySelectorAll('.row > div')].map((el) => {
      const r = el.getBoundingClientRect();
      return { cls: el.className, left: r.left, top: r.top, width: r.width, height: r.height };
    });
    const cta = firstCard.querySelector('.col-md-2 .btn');
    const ctaRect = cta.getBoundingClientRect();
    return {
      shell: { left: shell.left, width: shell.width },
      header: { left: headerBox.left, width: headerBox.width },
      listCard: { left: listCard.left, width: listCard.width },
      regions,
      cta: { width: ctaRect.width, height: ctaRect.height },
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      liveRegionText: document.querySelector('[data-my-cases-status]').textContent,
    };
  });
  filtrosJourney.desktopMeasures = desktopMeasures;
  check('my-cases-desktop-container-aligned', Math.abs(desktopMeasures.shell.left - desktopMeasures.header.left) <= 2 && Math.abs(desktopMeasures.shell.width - desktopMeasures.header.width) <= 2, JSON.stringify(desktopMeasures.shell) + ' vs ' + JSON.stringify(desktopMeasures.header));
  check('my-cases-live-region-updated', /Mostrando \d+ casos\./.test(desktopMeasures.liveRegionText), desktopMeasures.liveRegionText);
  const regionTops = desktopMeasures.regions.map((r) => r.top);
  const regionLefts = desktopMeasures.regions.map((r) => r.left);
  check('my-cases-desktop-four-regions-same-row', desktopMeasures.regions.length === 4 && Math.max(...regionTops) - Math.min(...regionTops) <= 6 && regionLefts.every((v, i, a) => i === 0 || v > a[i - 1]), JSON.stringify(desktopMeasures.regions));
  check('my-cases-desktop-cta-target', desktopMeasures.cta.height >= 44, `cta h=${desktopMeasures.cta.height}`);
  check('my-cases-desktop-no-overflow', desktopMeasures.scrollWidth <= desktopMeasures.clientWidth, `${desktopMeasures.scrollWidth} <= ${desktopMeasures.clientWidth}`);
  check('my-cases-sees-VERIFY-001', html.includes('VERIFY-001') && html.includes('Paciente Sintético Um') && html.includes('64 a'), 'seed VERIFY-001');
  check('my-cases-no-foreign', !html.includes('VERIFY-X01') && !html.includes('Paciente Sintético Alheio'), 'caso alheio ausente');
  check('my-cases-filter-form', (await page.locator('input[name="q"]').count()) === 1 && (await page.locator('select[name="status"]').count()) === 1 && (await page.locator('select[name="procedure_type"]').count()) === 1, 'form q/status/procedure_type');
  check('my-cases-filter-catalog', (await page.locator('#my-cases-procedure-type-select option').count()) === 14, `options=${await page.locator('#my-cases-procedure-type-select option').count()} (13 tipos + Todos)`);
  const hxGet = await page.locator('#my-cases-content').getAttribute('hx-get');
  check('my-cases-htmx-partial', typeof hxGet === 'string' && hxGet.includes('cases/partials/list/'), String(hxGet));
  check('my-cases-htmx-20s', (await page.locator('#my-cases-content').getAttribute('hx-trigger')) === 'every 20s', 'every 20s');
  check('my-cases-visible-initial', (await visibleCards(page)) >= 2, `visíveis=${await visibleCards(page)}`);
  await snap(page, 'nir-meus-casos-inicial');
  observed.steps.push({ step: 'nir-meus-casos-inicial', url: page.url() });

  // Filtro server por q (≥3 chars): round-trip com reload (navegação GET).
  await page.locator('#my-cases-search-input').fill('VERIFY-001');
  await page.getByRole('button', { name: 'Filtrar' }).click();
  await page.waitForURL(/q=VERIFY-001/, { timeout: 15000 });
  check('filter-q-narrows', (await visibleCards(page)) === 1 && (await page.locator('body').innerText()).includes('VERIFY-001'), `visíveis=${await visibleCards(page)}`);
  check('filter-q-preserves-in-partial-url', (await page.locator('#my-cases-content').getAttribute('hx-get')).includes('q=VERIFY-001'), 'q na URL do partial');

  // q curto (<3 chars) é ignorado: a lista volta cheia (nunca zero por erro).
  await page.goto(`${base}/intake/cases/?q=VE`, { waitUntil: 'domcontentloaded' });
  check('filter-q-short-ignored', (await visibleCards(page)) >= 2, `visíveis=${await visibleCards(page)}`);

  // Filtro por status (FAILED): só o caso falho.
  await page.goto(`${base}/intake/cases/?status=FAILED`, { waitUntil: 'domcontentloaded' });
  check('filter-status-failed', (await visibleCards(page)) === 1 && (await page.locator('body').innerText()).includes('VERIFY-M01'), `visíveis=${await visibleCards(page)}`);
  await snap(page, 'nir-meus-casos-status');

  // Filtro por tipo (cat_cardiaco): só VERIFY-M03 (e o seed VERIFY-001/002
  // também têm cat_cardiaco — a busca server casa os declarados).
  await page.goto(`${base}/intake/cases/?procedure_type=cat_cardiaco`, { waitUntil: 'domcontentloaded' });
  check('filter-type-cardio', (await page.locator('body').innerText()).includes('VERIFY-M03'), 'VERIFY-M03 visível');
  check('filter-type-excludes-perif-only', !(await page.locator('body').innerText()).includes('VERIFY-M01'), 'VERIFY-M01 (só periférica) ausente');

  // AND: q + status + tipo.
  await page.goto(`${base}/intake/cases/?q=VERIFY-M01&status=FAILED&procedure_type=art_perif`, { waitUntil: 'domcontentloaded' });
  check('filter-and', (await visibleCards(page)) === 1 && (await page.locator('body').innerText()).includes('VERIFY-M01'), `visíveis=${await visibleCards(page)}`);

  // Escopo por criador: q do caso alheio → vazio (página e partial).
  await page.goto(`${base}/intake/cases/?q=VERIFY-X01`, { waitUntil: 'domcontentloaded' });
  check('filter-cross-creator-page-empty', (await visibleCards(page)) === 0 && !(await page.locator('body').innerText()).includes('Paciente Sintético Alheio'), 'alheio nunca aparece');
  const partialHtml = await page.evaluate(async () => {
    const res = await fetch('/intake/cases/partials/list/?q=VERIFY-X01', { credentials: 'same-origin' });
    return await res.text();
  });
  check('filter-cross-creator-partial-empty', !partialHtml.includes('VERIFY-X01'), 'partial também vazio p/ alheio');

  // Aba encerrados: CLEANED aparece, ativos não.
  await page.goto(`${base}/intake/cases/?tab=closed`, { waitUntil: 'domcontentloaded' });
  await waitShell(page);
  check('closed-tab', (await page.locator('body').innerText()).includes('VERIFY-M02') && !(await page.locator('body').innerText()).includes('VERIFY-001'), 'aba encerrados');
  check('closed-admin-closure', (await page.locator('body').innerText()).includes('VERIFY-M04') && (await page.locator('body').innerText()).includes('Encerrado administrativamente') && (await page.locator('body').innerText()).includes('motivo sintetico de verificacao') && (await page.locator('.summary-box').count()) === 0, 'encerramento administrativo com motivo, sem summary-box');
  await snap(page, 'nir-meus-casos-encerrados');
  observed.steps.push({ step: 'nir-meus-casos-encerrados', url: page.url() });

  // Ida e volta pelas abas do header preservando filtros (q + tipo).
  await page.goto(`${base}/intake/cases/?q=VERIFY-M&procedure_type=art_perif`, { waitUntil: 'domcontentloaded' });
  await waitShell(page);
  check('header-tabs-start-active', (await page.locator('body').innerText()).includes('VERIFY-M01'), 'ativos com filtros');
  await page.getByRole('link', { name: /Casos Encerrados/ }).click();
  await page.waitForURL(/tab=closed.*q=VERIFY-M.*procedure_type=art_perif|tab=closed/, { timeout: 15000 });
  await waitShell(page);
  check('header-tabs-to-closed-preserves-filters', page.url().includes('tab=closed') && page.url().includes('q=VERIFY-M') && page.url().includes('procedure_type=art_perif') && (await page.locator('body').innerText()).includes('VERIFY-M02'), page.url());
  await page.getByRole('link', { name: /Meus Casos/ }).click();
  await page.waitForURL(/tab=active/, { timeout: 15000 });
  await waitShell(page);
  check('header-tabs-back-to-active-preserves-filters', page.url().includes('tab=active') && page.url().includes('q=VERIFY-M') && page.url().includes('procedure_type=art_perif') && (await page.locator('body').innerText()).includes('VERIFY-M01'), page.url());

  // Vazio com filtros: Limpar filtros.
  await page.goto(`${base}/intake/cases/?q=VERIFY-ZZZ`, { waitUntil: 'domcontentloaded' });
  await waitShell(page);
  check('empty-with-filters', (await page.locator('body').innerText()).includes('Nenhum caso encontrado para os filtros aplicados.') && (await page.getByRole('link', { name: 'Limpar filtros' }).count()) === 1, 'vazio com filtros + Limpar filtros');
  await snap(page, 'nir-meus-casos-vazio-filtros');
  observed.steps.push({ step: 'nir-meus-casos-vazio-filtros', url: page.url() });

  // Auto-refresh com filtros ATIVOS (review-1 P1): o caso compatível
  // deve aparecer com os filtros preservados e as asserções só rodam
  // após o swap htmx (htmx:afterSwap), nunca só após a resposta HTTP.
  // O listener é instalado antes da navegação; a linha de base é
  // capturada após cada goto; após a resposta HTTP o driver AINDA
  // aguarda o afterSwap (waitForFunction) antes de asserir o DOM.
  await page.addInitScript(() => {
    globalThis.__myCasesSwapCount = 0;
    document.addEventListener('htmx:afterSwap', (event) => {
      if (event.target && event.target.id === 'my-cases-content') {
        globalThis.__myCasesSwapCount = (globalThis.__myCasesSwapCount || 0) + 1;
      }
    });
  });

  // Auto-refresh compatível COM filtros ativos (status FAILED +
  // tipo art_perif, AND): cria o caso novo compatível (setup via verify
  // settings) e aguarda o poll de 20 s + o afterSwap — aparece sem
  // reload, com a aba e os filtros preservados.
  await page.goto(`${base}/intake/cases/?tab=active&status=FAILED&procedure_type=art_perif`, { waitUntil: 'domcontentloaded' });
  check('refresh-filters-active', page.url().includes('status=FAILED') && page.url().includes('procedure_type=art_perif') && (await page.locator('#my-cases-status-select').inputValue()) === 'FAILED' && (await page.locator('#my-cases-procedure-type-select').inputValue()) === 'art_perif', 'filtros FAILED+art_perif ativos no refresh');
  const swapsBeforeRefresh = await page.evaluate(() => globalThis.__myCasesSwapCount || 0);
  const urlBefore = page.url();
  runVerifyPy2(
    "from django.contrib.auth import get_user_model; " +
      "from apps.cases.models import Case, CaseProcedure; " +
      "u=get_user_model().objects.get(username='verify-nir'); " +
      "c=Case.objects.create(created_by=u); " +
      "CaseProcedure.objects.create(case=c, procedure_type='art_perif', declared_by_nir=True); " +
      "Case.objects.filter(pk=c.pk).update(agency_record_number='VERIFY-M10', patient_name='Paciente Sintético Meus Casos Dez', patient_age=50, status='FAILED'); " +
      "print(c.case_id)",
  );
  const pollPromise = page.waitForResponse((res) => res.url().includes('/cases/partials/list/'), { timeout: 40000 });
  const pollResponse = await pollPromise;
  // A resposta HTTP sozinha não prova o swap: aguarda o afterSwap do
  // #my-cases-content antes de qualquer asserção sobre o DOM.
  await page.waitForFunction((count) => (globalThis.__myCasesSwapCount || 0) > count, swapsBeforeRefresh, { timeout: 40000 });
  check('refresh-partial-polled', true, 'partial consultado pelo polling');
  check('refresh-poll-url-keeps-filters', pollResponse.url().includes('status=FAILED') && pollResponse.url().includes('procedure_type=art_perif'), pollResponse.url());
  check('refresh-swap-landed', (await page.evaluate(() => globalThis.__myCasesSwapCount || 0)) > swapsBeforeRefresh, 'htmx:afterSwap do #my-cases-content antes das asserções');
  check('refresh-new-card-visible', await page.locator('body').innerText().then((t) => t.includes('VERIFY-M10') && t.includes('Paciente Sintético Meus Casos Dez')), 'caso compatível visível');
  check('refresh-filters-still-applied', (await page.locator('#my-cases-status-select').inputValue()) === 'FAILED' && (await page.locator('#my-cases-procedure-type-select').inputValue()) === 'art_perif', 'filtros FAILED+art_perif após o swap');
  check('refresh-no-reload', page.url() === urlBefore, page.url());
  check('refresh-foreign-still-absent', !(await page.locator('body').innerText()).includes('VERIFY-X01'), 'alheio segue ausente');
  await snap(page, 'nir-meus-casos-refresh');
  observed.steps.push({ step: 'nir-meus-casos-refresh', url: page.url() });

  // Caso fora do filtro não aparece no refresh: filtra FAILED, cria caso
  // NEW (fora do filtro) e aguarda o poll — ele nunca entra no fragmento.
  await page.goto(`${base}/intake/cases/?tab=active&status=FAILED&procedure_type=art_perif`, { waitUntil: 'domcontentloaded' });
  const swapsBeforeOffFilter = await page.evaluate(() => globalThis.__myCasesSwapCount || 0);
  runVerifyPy2(
    "from django.contrib.auth import get_user_model; " +
      "from apps.cases.models import Case, CaseProcedure; " +
      "u=get_user_model().objects.get(username='verify-nir'); " +
      "c=Case.objects.create(created_by=u); " +
      "CaseProcedure.objects.create(case=c, procedure_type='art_perif', declared_by_nir=True); " +
      "Case.objects.filter(pk=c.pk).update(agency_record_number='VERIFY-M11', patient_name='Paciente Sintético Fora do Filtro', patient_age=50, status='NEW'); " +
      "print(c.case_id)",
  );
  const pollPromise2 = page.waitForResponse((res) => res.url().includes('/cases/partials/list/'), { timeout: 40000 });
  const pollResponse2 = await pollPromise2;
  await page.waitForFunction((count) => (globalThis.__myCasesSwapCount || 0) > count, swapsBeforeOffFilter, { timeout: 40000 });
  check('refresh-off-filter-poll-url-keeps-filters', pollResponse2.url().includes('status=FAILED') && pollResponse2.url().includes('procedure_type=art_perif'), pollResponse2.url());
  check('refresh-off-filter-swap-landed', (await page.evaluate(() => globalThis.__myCasesSwapCount || 0)) > swapsBeforeOffFilter, 'htmx:afterSwap do #my-cases-content antes das asserções');
  check('refresh-off-filter-absent', !(await page.locator('body').innerText()).includes('VERIFY-M11'), 'fora do filtro ausente no refresh');
  check('refresh-off-filter-still-filtered', (await page.locator('body').innerText()).includes('VERIFY-M01'), 'filtro FAILED preservado');
  check('refresh-off-filter-compatible-still-visible', (await page.locator('body').innerText()).includes('VERIFY-M10'), 'compatível segue visível');
  check('refresh-off-filter-filters-applied', (await page.locator('#my-cases-status-select').inputValue()) === 'FAILED' && (await page.locator('#my-cases-procedure-type-select').inputValue()) === 'art_perif', 'filtros FAILED+art_perif após o swap');
  await snap(page, 'nir-meus-casos-fora-do-filtro');
  observed.steps.push({ step: 'nir-meus-casos-fora-do-filtro', url: page.url() });
  // Vazio sem filtros (NIR sem nenhum caso): orienta a aba + Novo.
  const emptyLogin = await loginAs(browser, 'verify-nir-vazio', 'nir-meus-casos-vazio', { width: 1280, height: 900 });
  const emptyPage = emptyLogin.page;
  await emptyPage.goto(`${base}/intake/cases/`, { waitUntil: 'domcontentloaded' });
  await waitShell(emptyPage);
  check('empty-without-filters-active', (await emptyPage.locator('body').innerText()).includes('Nenhum caso ativo no momento.') && (await emptyPage.locator('#my-cases-content').getByRole('link', { name: 'Novo Encaminhamento' }).count()) === 1, 'vazio ativo + Novo Encaminhamento no fragmento');
  await snap(emptyPage, 'nir-meus-casos-vazio-ativo');
  observed.steps.push({ step: 'nir-meus-casos-vazio-ativo', url: emptyPage.url() });
  await emptyPage.goto(`${base}/intake/cases/?tab=closed`, { waitUntil: 'domcontentloaded' });
  await waitShell(emptyPage);
  check('empty-without-filters-closed', (await emptyPage.locator('body').innerText()).includes('Nenhum caso encerrado até agora.') && (await emptyPage.locator('#my-cases-content').getByRole('link', { name: 'Novo Encaminhamento' }).count()) === 1, 'vazio encerrados + Novo Encaminhamento no fragmento');
  await snap(emptyPage, 'nir-meus-casos-vazio-encerrados');
  observed.steps.push({ step: 'nir-meus-casos-vazio-encerrados', url: emptyPage.url() });
  await emptyLogin.context.close();

  // Mobile 390px: regiões empilham, CTA full-width >=44px, sem overflow.
  const mobileLogin = await loginAs(browser, 'verify-nir', 'nir-meus-casos-mobile', { width: 390, height: 844 });
  const mobilePage = mobileLogin.page;
  await mobilePage.goto(`${base}/intake/cases/`, { waitUntil: 'domcontentloaded' });
  await waitShell(mobilePage);
  const mobileMeasures = await mobilePage.evaluate(async () => {
    const firstCard = document.querySelector('.case-card');
    const regions = [...firstCard.querySelectorAll('.row > div')].map((el) => {
      const r = el.getBoundingClientRect();
      return { cls: el.className, left: r.left, top: r.top, width: r.width, height: r.height };
    });
    const cta = firstCard.querySelector('.col-md-2 .btn');
    const ctaRect = cta.getBoundingClientRect();
    const ctaColumn = firstCard.querySelector('.col-md-2').getBoundingClientRect();
    const badges = [...firstCard.querySelectorAll('.badge')].map((el) => ({ scrollWidth: el.scrollWidth, clientWidth: el.clientWidth }));
    const firstRect = () => firstCard.getBoundingClientRect().height;
    const heightBefore = firstRect();
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const heightAfter = firstRect();
    return {
      regions,
      cta: { width: ctaRect.width, height: ctaRect.height, columnWidth: ctaColumn.width },
      badges,
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      stableHeight: heightBefore === heightAfter,
    };
  });
  filtrosJourney.mobileMeasures = mobileMeasures;
  const mobileTops = mobileMeasures.regions.map((r) => r.top);
  check('mobile-regions-stack', mobileMeasures.regions.length === 4 && mobileTops.every((v, i, a) => i === 0 || v >= a[i - 1] - 1), JSON.stringify(mobileMeasures.regions));
  // CTA full-width = largura de conteúdo da coluna (border-box menos
  // o gutter de 12px de cada lado do grid).
  check('mobile-cta-full-width-target', Math.abs(mobileMeasures.cta.width - (mobileMeasures.cta.columnWidth - 24)) <= 2 && mobileMeasures.cta.height >= 44, JSON.stringify(mobileMeasures.cta));
  check('mobile-badges-wrap-no-overflow', mobileMeasures.scrollWidth <= mobileMeasures.clientWidth && mobileMeasures.badges.every((b) => b.scrollWidth <= b.clientWidth + 2), `${mobileMeasures.scrollWidth} <= ${mobileMeasures.clientWidth}`);
  check('mobile-layout-stable', mobileMeasures.stableHeight === true, 'altura estável após opacity >=0.99 (duplo rAF)');
  await snap(mobilePage, 'nir-meus-casos-mobile');
  observed.steps.push({ step: 'nir-meus-casos-mobile', url: mobilePage.url() });
  await mobileLogin.context.close();
  filtrosJourney.filters = true;
  filtrosJourney.refresh = true;
  filtrosJourney.creatorScope = true;
  await context.close();
  check('no-failed-static-assets', failedAssets.length === 0, failedAssets.join('; '));
} finally {
  await browser.close();
}
writeFileSync(path.join(artifacts, 'meus-casos-filtros.json'), JSON.stringify(filtrosJourney, null, 2));
const report = { ...observed, checks, failures, failedAssets };
writeFileSync(path.join(artifacts, 'report-meus-casos-filtros.json'), JSON.stringify(report, null, 2));
if (failures.length > 0) {
  console.error(`FAIL ${failures.length} checks: ${failures.join(', ')}`);
  process.exit(1);
}
console.log(`PASS ${checks.length} checks`);

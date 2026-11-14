// Driver Playwright pinado do verify-hmd — meus casos filtros+refresh.
// Login REAL pelo form (sem addCookies): "meus casos" do NIR
// (ui-parity-queues slice 003): filtros server-side (q/status/tipo,
// AND), pills Ativos/Encerrados com contadores, escopo por criador e
// partial htmx 20 s com reaplicação do contador pós-swap.
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
  // cat_cardiaco) e VERIFY-X01 (OUTRO NIR, casa com os filtros).
  runVerifyPy2(
    "from django.contrib.auth import get_user_model\n" +
    "from apps.accounts.models import Role\n" +
    "from apps.cases.models import Case, CaseProcedure\n" +
    "u = get_user_model().objects.get(username='verify-nir')\n" +
    "other, _ = get_user_model().objects.get_or_create(username='verify-nir-outro', defaults={'first_name': 'Verificação', 'last_name': 'Sintético nir', 'is_active': True, 'account_status': 'active'})\n" +
    "role, _ = Role.objects.get_or_create(name='nir')\n" +
    "other.roles.add(role)\n" +
    "def make(creator, rec, name, types, status):\n" +
    "    c = Case.objects.create(created_by=creator)\n" +
    "    for t in types:\n" +
    "        CaseProcedure.objects.create(case=c, procedure_type=t, declared_by_nir=True)\n" +
    "    Case.objects.filter(pk=c.pk).update(agency_record_number=rec, patient_name=name, patient_age=55, status=status)\n" +
    "    return c\n" +
    "make(u, 'VERIFY-M01', 'Paciente Sintético Meus Casos Falha', ('art_perif',), 'FAILED')\n" +
    "make(u, 'VERIFY-M02', 'Paciente Sintético Meus Casos Encerrado', ('art_perif',), 'CLEANED')\n" +
    "make(u, 'VERIFY-M03', 'Paciente Sintético Meus Casos Cardio', ('cat_cardiaco',), 'AWAITING_DOCTOR')\n" +
    "make(other, 'VERIFY-X01', 'Paciente Sintético Alheio', ('art_perif',), 'FAILED')\n" +
    "print('fixtures ok')",
  );
  const { context, page } = await loginAs(browser, 'verify-nir', 'nir-meus-casos-filtros');
  await page.goto(`${base}/intake/cases/`, { waitUntil: 'domcontentloaded' });
  check('nir-my-cases-url', page.url().includes('/intake/cases/'), page.url());
  check('my-cases-heading', (await page.getByRole('heading', { name: 'Meus casos' }).count()) === 1, 'h1 Meus casos');
  const html = await page.content();
  check('my-cases-pills', (await page.locator('ul[role="tablist"] a[href*="tab=active"]').innerText()).includes('Ativos') && (await page.locator('ul[role="tablist"] a[href*="tab=closed"]').innerText()).includes('Encerrados'), 'pills Ativos/Encerrados');
  check('my-cases-pills-counters', html.includes('?tab=active') && html.includes('?tab=closed'), 'pills com contadores e tab');
  check('my-cases-case-card', (await page.locator('.case-card').count()) >= 2, `case-card=${await page.locator('.case-card').count()}`);
  check('my-cases-summary-box', (await page.locator('.summary-box').count()) >= 2, 'summary-box');
  check('my-cases-waiting-time', (await page.locator('.waiting-time').count()) >= 2, 'waiting-time');
  check('my-cases-status-badge', (await page.locator('.status-badge').count()) >= 2, 'status-badge');
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
  check('closed-tab', (await page.locator('body').innerText()).includes('VERIFY-M02') && !(await page.locator('body').innerText()).includes('VERIFY-001'), 'aba encerrados');

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

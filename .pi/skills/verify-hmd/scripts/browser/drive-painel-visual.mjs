// Drive Playwright pinado do verify-hmd — ui-parity-intake-detail slice 004 + PVP-005.
// Jornada painel-visual: painel gerencial no padrão ats-web (toolbar de
// período, cards de métrica e lista em case-card) com os mesmos números e
// filtros do HMD. Login real pelo form como verify-manager (e verify-admin),
// leitura da toolbar/cards/lista, troca de período pela toolbar e filtro de
// busca server-side. PARTE NOVA PVP-005 (seção 6, APÓS a seção admin e os
// asserts de exatamente 2 casos): fixture VERIFY-101 criada NO DRIVE via
// runVerifyPy (2 PDFs válidos PyMuPDF + extracted_text + AWAITING_DOCTOR +
// criador verify-nir; mídia no mediaRoot da run validado) e detalhe do
// painel de VERIFY-101 com os 2 cards em 1280/390 (lazy por URL EXATA de
// serve desde ANTES do goto como GATE; mobile 390 = 0 fetch + viewer JÁ no
// 390 com Voltar topo/rodapé e ?next= inseguro → fallback); PNG/snapshot/JSON.
// Sem mutação do seed compartilhado. Evidência em <run>/artifacts/.
// Exit: 0 PASS · 1 falha observada · 2 infra/driver.
import { execSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync, readlinkSync, writeFileSync } from 'node:fs';
import { realpathSync } from 'node:fs';
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

const MEDIA_ROOT = manifest.mediaRoot;
if (!MEDIA_ROOT) {
  console.error('infra: mediaRoot ausente no manifest — recuso dirigir.');
  process.exit(2);
}
// Guarda de mídia por run (espelho de drive-detalhe-papeis.mjs, slice
// PVP-002): descendente ESTRITO do diretório da run pelo caminho real.
function isMediaUnderRun(candidate, runDir) {
  if (!candidate || !runDir) return false;
  let runReal;
  try {
    runReal = realpathSync(runDir);
  } catch {
    return false;
  }
  let candidateReal;
  try {
    candidateReal = realpathSync(candidate);
  } catch {
    return false;
  }
  return candidateReal !== runReal && candidateReal.startsWith(runReal + path.sep);
}
if (!isMediaUnderRun(MEDIA_ROOT, runDir)) {
  console.error(`infra: mediaRoot inválido ou fora da run: ${MEDIA_ROOT} — recuso dirigir.`);
  process.exit(2);
}

// runVerifyPy com HMD_VERIFY_MEDIA_ROOT=mediaRoot VALIDADO sob a run
// (padrão PVP-002): o subprocesso grava no mesmo root do servidor; path
// fora da run é recusado antes de qualquer execução.
const runVerifyPy = (code) => {
  if (!isMediaUnderRun(MEDIA_ROOT, runDir)) {
    throw new Error(`infra: mediaRoot fora da run, recuso executar: ${MEDIA_ROOT}`);
  }
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
      HMD_VERIFY_MEDIA_ROOT: MEDIA_ROOT,
    },
    stdio: ['ignore', 'pipe', 'pipe'],
    encoding: 'utf8',
  });
};

// Contagem de requisições na URL de serve por caminho EXATO (padrão
// PVP-002/PVP-004): o server.log do runserver registra cada GET com
// `"GET <path> HTTP/1.1" 200`. Verificações diretas usam `?probe=1` e são
// EXCLUÍDAS da contagem. `markPhase()` retorna a posição da próxima linha
// nova do server.log; falhas de leitura do log lançam (fail-loud).
function readServerLogLines(logFile) {
  return readFileSync(logFile, 'utf8').split('\n');
}
function serverLogPath() {
  return path.join(artifacts, 'server.log');
}
function markPhase() {
  return readServerLogLines(serverLogPath()).length - 1;
}
function countEmbedFetchesFromFile(logFile, since, servePath) {
  let count = 0;
  for (const line of readServerLogLines(logFile).slice(since)) {
    if (line.includes(`GET ${servePath}`) && line.includes(' 200') && !line.includes('probe=1')) {
      count += 1;
    }
  }
  return count;
}
function countEmbedFetches(since, servePath) {
  return countEmbedFetchesFromFile(serverLogPath(), since, servePath);
}
// Verificação direta (NÃO é prova do embed — usa ?probe=1, excluído da
// contagem): confirma que a URL de serve responde 200 application/pdf.
async function probeServesPdf(page, servePath) {
  const response = await page.request.get(`${base}${servePath}?probe=1`);
  const contentType = String(response.headers()['content-type'] || '');
  const ok = response.status() === 200 && contentType.includes('application/pdf');
  check('panel105-probe-serve-pdf', ok, `${response.status()} ${contentType} ${servePath}?probe=1`);
  return ok;
}
// O plugin de PDF do Chromium headless shell NÃO busca nem renderiza
// <embed type="application/pdf"> (prova P1-2 do PVP-002): a prova de rede do
// embed exige o Chromium headed — sob Xvfb quando houver DISPLAY, senão
// BLOCKED honesto (exit 2).
let pdfPluginMode = 'headless-shell (sem prova de rede do embed)';

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
  if (!process.env.DISPLAY) {
    console.error('infra BLOCKED: sem DISPLAY — o plugin de PDF do headless não busca/renderiza <embed>; rode sob Xvfb (DISPLAY=:99) para prova de rede/render do embed. Nenhum motor substituto foi usado.');
    process.exit(2);
  }
  browser = await chromium.launch({ headless: false, args: ['--no-sandbox'] });
  pdfPluginMode = `headed-xvfb (DISPLAY=${process.env.DISPLAY})`;
} catch (err) {
  console.error(`infra BLOCKED: Chromium pinado do Playwright indisponível (${err.message}) — nenhum motor substituto foi usado.`);
  process.exit(2);
}

const observed = { runId: manifest.runId, base, database: manifest.database, pdfPluginMode, steps: [] };

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

  // ══ 6) PARTE NOVA PVP-005 — detalhe do painel de VERIFY-101 (manager) ══
  // ORDEM OBRIGATÓRIA (§8): TODOS os passos/asserts existentes acima (com os
  // 2 casos do seed, incl. reset com exatamente 2 case-cards) rodam ANTES;
  // SÓ DEPOIS a fixture VERIFY-101 é criada via runVerifyPy. VERIFY-101:
  // criador verify-nir, AWAITING_DOCTOR, 2 documentos com PDFs VÁLIDOS
  // (PyMuPDF, 1 página com texto "VERIFY-101 doc N") gravados no storage da
  // run + extracted_text populado. Seed compartilhado intocado; nenhum
  // assert de contagem de exatamente 2 casos é repetido após a criação.
  runVerifyPy(
    "from django.conf import settings\n" +
    "assert str(settings.MEDIA_ROOT).startswith('" + runDir + "'), settings.MEDIA_ROOT\n" +
    "import pymupdf\n" +
    "from django.contrib.auth import get_user_model\n" +
    "from apps.cases.models import Case, CaseDocument, CaseProcedure\n" +
    "from django.core.files.base import ContentFile\n" +
    "u = get_user_model().objects.get(username='verify-nir')\n" +
    "c, _ = Case.objects.get_or_create(created_by=u, agency_record_number='VERIFY-101', defaults={'patient_name': 'Paciente Sintetico Cento e Um', 'patient_age': 57})\n" +
    "Case.objects.filter(pk=c.pk).update(patient_name='Paciente Sintetico Cento e Um', patient_age=57, extracted_text='TEXTO SINTETICO VERIFY-101 PARTE 1.\\nTEXTO SINTETICO VERIFY-101 PARTE 2.')\n" +
    "c.refresh_from_db()\n" +
    "CaseProcedure.objects.get_or_create(case=c, procedure_type='art_perif', defaults={'declared_by_nir': True})\n" +
    "CaseProcedure.objects.get_or_create(case=c, procedure_type='cat_cardiaco', defaults={'declared_by_nir': True})\n" +
    "c.start_pdf_extraction(user=None, role='system')\n" +
    "c.complete_pdf_extraction(user=None, role='system')\n" +
    "c.complete_anonymization(user=None, role='system')\n" +
    "c.complete_llm_extraction(user=None, role='system')\n" +
    "c.complete_llm_summarization(user=None, role='system')\n" +
    "Case.objects.filter(pk=c.pk).update(status='AWAITING_DOCTOR')\n" +
    "c.refresh_from_db()\n" +
    "existing = {d.position: d for d in c.documents.all()}\n" +
    "for pos, name in ((1, 'relatorio-verify101-parte1.pdf'), (2, 'relatorio-verify101-parte2.pdf')):\n" +
    "    if pos in existing:\n" +
    "        continue\n" +
    "    doc = pymupdf.open()\n" +
    "    pg = doc.new_page()\n" +
    "    pg.insert_text((72, 720), 'VERIFY-101 doc %d' % pos, fontsize=24)\n" +
    "    pdf = bytes(doc.tobytes())\n" +
    "    doc.close()\n" +
    "    d = CaseDocument(case=c, position=pos, original_filename=name, content_type='application/pdf', size_bytes=len(pdf), uploaded_by=u)\n" +
    "    d.file.save(name, ContentFile(pdf), save=False)\n" +
    "    d.save()\n" +
    "print('VERIFY-101 ok', str(c.case_id), c.documents.count())",
  );
  const out101 = runVerifyPy(
    "from apps.cases.models import Case\n" +
    "c = Case.objects.get(agency_record_number='VERIFY-101')\n" +
    "print(str(c.case_id))\n" +
    "print(str(c.status))",
  );
  const out101Lines = String(out101).trim().split('\n');
  const caseId101 = out101Lines[out101Lines.length - 2].trim();
  const status101 = out101Lines[out101Lines.length - 1].trim();
  // Rota própria do painel (PVP-005, R1/D1): serve por position.
  const doc1PanelServe = `/dashboard/case/${caseId101}/pdf/1/`;
  const doc2PanelServe = `/dashboard/case/${caseId101}/pdf/2/`;
  check('panel105-verify101-fixture', /^[0-9a-f-]{36}$/.test(caseId101) && status101 === 'AWAITING_DOCTOR', `caseId=${caseId101} status=${status101}`);
  // R5: os PDFs da fixture estão no root da run (mediaRoot do manifest).
  const fixture101Files = execSync(`find ${JSON.stringify(MEDIA_ROOT)} -type f -name '*.pdf' | sort`, { encoding: 'utf8' }).trim().split('\n').filter(Boolean);
  check('panel105-verify101-media-in-run-root', fixture101Files.length >= 2 && fixture101Files.every((f) => isMediaUnderRun(f, runDir)), `${fixture101Files.length} pdf(s) sob ${MEDIA_ROOT}`);
  const pdf101Magic = execSync(
    `python3 -c "import sys; data=open(sys.argv[1],'rb').read(); print(data[:5] == b'%PDF-' and b'/Type' in data and b'/Page' in data)" ${JSON.stringify(fixture101Files[0])}`,
    { encoding: 'utf8' },
  ).trim();
  check('panel105-verify101-pdf-valid', pdf101Magic === 'True', String(fixture101Files[0]));
  observed.verify101 = { caseId: caseId101, status: status101, fixtureFiles: fixture101Files };

  // 6a) Desktop 1280: detalhe do painel de VERIFY-101 — 2 cards na posição
  // D8 (após Procedimentos declarados, antes da Trilha), lazy com contagem
  // por URL EXATA de serve desde ANTES do goto como GATE.
  {
    const { context, page } = await loginAs(browser, 'verify-manager', 'panel-101', { viewport: { width: 1280, height: 900 } });
    observed.pdfPluginMode = pdfPluginMode;
    // Marco ANTES da navegação: um fetch iniciado no goto escaparia de um
    // contador marcado depois (P2-b do PVP-003) — cobre o carregamento inicial.
    const markPanelClosedGoto = markPhase();
    await page.goto(`${base}/dashboard/case/${caseId101}/`, { waitUntil: 'domcontentloaded' });
    const panelGotoFetches = countEmbedFetches(markPanelClosedGoto, doc1PanelServe)
      + countEmbedFetches(markPanelClosedGoto, doc2PanelServe);
    observed.steps.push({ step: 'painel-101-detalhe-1280-goto-info', url: page.url(), gotoFetches: panelGotoFetches });
    const body = await page.locator('body').innerText();
    check('panel105-detail-heading', body.includes('Detalhes do caso'), page.url());
    check('panel105-text-card', body.includes('Texto Extraído do PDF'));
    const preText = await page.locator('#text-collapse pre').evaluate((el) => el.textContent).catch(() => '');
    check('panel105-text-content', preText.includes('TEXTO SINTETICO VERIFY-101 PARTE 1'), preText.slice(0, 60));
    check('panel105-pdf-card', body.includes('Visualizar PDF Original'));
    // Posição D8: Procedimentos → texto → PDF → trilha (headers em sequência).
    const headers = await page.locator('.card-header').evaluateAll((els) => els.map((el) => el.textContent.replace(/\s+/g, ' ').trim()));
    const wanted = ['Procedimentos declarados', 'Texto Extraído do PDF', 'Visualizar PDF Original', 'Trilha de eventos'];
    const positions = wanted.map((t) => headers.findIndex((x) => x.includes(t)));
    check('panel105-card-position', positions.every((p, i) => p >= 0 && (i === 0 || p > positions[i - 1])), headers.join(' | '));
    check('panel105-collapses-closed', (await page.locator('.collapse.show').count()) === 0, `${await page.locator('.collapse.show').count()} abertos`);
    const embeds = await page.locator('embed.pdf-lazy-embed').count();
    const srcs = await page.locator('embed.pdf-lazy-embed[src]').count();
    const dataSrcs = await page.locator('embed.pdf-lazy-embed[data-src]').count();
    check('panel105-embeds-lazy', embeds === 2 && srcs === 0 && dataSrcs === 2, `embeds=${embeds} src=${srcs} data-src=${dataSrcs}`);
    check('panel105-doc-collapse-attr', (await page.locator('[data-pdf-doc-collapse]').count()) === 2);
    check('panel105-mobile-links', (await page.locator('a.d-md-none[href*="/viewer/"]').count()) === 2);
    const mobileTexts = await page.locator('a.d-md-none[href*="/viewer/"]').evaluateAll((els) => els.map((el) => el.textContent.replace(/\s+/g, ' ').trim()));
    check('panel105-mobile-links-identify-docs', mobileTexts[0].includes('PDF 1') && mobileTexts[1].includes('PDF 2'), mobileTexts.join(' | '));
    check('panel105-new-tab-links', (await page.locator('a[target="_blank"]').count()) >= 2);
    check('panel105-lazy-script', (await page.locator('script[src*="pdf-lazy-embed.js"]').count()) >= 1);
    // GATE do lazy: fechado = 0 fetch nas URLs exatas DESDE ANTES DO GOTO
    // (revisão PVP-005 P1 — cobre o carregamento inicial em ambos os docs).
    await page.waitForTimeout(800);
    const closedFetches = countEmbedFetches(markPanelClosedGoto, doc1PanelServe) + countEmbedFetches(markPanelClosedGoto, doc2PanelServe);
    check('panel105-zero-fetch-closed', closedFetches === 0, `${closedFetches} fetch(es) de embed`);
    await snap(page, 'painel-101-detalhe-1280-fechado');
    observed.steps.push({ step: 'painel-101-detalhe-1280-fechado', url: page.url(), embedFetches: closedFetches });

    // Abrir o card externo: continua 0 fetch do embed (lazy é por sub-collapse).
    // Mesmo marco pré-goto (revisão PVP-005 P1 — inclui a navegação).
    await page.getByRole('button', { name: /Visualizar PDF Original/ }).click();
    await page.waitForTimeout(800);
    const cardFetches = countEmbedFetches(markPanelClosedGoto, doc1PanelServe) + countEmbedFetches(markPanelClosedGoto, doc2PanelServe);
    check('panel105-zero-fetch-card-open', cardFetches === 0, `${cardFetches} fetch(es) de embed`);
    await snap(page, 'painel-101-card-externo-aberto');

    // Expandir o sub-collapse do doc 1: só o alvo busca, 1 única vez.
    const markDoc1 = markPhase();
    await page.locator('button[data-bs-target="#pdf-collapse-1"]').click();
    await page.waitForFunction(() => {
      const el = document.querySelector('#pdf-collapse-1 embed.pdf-lazy-embed');
      return el && el.getAttribute('src');
    }, { timeout: 10000 });
    await page.waitForTimeout(2500);
    const doc1Src = await page.locator('#pdf-collapse-1 embed.pdf-lazy-embed').getAttribute('src');
    check('panel105-doc1-loaded', !!doc1Src && doc1Src === doc1PanelServe, String(doc1Src));
    const doc2Src = await page.locator('#pdf-collapse-2 embed.pdf-lazy-embed').getAttribute('src');
    check('panel105-doc2-still-lazy', !doc2Src, String(doc2Src));
    const doc1Fetches = countEmbedFetches(markDoc1, doc1PanelServe);
    check('panel105-embed-fetch-doc1', doc1Fetches >= 1, `${doc1Fetches} GET(s) sem marcador em ${doc1PanelServe}`);
    const doc2Fetches = countEmbedFetches(markDoc1, doc2PanelServe);
    check('panel105-no-embed-fetch-doc2', doc2Fetches === 0, `${doc2Fetches} GET(s) de embed em ${doc2PanelServe}`);
    await probeServesPdf(page, doc1PanelServe);
    await snap(page, 'painel-101-doc1-expandido');
    observed.steps.push({ step: 'painel-101-doc1-expandido', url: page.url(), unmarkedGetsDoc1: doc1Fetches, unmarkedGetsDoc2: doc2Fetches });
    await context.close();
  }

  // 6b) Mobile 390: só link, 0 fetch de embed; viewer aberto JÁ no 390 com
  // Voltar topo/rodapé; ?next= inseguro cai no detalhe do painel.
  {
    const { context, page } = await loginAs(browser, 'verify-manager', 'panel-101-mobile', { viewport: { width: 390, height: 844 } });
    const markMobileGoto = markPhase();
    await page.goto(`${base}/dashboard/case/${caseId101}/`, { waitUntil: 'domcontentloaded' });
    observed.steps.push({ step: 'painel-101-mobile-goto-info', url: page.url(), gotoFetches: countEmbedFetches(markMobileGoto, doc1PanelServe) + countEmbedFetches(markMobileGoto, doc2PanelServe) });
    // Mesmo marco pré-goto até o fim da seção mobile (revisão PVP-005 P1):
    // nenhuma expansão no mobile inicia busca em nenhum dos docs.
    await page.getByRole('button', { name: /Visualizar PDF Original/ }).click();
    await page.waitForTimeout(800);
    await page.locator('button[data-bs-target="#pdf-collapse-1"]').click().catch(() => {});
    await page.waitForTimeout(800);
    const mobileSrcs = await page.locator('embed.pdf-lazy-embed[src]').count();
    const mobileFetches = countEmbedFetches(markMobileGoto, doc1PanelServe) + countEmbedFetches(markMobileGoto, doc2PanelServe);
    check('panel105-mobile-zero-embed-fetch', mobileFetches === 0 && mobileSrcs === 0, `fetches=${mobileFetches} srcs=${mobileSrcs}`);
    await snap(page, 'painel-101-detalhe-390');
    observed.steps.push({ step: 'painel-101-detalhe-390', url: page.url(), embedFetches: mobileFetches });
    // O viewer mobile abre JÁ no viewport 390 (P2 do PVP-003).
    const viewerHref = await page.locator('a.d-md-none[href*="/viewer/"]').first().getAttribute('href');
    check('panel105-mobile-viewer-link', !!viewerHref && String(viewerHref).includes(`/dashboard/case/${caseId101}/pdf/1/viewer/`), viewerHref);
    const mobileViewport = page.viewportSize();
    check('panel105-mobile-viewport-390', mobileViewport?.width === 390, JSON.stringify(mobileViewport));
    await page.goto(`${base}${viewerHref}`, { waitUntil: 'domcontentloaded' });
    const backLinks = await page.locator('a:has-text("← Voltar")').count();
    check('panel105-viewer-voltar-topo-rodape', backLinks >= 2, `${backLinks} links Voltar`);
    const viewerBody = await page.locator('body').innerText();
    check('panel105-viewer-title', viewerBody.includes('PDF Original') || (await page.title()).includes('PDF Original'), await page.title());
    await page.waitForFunction(() => document.querySelectorAll('#pdf-pages canvas').length >= 1, { timeout: 20000 });
    const canvasCount = await page.locator('#pdf-pages canvas').count();
    check('panel105-viewer-pdfjs-rendered', canvasCount >= 1, `${canvasCount} canvas(es) PDF.js`);
    const canvasPixels = await page.locator('#pdf-pages canvas').first().evaluate((el) => {
      const thumb = document.createElement('canvas');
      thumb.width = 64; thumb.height = 64;
      thumb.getContext('2d').drawImage(el, 0, 0, 64, 64);
      const d = thumb.getContext('2d').getImageData(0, 0, 64, 64).data;
      let nonwhite = 0;
      for (let i = 0; i < d.length; i += 4) {
        if (d[i] < 250 || d[i + 1] < 250 || d[i + 2] < 250) nonwhite += 1;
      }
      return { w: el.width, h: el.height, nonwhite64: nonwhite };
    });
    check('panel105-viewer-canvas-nonblank', canvasPixels.nonwhite64 > 0, JSON.stringify(canvasPixels));
    await snap(page, 'painel-101-viewer-mobile');
    observed.steps.push({ step: 'painel-101-viewer-mobile', url: page.url() });
    // ?next= inseguro cai no detalhe do painel.
    const detailUrl = `${base}/dashboard/case/${caseId101}/`;
    await page.goto(`${base}${viewerHref.split('?')[0]}?next=https://evil.example/`, { waitUntil: 'domcontentloaded' });
    const evilBack = await page.locator('a:has-text("← Voltar")').first().getAttribute('href');
    check('panel105-viewer-next-inseguro-fallback', !!evilBack && !String(evilBack).includes('evil.example') && String(evilBack).includes(`/dashboard/case/${caseId101}/`), evilBack);
    await context.close();
  }

  // 6c) Resize 390→1280 com o sub-collapse do doc 2 aberto carrega o alvo
  // (revisão PVP-005 P2 — espelha o padrão nir101-resize do
  // drive-detalhe-papeis.mjs).
  {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const page = await context.newPage();
    page.on('response', (res) => {
      if (res.url().includes('/static/') && res.status() >= 400) failedAssets.push(`${res.status()} ${res.url()}`);
    });
    await page.goto(`${base}/login/`, { waitUntil: 'domcontentloaded' });
    await page.getByLabel('Usuário').fill('verify-manager');
    await page.getByLabel('Senha', { exact: true }).fill(PASSWORD);
    await page.getByRole('button', { name: 'Entrar' }).click();
    await page.waitForFunction(() => !location.pathname.endsWith('/login/'), { timeout: 15000 });
    await page.goto(`${base}/dashboard/case/${caseId101}/`, { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: /Visualizar PDF Original/ }).click();
    await page.waitForTimeout(500);
    // Abrir o sub-collapse do doc 2 em mobile (embed permanece sem src).
    await page.locator('#pdf-collapse-2').evaluate((el) => el.classList.add('show'));
    await page.waitForTimeout(500);
    const beforeResizeSrc = await page.locator('#pdf-collapse-2 embed.pdf-lazy-embed').getAttribute('src');
    check('panel105-resize-before-no-src', !beforeResizeSrc, String(beforeResizeSrc));
    const markResize = markPhase();
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.waitForFunction(() => {
      const el = document.querySelector('#pdf-collapse-2 embed.pdf-lazy-embed');
      return el && el.getAttribute('src');
    }, { timeout: 10000 });
    await page.waitForTimeout(2500);
    const afterResizeSrc = await page.locator('#pdf-collapse-2 embed.pdf-lazy-embed').getAttribute('src');
    check('panel105-resize-loads-target', !!afterResizeSrc && afterResizeSrc === doc2PanelServe, String(afterResizeSrc));
    const resizeFetches = countEmbedFetches(markResize, doc2PanelServe);
    check('panel105-resize-embed-fetch', resizeFetches >= 1, `${resizeFetches} GET(s) sem marcador em ${doc2PanelServe} (o plugin busca o embed no headed-Xvfb)`);
    await snap(page, 'painel-101-resize-1280-carregado');
    observed.steps.push({ step: 'painel-101-resize-1280-carregado', url: page.url(), embedFetchesDoc2: resizeFetches });
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

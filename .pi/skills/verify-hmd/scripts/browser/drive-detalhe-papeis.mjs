// Drive Playwright pinado do verify-hmd — detalhe-papeis (R3+R5) + PVP-002 (R3+R5).
// Jornada ORIGINAL (slice 003, §7 — passos preservados sobre VERIFY-001):
// médico decide (aprova) o VERIFY-001 sintético na página de trabalho no novo
// layout ats-web → agendador confirma unit 1 no detalhe no novo layout
// (reserva/heartbeat intactos) → NIR confirma o recebimento no detalhe
// (CLEANED). Tudo pela UI real (login pelo form, selects/inputs/botões do
// browser), sem mutação direta de banco nem mudança de seed/backend.
// Jornada NOVA (slice PVP-002, fix-1): detalhe NIR sobre VERIFY-101 (fixture
// criada NO DRIVE via runVerifyPy: 2 CaseDocuments com PDFs VÁLIDOS gerados
// por PyMuPDF com texto "VERIFY-101 doc N" + extracted_text populado +
// AWAITING_DOCTOR + declarados + criador verify-nir; IDs/nomes fora de
// VERIFY-001/002 e das faixas de outros drives; seed compartilhado intocado).
// Prova de rede distinguível do embed (R3): verificações diretas usam
// `?probe=1` e NÃO contam como prova; o drive conta no server.log as entradas
// da URL de serve SEM o marcador entre marcos de fase. Renderização provada:
// o embed renderiza no Chromium headless com PDF válido (o plugin de PDF do
// headless shell não renderiza — o drive usa Chromium headed sob Xvfb, modo
// registrado em observed-state.json). Evidência em <run>/artifacts/.
// Exit: 0 PASS · 1 falha observada · 2 infra/driver.
import { execSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync, readlinkSync, writeFileSync } from 'node:fs';
import { realpathSync } from 'node:fs';
import path from 'node:path';

// Guarda de mídia por run (slice PVP-002 fix-1, P1-1; endurecida no fix-2,
// P1-b): exige descendente ESTRITO do diretório da run após resolver o
// caminho real (cobre symlinks e `..`). Igualdade (`candidate === run`) é
// recusada, e candidatos INEXISTENTES são recusados de plano — a variante
// mais simples e segura: um caminho inexistente não carrega evidência de
// contenção (qualquer recomposição textual, ex. subir até um ancestral
// existente e reanexar o resto, pode passar `<run>/link-externo/novo` onde
// `link-externo` é um symlink pendente para fora da run). Todos os pontos
// de chamada do drive validam paths que já existem (mediaRoot criado no
// launch; fixtures listadas do disco), então recusar o inexistente nunca
// rejeita uso legítimo. Prefixo textual NÃO basta: `<run>/../../../media`
// passa no startsWith mas resolve fora da run. Espelha `media_path_ok` do
// control-hmd (testada em apps/accounts/tests/test_verify_media_isolation.py).
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
const MEDIA_ROOT = manifest.mediaRoot;

if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base) || Number(new URL(base).port) !== manifest.port) {
  console.error(`infra: manifest.url=${base} não é a origem loopback verificada (porta ${manifest.port}).`);
  process.exit(2);
}
if (!existsSync(artifacts)) {
  console.error(`infra: diretório de artefatos ausente em ${artifacts}.`);
  process.exit(2);
}
// R5: mediaRoot validado ANTES de qualquer subprocesso/servidor: descendente
// estrito da run pelo caminho real (P1-1); subprocesso e servidor usam
// este mesmo root.
if (!isMediaUnderRun(MEDIA_ROOT, runDir)) {
  console.error(`infra: mediaRoot inválido ou fora da run: ${MEDIA_ROOT} — recuso dirigir.`);
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
  if (!environ.includes(`HMD_VERIFY_MEDIA_ROOT=${MEDIA_ROOT}`)) refuseOwned('mediaRoot divergente do servidor');
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
async function waitShell(page) {
  await page.waitForFunction(
    () => {
      const shell = document.querySelector('.app-shell');
      return shell !== null && Number(getComputedStyle(shell).opacity) >= 0.99;
    },
    { timeout: 15000 },
  );
}
async function snapNoJs(page, name) {
  const file = path.join(artifacts, `${name}.txt`);
  let text;
  try { text = await page.locator('body').ariaSnapshot(); } catch { text = await page.locator('body').innerText(); }
  writeFileSync(file, `${page.url()}\n${text}\n`);
  await page.screenshot({ path: path.join(artifacts, `${name}.png`), fullPage: true });
}
async function snap(page, name) {
  await waitShell(page);
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
  await page.waitForFunction(() => !location.pathname.endsWith('/login/'), { timeout: 15000 });
  return { context, page };
}

// runVerifyPy com HMD_VERIFY_MEDIA_ROOT=mediaRoot VALIDADO sob a run
// (slice PVP-002, R5): o subprocesso grava no mesmo root do servidor; path
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

// Contagem de requisições na URL de serve (P1-2): o server.log do runserver
// registra cada GET com `"GET <path> HTTP/1.1" 200`. Verificações diretas de
// sanidade usam `?probe=1` e são EXCLUÍDAS da contagem — só entram linhas SEM
// o marcador. EVIDÊNCIA FORTE (harness headed sob Xvfb): o plugin de PDF
// do Chromium pinado (1.64.0/chromium-1248) BUSCA o <embed> cujo `src` foi
// atribuído via JS — o server.log registra GET sem marcador após cada
// expansão (retificação do fix-2: o fix-1 afirmava o contrário com um
// contador quebrado — off-by-one + path que nunca casava). A contagem prova
// a AUSÊNCIA (0 fetch fechado/mobile/card-aberto = lazy) E a presença
// target-only (expansão ⇒ ≥1 GET sem marcador SÓ no documento-alvo); a
// prova de RENDERIZAÇÃO segue sendo a da decisão C (PDF.js canvas não-branco).
// `markPhase()` retorna a posição da próxima linha nova do server.log
// (sem off-by-one); `countEmbedFetches` conta as linhas novas desde o marco
// para o caminho REAL `servePath` sem `probe=1`; falha de leitura do log
// lança (fail-loud) em vez de retornar zero silencioso.
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
// contagem acima): confirma que a URL de serve responde 200 application/pdf.
async function probeServesPdf(page, servePath) {
  const response = await page.request.get(`${base}${servePath}?probe=1`);
  const contentType = String(response.headers()['content-type'] || '');
  const ok = response.status() === 200 && contentType.includes('application/pdf');
  check('probe-serve-pdf', ok, `${response.status()} ${contentType} ${servePath}?probe=1`);
  return ok;
}

// O plugin de PDF do Chromium headless (headless shell) NÃO busca nem renderiza
// <embed type="application/pdf"> (prova P1-2: 0 GET no server.log mesmo com src
// atribuído). Evidência de rede/render do embed exige o Chromium headed — sob
// Xvfb quando houver DISPLAY (CI/harness), senão BLOCKED honesto (exit 2).
let browser;
try {
  if (!process.env.DISPLAY) {
    console.error('infra BLOCKED: sem DISPLAY — o plugin de PDF do headless não busca/renderiza <embed>; rode sob Xvfb (DISPLAY=:99) para prova de rede/render do embed. Nenhum motor substituto foi usado.');
    process.exit(2);
  }
  browser = await chromium.launch({ headless: false, args: ['--no-sandbox'] });
} catch (err) {
  console.error(`infra BLOCKED: Chromium pinado do Playwright indisponível (${err.message}) — nenhum motor substituto foi usado.`);
  process.exit(2);
}

const observed = { runId: manifest.runId, base, database: manifest.database, steps: [] };
// Modo de render do plugin registrado: Chromium headed sob Xvfb (DISPLAY) —
// o único modo em que o plugin de PDF busca e renderiza <embed>.
observed.pdfPluginMode = `headed-xvfb (DISPLAY=${process.env.DISPLAY})`;
let caseId = null;

try {
  // ══ PARTE A — jornada ORIGINAL sobre VERIFY-001 (§7: passos preservados) ══
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
      // PVP-003 (R2): o card "Documentos" foi substituído pelos dois cards
      // do relatório no mesmo ponto (após Prior-case, antes de Requisitos).
      // Os cards do relatório têm o título no botão do collapse (sem h2
      // próprio): a ordem é pinada pelos headers dos cards em sequência.
      const titles = await page.locator('.doctor-report-card .card-header').evaluateAll((els) => els.map((el) => el.textContent.trim()));
      const expected = ['Procedimentos do caso', 'Sumário clínico', 'Estrutura extraída', 'Prior-case', 'Texto Extraído do PDF', 'Requisitos gerais acionáveis', 'Alertas consultivos'];
      const positions = expected.map((t) => titles.findIndex((x) => x.includes(t)));
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
      // ══ PARTE A-bis — seção NOVA PVP-003 (médico, sobre VERIFY-001) ══
      // Detalhe read-only pós-decisão: o card de texto no mesmo ponto do
      // partial (após Prior-case, antes de Requisitos). VERIFY-001 do seed
      // NÃO tem documentos nem extracted_text populado (seed compartilhado
      // intocado — o drive veda mutação direta de banco): o estado honesto
      // aqui é card de texto com PLACEHOLDER (PVP-003 R2) e SEM card de PDF
      // (condicional a pdf_items). A prova completa do card de PDF (lazy +
      // viewer mobile) vive na seção 5-bis sobre VERIFY-101, que tem 2
      // documentos. NÃO substitui passos existentes: a decisão/aprovação acima
      // segue intacta; esta seção só LÊ o detalhe pós-decisão.
      {
        const detailUrl = `${base}/doctor/case/${caseId}/`;
        await page.goto(detailUrl, { waitUntil: 'domcontentloaded' });
        await waitShell(page);
        const bodyReadonly = await page.locator('body').innerText();
        check('doctor101-detail-text-card', bodyReadonly.includes('Texto Extraído do PDF'));
        // Placeholder de texto: VERIFY-001 não tem extracted_text → o card
        // exibe o placeholder em vez de omitir (diferença contratual vs
        // NIR). O placeholder vive no collapse fechado (fora do innerText):
        // ler o textContent do #text-collapse.
        const readonlyTextContent = await page.locator('#text-collapse').evaluate((el) => el.textContent);
        check('doctor101-detail-text-placeholder', readonlyTextContent.includes('Texto extraído indisponível para este caso.'), readonlyTextContent.trim().slice(0, 60));
        // Sem documentos no seed → sem card de PDF (honesto, não falha).
        check('doctor101-detail-no-pdf-without-docs', !bodyReadonly.includes('Visualizar PDF Original'));
        // Ordem clínica: texto após Prior-case, antes de Requisitos.
        const readonlyHeaders = await page.locator('.doctor-report-card .card-header').evaluateAll((els) => els.map((el) => el.textContent.trim()));
        const readonlyExpected = ['Prior-case', 'Texto Extraído do PDF', 'Requisitos gerais acionáveis'];
        const readonlyPositions = readonlyExpected.map((t) => readonlyHeaders.findIndex((x) => x.includes(t)));
        check('doctor101-detail-card-position', readonlyPositions.every((p, i) => p >= 0 && (i === 0 || p > readonlyPositions[i - 1])), readonlyHeaders.join(' | '));
        check('doctor101-detail-lazy-script', (await page.locator('script[src*="pdf-lazy-embed.js"]').count()) >= 1);
        await snap(page, 'doctor-101-detalhe-1280-fechado');
        observed.steps.push({ step: 'doctor-101-detalhe-1280-fechado', url: page.url() });
      }
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
      check('scheduler-queue-heading', (await page.getByRole('heading', { name: 'Solicitações de agendamento aguardando confirmação' }).count()) >= 1);
      const schedHref = await page.locator('[data-agency-record-number="VERIFY-001"] a.stretched-link').getAttribute('href').catch(() => null);
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

  // ══ PARTE B — jornada NOVA PVP-002 sobre VERIFY-101 (nir-pvp002) ══
  // Setup VERIFY-101 via runVerifyPy (NÃO toca o seed compartilhado):
  // criador verify-nir, AWAITING_DOCTOR, 2 documentos com PDFs VÁLIDOS
  // (PyMuPDF, 1 página com texto "VERIFY-101 doc N" — renderizáveis no
  // plugin) gravados no storage da run + extracted_text populado.
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
  const out = runVerifyPy(
    "from apps.cases.models import Case\n" +
    "c = Case.objects.get(agency_record_number='VERIFY-101')\n" +
    "from apps.cases.models import CaseDocument\n" +
    "print(str(c.case_id))\n" +
    "print(','.join(str(d.pk) + ':' + str(d.position) for d in c.documents.order_by('position')))",
  );
  const outLines = String(out).trim().split('\n');
  const caseId101 = outLines[outLines.length - 2].trim();
  const docMap = Object.fromEntries(
    outLines[outLines.length - 1].trim().split(',').map((pair) => {
      const [pk, pos] = pair.split(':');
      return [pos, pk];
    }),
  );
  const doc1ServePath = `/intake/cases/${caseId101}/documents/${docMap['1']}/`;
  const doc2ServePath = `/intake/cases/${caseId101}/documents/${docMap['2']}/`;
  //VERIRY-101 como caso médico AWAITING_DOCTOR acessível ao verify-doctor
  // (matriz D2: precisa de ≥1 tipo declarado do subtipo do médico; o
  // verify-doctor do seed não tem specialties → generalista, vê tudo).
  // O seed já criou os procedimentos declarados acima; nenhuma mutação nova.
  check('verify101-fixture', /^[0-9a-f-]{36}$/.test(caseId101) && !!docMap['1'] && !!docMap['2'], `caseId=${caseId101} docs=${JSON.stringify(docMap)}`);
  // R5: os PDFs da fixture estão no root da run (mediaRoot do manifest).
  const fixtureFiles = execSync(`find ${JSON.stringify(MEDIA_ROOT)} -type f -name '*.pdf' | sort`, { encoding: 'utf8' }).trim().split('\n').filter(Boolean);
  check('verify101-media-in-run-root', fixtureFiles.length >= 2 && fixtureFiles.every((f) => isMediaUnderRun(f, runDir)), `${fixtureFiles.length} pdf(s) sob ${MEDIA_ROOT}`);
  // PDFs válidos: magic %PDF + /Type /Page (PyMuPDF) — o plugin os renderiza.
  const pdfMagic = execSync(
    `python3 -c "import sys; data=open(sys.argv[1],'rb').read(); print(data[:5] == b'%PDF-' and b'/Type' in data and b'/Page' in data)" ${JSON.stringify(fixtureFiles[0])}`,
    { encoding: 'utf8' },
  ).trim();
  check('verify101-pdf-valid', pdfMagic === 'True', String(fixtureFiles[0]));
  observed.verify101 = { caseId: caseId101, docMap, fixtureFiles };

  // 4) Desktop 1280: detalhe — cards fechados, 0 fetch do embed.
  {
    const { context, page } = await loginAs(browser, 'verify-nir', 'nir-101', { viewport: { width: 1280, height: 900 } });
    // Marco ANTES da navegação: um fetch iniciado no goto escaparia de um
    // contador marcado depois (P2-b da revisão do slice PVP-003).
    const markNirClosedGoto = markPhase();
    await page.goto(`${base}/intake/cases/${caseId101}/`, { waitUntil: 'domcontentloaded' });
    await waitShell(page);
    const nirClosedGotoFetches = countEmbedFetches(markNirClosedGoto, '/intake/cases/');
    const nirClosedGotoInfo = nirClosedGotoFetches;
    void nirClosedGotoInfo;
    check('nir101-detail-layout', (await page.locator('.detail-layout').count()) === 1);
    const body = await page.locator('body').innerText();
    // O <pre> vive no collapse fechado (fora do innerText): ler o textContent.
    const preText = await page.locator('#text-collapse pre').evaluate((el) => el.textContent);
    check('nir101-text-card', body.includes('Texto Extraído do PDF') && preText.includes('TEXTO SINTETICO VERIFY-101 PARTE 1'), preText.slice(0, 60));
    check('nir101-pdf-card', body.includes('Visualizar PDF Original'));
    check('nir101-collapses-closed', (await page.locator('.collapse.show').count()) === 0, `${await page.locator('.collapse.show').count()} abertos`);
    const embedCount = await page.locator('embed.pdf-lazy-embed').count();
    const srcCount = await page.locator('embed.pdf-lazy-embed[src]').count();
    const dataSrcCount = await page.locator('embed.pdf-lazy-embed[data-src]').count();
    check('nir101-embeds-lazy', embedCount === 2 && srcCount === 0 && dataSrcCount === 2, `embeds=${embedCount} src=${srcCount} data-src=${dataSrcCount}`);
    check('nir101-doc-collapse-attr', (await page.locator('[data-pdf-doc-collapse]').count()) === 2);
    check('nir101-mobile-links', (await page.locator('a.d-md-none[href*="/viewer/"]').count()) === 2);
    check('nir101-new-tab-links', (await page.locator('a[target="_blank"]').count()) >= 2);
    check('nir101-lazy-script', (await page.locator('script[src*="pdf-lazy-embed.js"]').count()) >= 1);
    // Prova de rede do estado fechado: nenhum GET de serve SEM ?probe=1
    // (caminho REAL da requisição — `GET /intake/cases/.../documents/...`).
    // O contador pré-navegação (markNirClosedGoto) cobre também o
    // carregamento inicial (registro informativo em observed).
    const markClosed = markPhase();
    await page.waitForTimeout(800);
    const closedFetches = countEmbedFetches(markClosed, '/intake/cases/');
    check('nir101-zero-fetch-closed', closedFetches === 0, `${closedFetches} fetch(es) de embed`);
    observed.steps.push({ step: 'nir-101-detalhe-1280-fechado-goto-info', url: page.url(), gotoFetches: nirClosedGotoInfo });
    await snap(page, 'nir-101-detalhe-1280-fechado');
    observed.steps.push({ step: 'nir-101-detalhe-1280-fechado', url: page.url(), embedFetches: closedFetches });

    // Abrir o card externo: continua 0 fetch do embed (lazy é por sub-collapse).
    const markCard = markPhase();
    await page.getByRole('button', { name: /Visualizar PDF Original/ }).click();
    await page.waitForTimeout(800);
    const cardFetches = countEmbedFetches(markCard, '/intake/cases/');
    check('nir101-zero-fetch-card-open', cardFetches === 0, `${cardFetches} fetch(es) de embed`);
    await snap(page, 'nir-101-card-externo-aberto');

    // Expandir o sub-collapse do doc 1: o EMBED busca só o alvo, 1 única vez.
    // Prova distinguível: contagem no server.log das entradas da URL de serve
    // do doc 1 SEM ?probe=1 entre marcos (verificações diretas usam ?probe=1
    // e são excluídas). O drive FALHA se a prova faltar.
    const markDoc1 = markPhase();
    await page.locator('button[data-bs-target="#pdf-collapse-1"]').click();
    await page.waitForFunction(() => {
      const el = document.querySelector('#pdf-collapse-1 embed.pdf-lazy-embed');
      return el && el.getAttribute('src');
    }, { timeout: 10000 });
    await page.waitForTimeout(2500);
    const doc1Src = await page.locator('#pdf-collapse-1 embed.pdf-lazy-embed').getAttribute('src');
    const doc2Src = await page.locator('#pdf-collapse-2 embed.pdf-lazy-embed').getAttribute('src');
    check('nir101-doc1-loaded', !!doc1Src && doc1Src === doc1ServePath, String(doc1Src));
    check('nir101-doc2-still-lazy', !doc2Src, String(doc2Src));
    const doc1EmbedFetches = countEmbedFetches(markDoc1, doc1ServePath);
    check('nir101-embed-fetch-doc1', doc1EmbedFetches >= 1, `${doc1EmbedFetches} GET(s) sem marcador em ${doc1ServePath} (o plugin busca o embed no headed-Xvfb)`);
    const doc2EmbedFetches = countEmbedFetches(markDoc1, doc2ServePath);
    check('nir101-no-embed-fetch-doc2', doc2EmbedFetches === 0, `${doc2EmbedFetches} GET(s) de embed em ${doc2ServePath}`);
    // Verificação direta (marcada, não conta como prova): URL serve o PDF.
    await probeServesPdf(page, doc1ServePath);
    const srcCountAfter = await page.locator('embed.pdf-lazy-embed[src]').count();
    check('nir101-only-target-has-src', srcCountAfter === 1, `${srcCountAfter} embed(s) com src`);
    // Mecanismo D2 (src só no alvo, 1×) + URL válida (probe) + fetch do
    // plugin registrado no server.log + ausência do erro de plugin que o
    // PDF inválido anterior causava. A prova de RENDERIZAÇÃO segue a
    // decisão C: viewer PDF.js (etapa 5: canvas com o PDF).
    await snap(page, 'nir-101-doc1-expandido');
    const expandedText = await page.locator('body').innerText();
    check('nir101-no-plugin-error', !expandedText.includes("Couldn't load plugin"), expandedText.slice(0, 120));
    observed.steps.push({ step: 'nir-101-doc1-expandido', url: page.url(), unmarkedGetsDoc1: doc1EmbedFetches, unmarkedGetsDoc2: doc2EmbedFetches });
    await context.close();
  }

  // 5) Mobile 390: só link, 0 fetch de embed; viewer com Voltar topo/rodapé.
  // O contexto já nasce em 390 — o viewer abre no viewport mobile para a
  // evidência corresponder à alegação (P2-b da revisão do slice PVP-003).
  {
    const { context, page } = await loginAs(browser, 'verify-nir', 'nir-101-mobile', { viewport: { width: 390, height: 844 } });
    // O contexto já nasce em 390 — o viewer abre no viewport mobile para a
    // evidência corresponder à alegação (P2-b da revisão do slice PVP-003).
    // O marco pré-navegação cobre o carregamento inicial; o valor é
    // registrado como diagnóstico em observed (ver NOTA headed-Xvfb acima).
    const markNirMobileGoto = markPhase();
    await page.goto(`${base}/intake/cases/${caseId101}/`, { waitUntil: 'domcontentloaded' });
    await waitShell(page);
    const nirMobileGotoFetches = countEmbedFetches(markNirMobileGoto, '/intake/cases/');
    observed.steps.push({ step: 'nir-101-mobile-goto-info', url: page.url(), gotoFetches: nirMobileGotoFetches });
    // P1 da revisão do slice PVP-003 (escopo: partial do médico — o
    // template NIR `intake/case_detail.html` está fora do blast radius e
    // mantém os links genéricos): registra os textos para diagnóstico, sem
    // gate — o gate de distinguibilidade vive na seção médica + pytest.
    const nirMobileLinkTexts = await page.locator('a.d-md-none[href*="/viewer/"]').evaluateAll((els) => els.map((el) => el.textContent.replace(/\s+/g, ' ').trim()));
    void nirMobileLinkTexts;
    const markMobile = markPhase();
    await page.getByRole('button', { name: /Visualizar PDF Original/ }).click();
    await page.waitForTimeout(800);
    await page.locator('button[data-bs-target="#pdf-collapse-1"]').click().catch(() => {});
    await page.waitForTimeout(800);
    const mobileSrcs = await page.locator('embed.pdf-lazy-embed[src]').count();
    const mobileFetches = countEmbedFetches(markMobile, '/intake/cases/');
    check('nir101-mobile-zero-embed-fetch', mobileFetches === 0 && mobileSrcs === 0, `fetches=${mobileFetches} srcs=${mobileSrcs}`);
    await snap(page, 'nir-101-detalhe-390');
    observed.steps.push({ step: 'nir-101-detalhe-390', url: page.url(), embedFetches: mobileFetches });
    // Viewer dedicado do doc 1: PDF.js baixa o PDF pela MESMA rota de serve
    // e RENDERIZA em <canvas> — prova real de bytes válidos + renderização.
    // Fail-loud: o drive falha se o canvas não aparecer ou ficar em branco.
    const viewerHref = await page.locator('a.d-md-none[href*="/viewer/"]').first().getAttribute('href');
    check('nir101-mobile-viewer-link', !!viewerHref && viewerHref.includes('/viewer/'), viewerHref);
    await page.goto(`${base}${viewerHref}`, { waitUntil: 'domcontentloaded' });
    await waitShell(page);
    const viewerBackLinks = await page.locator('a:has-text("← Voltar")').count();
    check('nir101-viewer-voltar-topo-rodape', viewerBackLinks >= 2, `${viewerBackLinks} links Voltar`);
    const viewerBody = await page.locator('body').innerText();
    check('nir101-viewer-title', viewerBody.includes('PDF Original') || (await page.title()).includes('PDF Original'), await page.title());
    await page.waitForFunction(() => document.querySelectorAll('#pdf-pages canvas').length >= 1, { timeout: 20000 });
    const canvasCount = await page.locator('#pdf-pages canvas').count();
    check('nir101-viewer-pdfjs-rendered', canvasCount >= 1, `${canvasCount} canvas(es) PDF.js`);
    // Canvas não-branco: o texto PyMuPDF é preto fino anti-aliased sobre
    // branco — conta pixels ESTRITAMENTE não-brancos (qualquer canal < 250)
    // numa miniatura 64×64 (fail-loud se o canvas estiver em branco).
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
    check('nir101-viewer-canvas-nonblank', canvasPixels.nonwhite64 > 0, JSON.stringify(canvasPixels));
    await snap(page, 'nir-101-viewer-mobile');
    observed.steps.push({ step: 'nir-101-viewer-mobile', url: page.url() });
    // ?next= inseguro cai no detalhe.
    const detailUrl = `${base}/intake/cases/${caseId101}/`;
    await page.goto(`${base}${viewerHref.split('?')[0]}?next=https://evil.example/`, { waitUntil: 'domcontentloaded' });
    await waitShell(page);
    const evilBack = await page.locator('a:has-text("← Voltar")').first().getAttribute('href');
    check('nir101-viewer-next-inseguro-fallback', !!evilBack && !String(evilBack).includes('evil.example') && String(evilBack).includes(`/intake/cases/${caseId101}/`), evilBack);
    await context.close();
  }

  // 5-bis) Página de trabalho do médico sobre VERIFY-101 (PVP-003, R3):
  // AWAITING_DOCTOR com reserva — os MESMOS cards do partial, lazy + viewer.
  // A reserva via GET é o comportamento normal de leitura (gotcha da
  // feature); nenhuma decisão é submetida aqui — o drive só LÊ a página.
  // Prova de rede com o contador CORRIGIDO, como na Parte B (etapa 4).
  {
    const { context, page } = await loginAs(browser, 'verify-doctor', 'doctor-101-work', { viewport: { width: 1280, height: 900 } });
    // Marco ANTES da navegação: cobre também o carregamento inicial; o
    // valor é registrado como diagnóstico em observed (ver NOTA
    // headed-Xvfb acima) — a prova real do lazy segue nos contadores
    // pós-shell (markWorkClosed/markWorkCard).
    const markWorkGoto = markPhase();
    await page.goto(`${base}/doctor/case/${caseId101}/decide/`, { waitUntil: 'domcontentloaded' });
    await waitShell(page);
    const workGotoFetches = countEmbedFetches(markWorkGoto, '/doctor/case/');
    observed.steps.push({ step: 'doctor-101-work-goto-info', url: page.url(), gotoFetches: workGotoFetches });
    check('doctor101-work-layout', (await page.locator('.doctor-decision-layout').count()) === 1);
    check('doctor101-work-form', (await page.locator('#doctor-decision-form').count()) === 1);
    const bodyWork = await page.locator('body').innerText();
    check('doctor101-work-text-card', bodyWork.includes('Texto Extraído do PDF'));
    check('doctor101-work-pdf-card', bodyWork.includes('Visualizar PDF Original'));
    const preWork = await page.locator('#text-collapse pre').evaluate((el) => el.textContent).catch(() => '');
    check('doctor101-work-text-content', preWork.includes('TEXTO SINTETICO VERIFY-101 PARTE 1'), preWork.slice(0, 60));
    check('doctor101-work-collapses-closed', (await page.locator('.collapse.show').count()) === 0, `${await page.locator('.collapse.show').count()} abertos`);
    const workEmbeds = await page.locator('embed.pdf-lazy-embed').count();
    const workSrcs = await page.locator('embed.pdf-lazy-embed[src]').count();
    const workDataSrcs = await page.locator('embed.pdf-lazy-embed[data-src]').count();
    check('doctor101-work-embeds-lazy', workEmbeds === 2 && workSrcs === 0 && workDataSrcs === 2, `embeds=${workEmbeds} src=${workSrcs} data-src=${workDataSrcs}`);
    check('doctor101-work-mobile-links', (await page.locator('a.d-md-none[href*="/viewer/"]').count()) === 2);
    check('doctor101-work-lazy-script', (await page.locator('script[src*="pdf-lazy-embed.js"]').count()) >= 1);
    const markWorkClosed = markPhase();
    await page.waitForTimeout(800);
    const workClosedFetches = countEmbedFetches(markWorkClosed, '/doctor/case/');
    check('doctor101-work-zero-fetch-closed', workClosedFetches === 0, `${workClosedFetches} fetch(es) de embed`);
    await snap(page, 'doctor-101-work-1280-fechado');
    // Abrir o card externo: continua 0 fetch do embed.
    const markWorkCard = markPhase();
    await page.getByRole('button', { name: /Visualizar PDF Original/ }).click();
    await page.waitForTimeout(800);
    const workCardFetches = countEmbedFetches(markWorkCard, '/doctor/case/');
    check('doctor101-work-zero-fetch-card-open', workCardFetches === 0, `${workCardFetches} fetch(es) de embed`);
    // Expandir o sub-collapse do doc 1: só o alvo busca (target-only).
    const doc1DoctorServe = `/doctor/case/${caseId101}/pdf/1/`;
    const doc2DoctorServe = `/doctor/case/${caseId101}/pdf/2/`;
    const markWorkDoc1 = markPhase();
    await page.locator('button[data-bs-target="#pdf-collapse-1"]').click();
    await page.waitForFunction(() => {
      const el = document.querySelector('#pdf-collapse-1 embed.pdf-lazy-embed');
      return el && el.getAttribute('src');
    }, { timeout: 10000 });
    await page.waitForTimeout(2500);
    const workDoc1Src = await page.locator('#pdf-collapse-1 embed.pdf-lazy-embed').getAttribute('src');
    const workDoc2Src = await page.locator('#pdf-collapse-2 embed.pdf-lazy-embed').getAttribute('src');
    check('doctor101-work-doc1-loaded', !!workDoc1Src && workDoc1Src === doc1DoctorServe, String(workDoc1Src));
    check('doctor101-work-doc2-still-lazy', !workDoc2Src, String(workDoc2Src));
    const workDoc1Fetches = countEmbedFetches(markWorkDoc1, doc1DoctorServe);
    check('doctor101-work-embed-fetch-doc1', workDoc1Fetches >= 1, `${workDoc1Fetches} GET(s) sem marcador em ${doc1DoctorServe}`);
    const workDoc2Fetches = countEmbedFetches(markWorkDoc1, doc2DoctorServe);
    check('doctor101-work-no-embed-fetch-doc2', workDoc2Fetches === 0, `${workDoc2Fetches} GET(s) de embed em ${doc2DoctorServe}`);
    await snap(page, 'doctor-101-work-doc1-expandido');
    observed.steps.push({ step: 'doctor-101-work-doc1-expandido', url: page.url(), unmarkedGetsDoc1: workDoc1Fetches, unmarkedGetsDoc2: workDoc2Fetches });
    // Viewer mobile do médico (doctor:pdf_viewer, posição 1 de VERIFY-101)
    // com Voltar topo/rodapé; ?next= inseguro cai no detalhe do caso.
    const doctorViewerHref = await page.locator('a.d-md-none[href*="/viewer/"]').first().getAttribute('href');
    check('doctor101-viewer-link', !!doctorViewerHref && doctorViewerHref.includes('/doctor/case/') && doctorViewerHref.includes('/pdf/1/viewer/'), doctorViewerHref);
    await page.goto(`${base}${doctorViewerHref}`, { waitUntil: 'domcontentloaded' });
    await waitShell(page);
    const doctorViewerBacks = await page.locator('a:has-text("← Voltar")').count();
    check('doctor101-viewer-voltar-topo-rodape', doctorViewerBacks >= 2, `${doctorViewerBacks} links Voltar`);
    const doctorViewerBody = await page.locator('body').innerText();
    check('doctor101-viewer-title', doctorViewerBody.includes('PDF Original') || (await page.title()).includes('PDF Original'), await page.title());
    await snap(page, 'doctor-101-viewer-mobile');
    observed.steps.push({ step: 'doctor-101-viewer-mobile', url: page.url() });
    const doctorDetailUrl = `${base}/doctor/case/${caseId101}/`;
    await page.goto(`${base}${doctorViewerHref.split('?')[0]}?next=https://evil.example/`, { waitUntil: 'domcontentloaded' });
    await waitShell(page);
    const doctorEvilBack = await page.locator('a:has-text("← Voltar")').first().getAttribute('href');
    check('doctor101-viewer-next-inseguro-fallback', !!doctorEvilBack && !String(doctorEvilBack).includes('evil.example') && String(doctorEvilBack).includes(`/doctor/case/${caseId101}/`), doctorEvilBack);
    // Mobile 390 sobre a página de trabalho: só link, 0 fetch de embed.
    // Reabre a página (a reserva anterior segue válida para o mesmo ator —
    // claim renova com token novo; o GET re-reserva sem decidir). O
    // viewport volta a 390 ANTES da navegação para a página carregar já em
    // mobile (P2-b da revisão do slice PVP-003).
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(800);
    const markWorkMobileGoto = markPhase();
    await page.goto(`${base}/doctor/case/${caseId101}/decide/`, { waitUntil: 'domcontentloaded' });
    await waitShell(page);
    const workMobileGotoFetches = countEmbedFetches(markWorkMobileGoto, '/doctor/case/');
    observed.steps.push({ step: 'doctor-101-work-mobile-goto-info', url: page.url(), gotoFetches: workMobileGotoFetches });
    // P1 da revisão do slice PVP-003: os links mobile identificam o
    // documento (posição + nome) — textos distinguíveis entre si.
    const workMobileLinkTexts = await page.locator('a.d-md-none[href*="/viewer/"]').evaluateAll((els) => els.map((el) => el.textContent.replace(/\s+/g, ' ').trim()));
    check('doctor101-work-mobile-links-distinct', workMobileLinkTexts.length === 2 && workMobileLinkTexts[0] !== workMobileLinkTexts[1] && workMobileLinkTexts.every((t) => /\.pdf/i.test(t)), workMobileLinkTexts.join(' | '));
    const markWorkMobile = markPhase();
    // P1 da revisão do slice PVP-003: o viewer mobile abre já no viewport
    // 390 — registra o viewport efetivo na evidência.
    const workMobileViewport = page.viewportSize();
    check('doctor101-work-mobile-viewport-390', workMobileViewport?.width === 390, JSON.stringify(workMobileViewport));
    await page.getByRole('button', { name: /Visualizar PDF Original/ }).click();
    await page.waitForTimeout(800);
    const workMobileSrcs = await page.locator('embed.pdf-lazy-embed[src]').count();
    const workMobileFetches = countEmbedFetches(markWorkMobile, '/doctor/case/');
    check('doctor101-work-mobile-zero-embed-fetch', workMobileFetches === 0 && workMobileSrcs === 0, `fetches=${workMobileFetches} srcs=${workMobileSrcs}`);
    await snap(page, 'doctor-101-work-390');
    observed.steps.push({ step: 'doctor-101-work-390', url: page.url(), embedFetches: workMobileFetches });
    // Sair sem decidir: libera a reserva e volta à fila (sem decisão).
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.waitForTimeout(500);
    await page.getByRole('button', { name: 'Sair sem decidir' }).click();
    await page.waitForLoadState('domcontentloaded');
    check('doctor101-work-leave-queue', page.url().includes('/doctor/'), page.url());
    await context.close();
  }

  // ══ PARTE C — seção NOVA PVP-004 (agendador, sobre VERIFY-102) ══
  // Detalhe DENTRO do gate com os dois cards sob o {% if can_view_pdf %}
  // existente (D7 — texto é a ÚNICA adição à visão limitada). Fixture criada
  // NO DRIVE via runVerifyPy (NÃO toca o seed compartilhado): VERIFY-102 com
  // scheduled_by=verify-scheduler, status pós-decisão (transições EXISTENTES
  // dos serviços: decisão médica + confirm_case_scheduling), 1 PDF real via
  // PyMuPDF com texto "VERIFY-102 doc 1" + extracted_text populado, criador
  // verify-nir, IDs fora de VERIFY-001/VERIFY-002 e das faixas de outros
  // drives. Fora do gate (Parte A, VERIFY-001 pré-confirmação pelo
  // verify-scheduler sem scheduled_by): prove 404/ausência na rota direta do
  // detalhe/serve conforme o fluxo existente do drive.
  // Justificativa da escolha (tarefa PVP-004): VERIFY-001 pós-confirmação da
  // Parte A JÁ está DENTRO do gate; a prova dentro-do-gate é feita sobre o
  // VERIFY-102 dedicado para não acoplar a seção nova ao estado mutável da
  // Parte A, e a prova fora-do-gate usa a rota direta de VERIFY-001
  // pré-confirmação (mesma seção médica lê AWAITING). Contadores de rede no
  // padrão corrigido (contagem por URL EXATA de serve desde ANTES do goto):
  // detalhe fechado = 0 fetch; card externo aberto = 0 fetch; expansão do
  // sub-collapse ⇒ ≥1 GET sem marcador SÓ no alvo; mobile 390 = 0 fetch e 0
  // src; viewer mobile aberto JÁ no viewport 390 com Voltar topo/rodapé
  // (lições P2 do PVP-003 aplicadas desde o início).
  runVerifyPy(
    "from django.conf import settings\n" +
    "assert str(settings.MEDIA_ROOT).startswith('" + runDir + "'), settings.MEDIA_ROOT\n" +
    "import pymupdf\n" +
    "from django.contrib.auth import get_user_model\n" +
    "from django.utils import timezone\n" +
    "from datetime import timedelta\n" +
    "from apps.cases.models import Case, CaseDocument, CaseProcedure\n" +
    "from apps.cases.procedures import record_doctor_procedure_decisions\n" +
    "from apps.scheduler.services import confirm_case_scheduling\n" +
    "from django.core.files.base import ContentFile\n" +
    "nir = get_user_model().objects.get(username='verify-nir')\n" +
    "doctor = get_user_model().objects.get(username='verify-doctor')\n" +
    "sched = get_user_model().objects.get(username='verify-scheduler')\n" +
    "c, created = Case.objects.get_or_create(created_by=nir, agency_record_number='VERIFY-102', defaults={'patient_name': 'Paciente Sintetico Cento e Dois', 'patient_age': 58})\n" +
    "Case.objects.filter(pk=c.pk).update(patient_name='Paciente Sintetico Cento e Dois', patient_age=58, extracted_text='TEXTO SINTETICO VERIFY-102 PARTE 1.\\nTEXTO SINTETICO VERIFY-102 PARTE 2.')\n" +
    "c.refresh_from_db()\n" +
    "CaseProcedure.objects.get_or_create(case=c, procedure_type='art_perif', defaults={'declared_by_nir': True})\n" +
    "CaseProcedure.objects.get_or_create(case=c, procedure_type='cat_cardiaco', defaults={'declared_by_nir': True})\n" +
    "from apps.cases.models import CaseStatus\n" +
    "if c.status == CaseStatus.NEW:\n" +
    "    c.start_pdf_extraction(user=None, role='system')\n" +
    "    c.complete_pdf_extraction(user=None, role='system')\n" +
    "    c.complete_anonymization(user=None, role='system')\n" +
    "    c.complete_llm_extraction(user=None, role='system')\n" +
    "    c.complete_llm_summarization(user=None, role='system')\n" +
    "c.refresh_from_db()\n" +
    "if c.status == CaseStatus.AWAITING_DOCTOR:\n" +
    "    record_doctor_procedure_decisions(c, {'art_perif': ('approved', ''), 'cat_cardiaco': ('approved', '')}, user=doctor, role='doctor')\n" +
    "    c.refresh_from_db()\n" +
    "if c.status in (CaseStatus.SCHEDULER_REQUESTED, CaseStatus.AWAITING_SCHEDULING):\n" +
    "    confirm_case_scheduling(c, unit=1, scheduled_datetime=timezone.now() + timedelta(days=3), scheduled_location='Sala de hemodinamica — jornada PVP-004', user=sched, role='scheduler')\n" +
    "    c.refresh_from_db()\n" +
    "Case.objects.filter(pk=c.pk).update(extracted_text='TEXTO SINTETICO VERIFY-102 PARTE 1.\\nTEXTO SINTETICO VERIFY-102 PARTE 2.', scheduled_by=sched)\n" +
    "c.refresh_from_db()\n" +
    "existing = {d.position: d for d in c.documents.all()}\n" +
    "if 1 not in existing:\n" +
    "    doc = pymupdf.open()\n" +
    "    pg = doc.new_page()\n" +
    "    pg.insert_text((72, 720), 'VERIFY-102 doc 1', fontsize=24)\n" +
    "    pdf = bytes(doc.tobytes())\n" +
    "    doc.close()\n" +
    "    d = CaseDocument(case=c, position=1, original_filename='relatorio-verify102-parte1.pdf', content_type='application/pdf', size_bytes=len(pdf), uploaded_by=nir)\n" +
    "    d.file.save('relatorio-verify102-parte1.pdf', ContentFile(pdf), save=False)\n" +
    "    d.save()\n" +
    "print('VERIFY-102 ok', str(c.case_id), c.documents.count(), str(c.status))",
  );
  const out102 = runVerifyPy(
    "from apps.cases.models import Case\n" +
    "c = Case.objects.get(agency_record_number='VERIFY-102')\n" +
    "print(str(c.case_id))\n" +
    "print(str(c.status))\n" +
    "print(c.scheduled_by.username if c.scheduled_by_id else 'SEM-SCHEDULER')",
  );
  const out102Lines = String(out102).trim().split('\n');
  const caseId102 = out102Lines[out102Lines.length - 3].trim();
  const status102 = out102Lines[out102Lines.length - 2].trim();
  const schedBy102 = out102Lines[out102Lines.length - 1].trim();
  const doc1SchedServe = `/scheduler/case/${caseId102}/pdf/1/`;
  check('verify102-fixture', /^[0-9a-f-]{36}$/.test(caseId102) && schedBy102 === 'verify-scheduler', `caseId=${caseId102} status=${status102} scheduled_by=${schedBy102}`);
  // R5: o PDF da fixture está no root da run (mediaRoot do manifest). O
  // storage renomeia o arquivo (sufixo aleatório), então o find lista por
  // diretório do caso em vez de nome (`-newer` cobriria clock-skew).
  const fixture102Files = execSync(`find ${JSON.stringify(path.join(MEDIA_ROOT, 'case_documents', caseId102))} -type f -name '*.pdf' | sort`, { encoding: 'utf8' }).trim().split('\n').filter(Boolean);
  check('verify102-media-in-run-root', fixture102Files.length === 1 && fixture102Files.every((f) => isMediaUnderRun(f, runDir)), `${fixture102Files.length} pdf(s) sob ${MEDIA_ROOT}`);
  const pdf102Magic = execSync(
    `python3 -c "import sys; data=open(sys.argv[1],'rb').read(); print(data[:5] == b'%PDF-' and b'/Type' in data and b'/Page' in data)" ${JSON.stringify(fixture102Files[0])}`,
    { encoding: 'utf8' },
  ).trim();
  check('verify102-pdf-valid', pdf102Magic === 'True', String(fixture102Files[0]));
  observed.verify102 = { caseId: caseId102, status: status102, scheduledBy: schedBy102, fixtureFiles: fixture102Files };

  // 8) Desktop 1280: detalhe DENTRO do gate — 2 cards, lazy, 0 fetch fechado.
  {
    const { context, page } = await loginAs(browser, 'verify-scheduler', 'scheduler-102', { viewport: { width: 1280, height: 900 } });
    // Contador por URL EXATA de serve desde ANTES do goto (padrão corrigido).
    const markSchedClosedGoto = markPhase();
    await page.goto(`${base}/scheduler/case/${caseId102}/`, { waitUntil: 'domcontentloaded' });
    await waitShell(page);
    observed.steps.push({ step: 'scheduler-102-goto-info', url: page.url(), gotoFetches: countEmbedFetches(markSchedClosedGoto, doc1SchedServe) });
    check('sched102-detail-layout', (await page.locator('.detail-layout').count()) === 1);
    const schedBody = await page.locator('body').innerText();
    // Os DOIS cards sob o {% if can_view_pdf %} (D7).
    check('sched102-text-card', schedBody.includes('Texto Extraído do PDF'));
    const schedPre = await page.locator('#text-collapse pre').evaluate((el) => el.textContent).catch(() => '');
    check('sched102-text-content', schedPre.includes('TEXTO SINTETICO VERIFY-102 PARTE 1'), schedPre.slice(0, 60));
    check('sched102-pdf-card', schedBody.includes('Visualizar PDF Original'));
    check('sched102-collapses-closed', (await page.locator('.collapse.show').count()) === 0, `${await page.locator('.collapse.show').count()} abertos`);
    const schedEmbeds = await page.locator('embed.pdf-lazy-embed').count();
    const schedSrcs = await page.locator('embed.pdf-lazy-embed[src]').count();
    const schedDataSrcs = await page.locator('embed.pdf-lazy-embed[data-src]').count();
    check('sched102-embeds-lazy', schedEmbeds === 1 && schedSrcs === 0 && schedDataSrcs === 1, `embeds=${schedEmbeds} src=${schedSrcs} data-src=${schedDataSrcs}`);
    check('sched102-doc-collapse-attr', (await page.locator('[data-pdf-doc-collapse]').count()) === 1);
    check('sched102-mobile-link', (await page.locator('a.d-md-none[href*="/viewer/"]').count()) === 1);
    const schedMobileText = await page.locator('a.d-md-none[href*="/viewer/"]').first().evaluate((el) => el.textContent.replace(/\s+/g, ' ').trim());
    check('sched102-mobile-link-identifies-doc', schedMobileText.includes('PDF 1') && schedMobileText.includes('relatorio-verify102-parte1.pdf'), schedMobileText);
    check('sched102-new-tab-link', (await page.locator('a[target="_blank"]').count()) >= 1);
    check('sched102-lazy-script', (await page.locator('script[src*="pdf-lazy-embed.js"]').count()) >= 1);
    // Posição dos cards: texto antes do PDF, ambos antes de Comunicações,
    // depois de Procedimentos (mesmo ponto do antigo bloco can_view_pdf).
    const schedHeaders = await page.locator('.report-card .card-header').evaluateAll((els) => els.map((el) => el.textContent.trim()));
    const schedPos = ['Texto Extraído do PDF', 'Visualizar PDF Original', 'Comunicações'].map((t) => schedHeaders.findIndex((x) => x.includes(t)));
    check('sched102-card-position', schedPos.every((p, i) => p >= 0 && (i === 0 || p > schedPos[i - 1])), schedHeaders.join(' | '));
    const markSchedClosed = markPhase();
    await page.waitForTimeout(800);
    const schedClosedFetches = countEmbedFetches(markSchedClosed, doc1SchedServe);
    check('sched102-zero-fetch-closed', schedClosedFetches === 0, `${schedClosedFetches} fetch(es) de embed`);
    await snap(page, 'scheduler-102-detalhe-1280-fechado');
    observed.steps.push({ step: 'scheduler-102-detalhe-1280-fechado', url: page.url(), embedFetches: schedClosedFetches });

    // Card externo aberto: continua 0 fetch (lazy é por sub-collapse).
    const markSchedCard = markPhase();
    await page.getByRole('button', { name: /Visualizar PDF Original/ }).click();
    await page.waitForTimeout(800);
    const schedCardFetches = countEmbedFetches(markSchedCard, doc1SchedServe);
    check('sched102-zero-fetch-card-open', schedCardFetches === 0, `${schedCardFetches} fetch(es) de embed`);
    await snap(page, 'scheduler-102-card-externo-aberto');

    // Expansão do sub-collapse: só o alvo busca (target-only).
    const markSchedDoc1 = markPhase();
    await page.locator('button[data-bs-target="#pdf-collapse-1"]').click();
    await page.waitForFunction(() => {
      const el = document.querySelector('#pdf-collapse-1 embed.pdf-lazy-embed');
      return el && el.getAttribute('src');
    }, { timeout: 10000 });
    await page.waitForTimeout(2500);
    const schedDoc1Src = await page.locator('#pdf-collapse-1 embed.pdf-lazy-embed').getAttribute('src');
    check('sched102-doc1-loaded', !!schedDoc1Src && schedDoc1Src === doc1SchedServe, String(schedDoc1Src));
    await probeServesPdf(page, doc1SchedServe);
    const schedDoc1Fetches = countEmbedFetches(markSchedDoc1, doc1SchedServe);
    check('sched102-embed-fetch-doc1', schedDoc1Fetches >= 1, `${schedDoc1Fetches} GET(s) sem marcador em ${doc1SchedServe}`);
    await snap(page, 'scheduler-102-doc1-expandido');
    observed.steps.push({ step: 'scheduler-102-doc1-expandido', url: page.url(), unmarkedGetsDoc1: schedDoc1Fetches });
    await context.close();
  }

  // 9) Mobile 390: só link, 0 fetch; viewer aberto JÁ no viewport 390.
  {
    const { context, page } = await loginAs(browser, 'verify-scheduler', 'scheduler-102-mobile', { viewport: { width: 390, height: 844 } });
    const markSchedMobileGoto = markPhase();
    await page.goto(`${base}/scheduler/case/${caseId102}/`, { waitUntil: 'domcontentloaded' });
    await waitShell(page);
    observed.steps.push({ step: 'scheduler-102-mobile-goto-info', url: page.url(), gotoFetches: countEmbedFetches(markSchedMobileGoto, doc1SchedServe) });
    const markSchedMobile = markPhase();
    await page.getByRole('button', { name: /Visualizar PDF Original/ }).click();
    await page.waitForTimeout(800);
    const schedMobileSrcs = await page.locator('embed.pdf-lazy-embed[src]').count();
    const schedMobileFetches = countEmbedFetches(markSchedMobile, doc1SchedServe);
    check('sched102-mobile-zero-embed-fetch', schedMobileFetches === 0 && schedMobileSrcs === 0, `fetches=${schedMobileFetches} srcs=${schedMobileSrcs}`);
    await snap(page, 'scheduler-102-detalhe-390');
    observed.steps.push({ step: 'scheduler-102-detalhe-390', url: page.url(), embedFetches: schedMobileFetches });
    // O viewer mobile abre JÁ no viewport 390 (P2 do PVP-003).
    const schedViewerHref = await page.locator('a.d-md-none[href*="/viewer/"]').first().getAttribute('href');
    check('sched102-mobile-viewer-link', !!schedViewerHref && schedViewerHref.includes('/scheduler/case/') && schedViewerHref.includes('/pdf/1/viewer/'), schedViewerHref);
    const schedMobileViewport = page.viewportSize();
    check('sched102-mobile-viewport-390', schedMobileViewport?.width === 390, JSON.stringify(schedMobileViewport));
    await page.goto(`${base}${schedViewerHref}`, { waitUntil: 'domcontentloaded' });
    await waitShell(page);
    const schedViewerBacks = await page.locator('a:has-text("← Voltar")').count();
    check('sched102-viewer-voltar-topo-rodape', schedViewerBacks >= 2, `${schedViewerBacks} links Voltar`);
    const schedViewerBody = await page.locator('body').innerText();
    check('sched102-viewer-title', schedViewerBody.includes('PDF Original') || (await page.title()).includes('PDF Original'), await page.title());
    await page.waitForFunction(() => document.querySelectorAll('#pdf-pages canvas').length >= 1, { timeout: 20000 });
    const schedCanvasCount = await page.locator('#pdf-pages canvas').count();
    check('sched102-viewer-pdfjs-rendered', schedCanvasCount >= 1, `${schedCanvasCount} canvas(es) PDF.js`);
    const schedCanvasPixels = await page.locator('#pdf-pages canvas').first().evaluate((el) => {
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
    check('sched102-viewer-canvas-nonblank', schedCanvasPixels.nonwhite64 > 0, JSON.stringify(schedCanvasPixels));
    await snap(page, 'scheduler-102-viewer-mobile');
    observed.steps.push({ step: 'scheduler-102-viewer-mobile', url: page.url() });
    // ?next= inseguro cai no detalhe do agendador.
    await page.goto(`${base}${schedViewerHref.split('?')[0]}?next=https://evil.example/`, { waitUntil: 'domcontentloaded' });
    await waitShell(page);
    const schedEvilBack = await page.locator('a:has-text("← Voltar")').first().getAttribute('href');
    check('sched102-viewer-next-inseguro-fallback', !!schedEvilBack && !String(schedEvilBack).includes('evil.example') && String(schedEvilBack).includes(`/scheduler/case/${caseId102}/`), schedEvilBack);
    await context.close();
  }

  // 10) Fora do gate sobre VERIFY-101 (AWAITING_DOCTOR, sem scheduled_by):
  // ausência TOTAL dos cards no detalhe do agendador. VERIFY-101 não aparece
  // na fila do agendador — usa-se a rota DIRETA do detalhe (fluxo existente:
  // detalhe permitido, cards ausentes) e do serve (404 fail-closed).
  {
    const { context, page } = await loginAs(browser, 'verify-scheduler', 'scheduler-fora-gate', { viewport: { width: 1280, height: 900 } });
    await page.goto(`${base}/scheduler/case/${caseId101}/`, { waitUntil: 'domcontentloaded' });
    await waitShell(page);
    const foraBody = await page.locator('body').innerText();
    check('sched101-fora-gate-sem-cards', !foraBody.includes('Texto Extraído do PDF') && !foraBody.includes('Visualizar PDF Original'), page.url());
    check('sched101-fora-gate-sem-texto', !foraBody.includes('TEXTO SINTETICO VERIFY-101'), page.url());
    check('sched101-fora-gate-sem-script', (await page.locator('script[src*="pdf-lazy-embed.js"]').count()) === 0);
    await snap(page, 'scheduler-101-fora-do-gate');
    observed.steps.push({ step: 'scheduler-101-fora-do-gate', url: page.url() });
    // Serve direto fora do gate: 404 fail-closed.
    const schedServeResp = await page.request.get(`${base}/scheduler/case/${caseId101}/pdf/1/?probe=1`);
    check('sched101-serve-fora-gate-404', schedServeResp.status() === 404, `${schedServeResp.status()} ${doc1SchedServe}`);
    // Viewer direto fora do gate: 404 fail-closed.
    const schedViewerResp = await page.request.get(`${base}/scheduler/case/${caseId101}/pdf/1/viewer/?probe=1`);
    check('sched102-viewer-fora-gate-404', schedViewerResp.status() === 404, `${schedViewerResp.status()} viewer`);
    await context.close();
  }

  // 6) Resize 390→1280 com sub-collapse aberto carrega o alvo (via embed).
  {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const page = await context.newPage();
    page.on('response', (res) => {
      if (res.url().includes('/static/') && res.status() >= 400) failedAssets.push(`${res.status()} ${res.url()}`);
    });
    await page.goto(`${base}/login/`, { waitUntil: 'domcontentloaded' });
    await page.getByLabel('Usuário').fill('verify-nir');
    await page.getByLabel('Senha', { exact: true }).fill(PASSWORD);
    await page.getByRole('button', { name: 'Entrar' }).click();
    await page.waitForFunction(() => !location.pathname.endsWith('/login/'), { timeout: 15000 });
    await page.goto(`${base}/intake/cases/${caseId101}/`, { waitUntil: 'domcontentloaded' });
    await waitShell(page);
    await page.getByRole('button', { name: /Visualizar PDF Original/ }).click();
    await page.waitForTimeout(500);
    // Abrir o sub-collapse em mobile (embed permanece sem src por contrato).
    await page.locator('#pdf-collapse-2').evaluate((el) => el.classList.add('show'));
    await page.waitForTimeout(500);
    const beforeResizeSrc = await page.locator('#pdf-collapse-2 embed.pdf-lazy-embed').getAttribute('src');
    check('nir101-resize-before-no-src', !beforeResizeSrc, String(beforeResizeSrc));
    const markResize = markPhase();
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.waitForFunction(() => {
      const el = document.querySelector('#pdf-collapse-2 embed.pdf-lazy-embed');
      return el && el.getAttribute('src');
    }, { timeout: 10000 });
    await page.waitForTimeout(2500);
    const afterResizeSrc = await page.locator('#pdf-collapse-2 embed.pdf-lazy-embed').getAttribute('src');
    check('nir101-resize-loads-target', !!afterResizeSrc && afterResizeSrc === doc2ServePath, String(afterResizeSrc));
    const resizeFetches = countEmbedFetches(markResize, doc2ServePath);
    check('nir101-resize-embed-fetch', resizeFetches >= 1, `${resizeFetches} GET(s) sem marcador em ${doc2ServePath} (o plugin busca o embed no headed-Xvfb)`);
    await snap(page, 'nir-101-resize-1280-carregado');
    observed.steps.push({ step: 'nir-101-resize-1280-carregado', url: page.url(), embedFetchesDoc2: resizeFetches });
    await context.close();
  }

  // 7) Sem JS: bloco noscript visível e funcional.
  {
    const context = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 1280, height: 900 } });
    const page = await context.newPage();
    await page.goto(`${base}/login/`, { waitUntil: 'domcontentloaded' });
    await page.getByLabel('Usuário').fill('verify-nir');
    await page.getByLabel('Senha', { exact: true }).fill(PASSWORD);
    await page.getByRole('button', { name: 'Entrar' }).click();
    await page.waitForFunction(() => !location.pathname.endsWith('/login/'), { timeout: 15000 });
    await page.goto(`${base}/intake/cases/${caseId101}/`, { waitUntil: 'domcontentloaded' });
    const noscriptVisible = await page.locator('noscript').count();
    const noscriptHtml = await page.content();
    const directLinks = (noscriptHtml.match(/Abrir PDF relatorio-verify101-parte/g) || []).length;
    check('nir101-noscript-visible', noscriptVisible >= 1 && directLinks >= 2, `noscript=${noscriptVisible} links=${directLinks}`);
    // Link direto abre o PDF mesmo sem JS (serve_document responde 200).
    const href = await page.evaluate(() => {
      const html = document.documentElement.innerHTML;
      const m = html.match(/\/intake\/cases\/[0-9a-f-]+\/documents\/\d+\//);
      return m ? m[0] : null;
    });
    check('nir101-noscript-href', !!href, href);
    const pdfResponse = await page.request.get(`${base}${href}?probe=1`);
    check('nir101-noscript-serves-pdf', pdfResponse.status() === 200 && String(pdfResponse.headers()['content-type']).includes('application/pdf'), `${pdfResponse.status()} ${pdfResponse.headers()['content-type']}`);
    await snapNoJs(page, 'nir-101-sem-js-noscript');
    observed.steps.push({ step: 'nir-101-sem-js-noscript', url: page.url() });
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

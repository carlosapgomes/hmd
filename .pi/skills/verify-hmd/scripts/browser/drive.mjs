// Drive Playwright pinado do verify-hmd (slice 002/D7) — prova oficial.
// Login REAL pelo form (nenhum cookie injetado: sem addCookies em nenhum
// caminho), home por papel e leitura da fila médica. Evidência em
// <run>/artifacts/: PNG + snapshot + report.json + observed-state.json.
// Exit: 0 PASS · 1 falha observada da aplicação · 2 bloqueador infra/driver.
import { execSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync, readlinkSync, realpathSync, writeFileSync } from 'node:fs';
import path from 'node:path';

import { chromium } from 'playwright';

const runArg = process.argv.indexOf('--run');
if (runArg < 0 || !process.argv[runArg + 1]) {
  console.error('uso: drive.mjs --run <tmp/verification/hmd-verify-...>');
  process.exit(2);
}
const runDir = path.resolve(process.argv[runArg + 1]);
const manifest = JSON.parse(readFileSync(path.join(runDir, 'manifest.json'), 'utf8'));
const base = manifest.url;
// Jornada S2 (slice 002) roteada pelo entrypoint oficial: --journey s2 (ou --s2)
// preserva a jornada default e todos os guards; S2 usa a mesma posse/Chromium/assets.
const journeyArg = process.argv.indexOf('--journey');
const journeyName = journeyArg >= 0 ? process.argv[journeyArg + 1] : 'default';
const isS2 = journeyName === 's2' || process.argv.includes('--s2');
const isS4 = journeyName === 's4' || process.argv.includes('--s4');
const artifacts = path.join(runDir, 'artifacts');
const PASSWORD = manifest.seedPassword;

// Origem exata verificada: o driver só dirige a URL do manifest se ela for
// o loopback da porta do manifest (a mesma origem que o doctor atestou).
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base) || Number(new URL(base).port) !== manifest.port) {
  console.error(`infra: manifest.url=${base} não é a origem loopback verificada (porta ${manifest.port}) — recuso dirigir.`);
  process.exit(2);
}
if (!existsSync(artifacts)) {
  console.error(`infra: diretório de artefatos ausente em ${artifacts} — recuso dirigir.`);
  process.exit(2);
}

// Posse da instância (mesma amarração do doctor, repetida aqui para que
// NENHUM ponto de entrada dirija sem ela — nem a invocação direta deste
// script): PID vivo com start-ticks, marcador/build por-run no ambiente do
// PID, posse do socket LISTEN (inode) e identidade HEAD+dirty do checkout.
function refuseOwned(reason) {
  console.error(`infra: posse da instância não atestada (${reason}) — recuso dirigir (rode o doctor).`);
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
  if (!environ.includes(`HMD_VERIFY_MARKER=${manifest.marker}`)) {
    refuseOwned(`PID ${pid} não carrega o marcador desta run`);
  }
  if (!environ.includes(`HMD_VERIFY_BUILD=${manifest.build}`)) {
    refuseOwned(`PID ${pid} não carrega o build registrado`);
  }
  // STATIC_ROOT por-run: presente, sob o diretório da run (isolamento) e
  // no ambiente do processo servidor (serve deste root, não do checkout).
  if (!manifest.staticRoot) refuseOwned('staticRoot ausente no manifest');
  if (!String(manifest.staticRoot).startsWith(`${runDir}/`)) {
    refuseOwned(`staticRoot fora do diretório da run: ${manifest.staticRoot}`);
  }
  if (!environ.includes(`HMD_VERIFY_STATIC_ROOT=${manifest.staticRoot}`)) {
    refuseOwned(`PID ${pid} não serve do static root desta run`);
  }
  const cmdline = readFileSync(`/proc/${pid}/cmdline`, 'utf8').replaceAll('\0', ' ');
  if (!cmdline.includes('config.settings.verify') || !cmdline.includes(`127.0.0.1:${manifest.port}`)) {
    refuseOwned(`PID ${pid} não é o runserver de verificação desta porta`);
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
  if (!ownsSocket) refuseOwned(`PID ${pid} não detém o socket de escuta em 127.0.0.1:${manifest.port}`);
  const git = (args) => execSync(`git ${args}`, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
  const head = git('rev-parse HEAD').trim();
  // Mesmo algoritmo do control-hmd build_identity: status+diffs rastreados
  // + CONTEÚDO sha256 das fontes untracked relevantes de verificação/
  // harness — edição só-conteúdo de arquivo untracked muda o hash.
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
    const fileHash = createHash('sha256').update(readFileSync(file)).digest('hex');
    hash.update(`${file} ${fileHash}\n`);
  }
  const dirty = hash.digest('hex').slice(0, 12);
  if (`${head}-${dirty}` !== manifest.build) refuseOwned('build (HEAD+dirty) divergiu do manifest');
}
assertOwnedInstance();

// Assets estáticos exigidos pela casca do app: 404 aqui reprova o drive
// (capturas sem CSS/JS não servem como evidência visual).
const REQUIRED_ASSETS = ['/static/css/app.css', '/static/js/sw.js'];
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

async function loginAs(browser, username, label) {
  const context = await browser.newContext();
  const page = await context.newPage();
  page.on('response', (res) => {
    if (res.url().includes('/static/') && res.status() >= 400) {
      failedAssets.push(`${res.status()} ${res.url()}`);
    }
  });
  await page.goto(`${base}/login/`, { waitUntil: 'domcontentloaded' });
  await snap(page, `${label}-login-before`);
  await page.getByLabel('Usuário').fill(username);
  await page.getByLabel('Senha', { exact: true }).fill(PASSWORD);
  await page.getByRole('button', { name: 'Entrar' }).click();
  await page.waitForURL((url) => !url.pathname.endsWith('/login/'), { timeout: 15000 });
  return { context, page };
}

// Jornada provada: login doctor pelo form → home/fila médica → leitura do
// caso sintético (cartão + página de trabalho, sem decidir) → homes dos
// demais papéis pelo form. Cada papel em contexto isolado.
let browser;
try {
  // Chromium pinado do Playwright — nunca o Chrome do sistema. Se o
  // download não aconteceu (ver artifacts/playwright-install.log), o
  // lançamento falha e o drive sai BLOCKED (exit 2), sem substituir motor.
  // Para s4 (que prova rede de <embed type="application/pdf">), o plugin de PDF
  // exige Chromium headed sob Xvfb (DISPLAY), nunca headless-shell.
  if (isS4) {
    if (!process.env.DISPLAY) {
      console.error('infra BLOCKED: sem DISPLAY — o plugin de PDF do headless não busca/renderiza <embed>; rode sob Xvfb.');
      process.exit(2);
    }
    browser = await chromium.launch({ headless: false, args: ['--no-sandbox'] });
  } else {
    browser = await chromium.launch({ headless: true });
  }
} catch (err) {
  console.error(`infra BLOCKED: Chromium pinado do Playwright indisponível (${err.message}) — nenhum motor substituto foi usado.`);
  process.exit(2);
}

const observed = { runId: manifest.runId, base, database: manifest.database, steps: [] };
// Jornada S2 (slice 002) via entrypoint oficial drive.mjs --journey s2.
// Mesma posse/Chromium/assets do default (assertOwnedInstance acima); seed é
// setup, navegador prova fila/histórico/espera. Artefatos s2-* sem sobrescrever default.
if (isS2) {
  const s2checks = [];
  const s2failures = [];
  const s2failedAssets = [];
  const s2observed = { runId: manifest.runId, base, database: manifest.database, steps: [] };
  function s2check(id, ok, detail) {
    s2checks.push({ id, ok: !!ok, detail: String(detail ?? '') });
    if (!ok) s2failures.push(id);
    console.log(`${ok ? 'PASS' : 'FAIL'} ${id} ${detail ?? ''}`);
  }
  async function s2snap(page, name) {
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
  async function s2loginAs(viewport, username) {
    const context = await browser.newContext(viewport ? { viewport } : {});
    const page = await context.newPage();
    page.on('response', (res) => {
      if (res.url().includes('/static/') && res.status() >= 400) {
        s2failedAssets.push(`${res.status()} ${res.url()}`);
      }
    });
    await page.goto(`${base}/login/`, { waitUntil: 'domcontentloaded' });
    await page.getByLabel('Usuário').fill(username);
    await page.getByLabel('Senha', { exact: true }).fill(PASSWORD);
    await page.getByRole('button', { name: 'Entrar' }).click();
    await page.waitForURL((url) => !url.pathname.endsWith('/login/'), { timeout: 15000 });
    return { context, page };
  }
  // Unidade D (recuperação observável): fixture pendente via runVerifyPy
  // (setup pelo serviço real, nunca prova) + comando oficial em subprocesso.
  const MEDIA_ROOT = manifest.mediaRoot;
  function s2MediaUnderRun(candidate) {
    if (!candidate) return false;
    let runReal; let candidateReal;
    try { runReal = realpathSync(runDir); candidateReal = realpathSync(candidate); } catch { return false; }
    return candidateReal !== runReal && candidateReal.startsWith(runReal + path.sep);
  }
  const s2py = (process.env.UV_PROJECT_ENVIRONMENT && existsSync(`${process.env.UV_PROJECT_ENVIRONMENT}/bin/python`))
    ? `${process.env.UV_PROJECT_ENVIRONMENT}/bin/python`
    : (existsSync('.venv/bin/python') ? '.venv/bin/python' : 'uv run --no-sync python');
  function s2runVerifyPy(code) {
    if (!s2MediaUnderRun(MEDIA_ROOT)) throw new Error('infra: mediaRoot fora da run, recuso executar');
    return execSync(`${s2py} manage.py shell --settings=config.settings.verify -c "${code.replace(/"/g, '\\"')}"`, {
      env: { ...process.env, DJANGO_SETTINGS_MODULE: 'config.settings.verify', VERIFY_DB_HOST: manifest.dbHost || '127.0.0.1', VERIFY_DB_PORT: String(manifest.dbPort || process.env.TEST_DB_PORT || '55435'), VERIFY_DB_NAME: manifest.database, VERIFY_DB_USER: process.env.VERIFY_DB_USER || 'hmd', VERIFY_DB_PASSWORD: process.env.VERIFY_DB_PASSWORD || 'hmd_dev', HMD_VERIFY_MEDIA_ROOT: MEDIA_ROOT },
      stdio: ['ignore', 'pipe', 'pipe'], encoding: 'utf8',
    });
  }
  function s2runCommand(args) {
    if (!s2MediaUnderRun(MEDIA_ROOT)) throw new Error('infra: mediaRoot fora da run, recuso executar');
    return execSync(`${s2py} manage.py ${args} --settings=config.settings.verify`, {
      env: { ...process.env, DJANGO_SETTINGS_MODULE: 'config.settings.verify', VERIFY_DB_HOST: manifest.dbHost || '127.0.0.1', VERIFY_DB_PORT: String(manifest.dbPort || process.env.TEST_DB_PORT || '55435'), VERIFY_DB_NAME: manifest.database, VERIFY_DB_USER: process.env.VERIFY_DB_USER || 'hmd', VERIFY_DB_PASSWORD: process.env.VERIFY_DB_PASSWORD || 'hmd_dev', HMD_VERIFY_MEDIA_ROOT: MEDIA_ROOT },
      stdio: ['ignore', 'pipe', 'pipe'], encoding: 'utf8',
    });
  }
  const VIEWPORTS = { desktop: { width: 1280, height: 800 }, mobile: { width: 390, height: 844 } };
  try {
    for (const [label, viewport] of Object.entries(VIEWPORTS)) {
      const { context, page } = await s2loginAs(viewport, 'verify-doctor');
      s2check(`s2-doctor-home-${label}`, page.url().includes('/doctor/'), page.url());
      const cards = page.locator('[data-doctor-queue-card][data-agency-record-number="880101"]');
      s2check(`s2-single-new-card-${label}`, (await cards.count()) === 1, `${await cards.count()} card(s) 880101`);
      const waiting = await cards.first().locator('.waiting-time').innerText();
      s2check(`s2-inherited-wait-${label}`, /6\s*dias/.test(waiting), waiting.trim());
      await s2snap(page, `s2-doctor-queue-${label}`);
      s2observed.steps.push({ step: `s2-doctor-queue-${label}`, url: page.url(), waiting: waiting.trim() });
      for (const asset of REQUIRED_ASSETS) {
        const status = await page.evaluate(async (u) => (await fetch(u, { cache: 'no-store' })).status, asset);
        s2check(`s2-static-asset-${label}-${asset.split('/').pop()}`, status === 200, `${asset} HTTP ${status}`);
      }
      const decideHref = await cards.first().locator('a.stretched-link').getAttribute('href');
      const m = decideHref && decideHref.match(/\/doctor\/case\/([0-9a-f-]{36})\//);
      s2check(`s2-card-href-${label}`, !!m, decideHref);
      if (m) {
        await page.goto(`${base}/doctor/case/${m[1]}/`, { waitUntil: 'domcontentloaded' });
        const detailBody = await page.locator('body').innerText();
        s2check(`s2-representative-detail-${label}`, detailBody.includes('880101'), page.url());
        await s2snap(page, `s2-doctor-detail-${label}`);
        s2observed.steps.push({ step: `s2-doctor-detail-${label}`, url: page.url() });
      }
      await context.close();
    }
    {
      const { context, page } = await s2loginAs(null, 'verify-nir');
      await page.goto(`${base}/intake/closed-cases/?q=880101`, { waitUntil: 'domcontentloaded' });
      const searchBody = await page.locator('body').innerText();
      s2check('s2-history-search-finds-original', searchBody.includes('880101'), 'nº 880101 na busca');
      await s2snap(page, 's2-nir-search');
      s2observed.steps.push({ step: 's2-nir-search', url: page.url() });
      const closedLink = page.locator('a.btn[href*="/intake/closed-cases/"]:has-text("Ver detalhes")').first();
      s2check('s2-history-link', (await closedLink.count()) >= 1);
      await closedLink.click();
      await page.waitForURL(/\/intake\/closed-cases\//);
      const historyBody = await page.locator('body').innerText();
      s2check('s2-history-substitution-card', historyBody.includes('Reapresentação do mesmo pedido'), 'card de vínculo');
      const immediateHref = await page.locator('dt:has-text("Substituído por") + dd a').getAttribute('href');
      const representativeHref = await page.locator('dt:has-text("Representante atual") + dd a').getAttribute('href');
      s2check('s2-immediate-link', !!immediateHref, immediateHref);
      s2check('s2-representative-link', !!representativeHref, representativeHref);
      s2check('s2-single-hop-chain', !!immediateHref && immediateHref === representativeHref, `${immediateHref} vs ${representativeHref}`);
      await s2snap(page, 's2-nir-history');
      s2observed.steps.push({ step: 's2-nir-history', url: page.url(), immediateHref, representativeHref });
      if (representativeHref) {
        await page.goto(`${base}${representativeHref}`, { waitUntil: 'domcontentloaded' });
        const repBody = await page.locator('body').innerText();
        s2check('s2-representative-active', page.url().includes('/intake/cases/') && repBody.includes('880101'), page.url());
        await s2snap(page, 's2-nir-representative');
        s2observed.steps.push({ step: 's2-nir-representative', url: page.url() });
      }
      await context.close();
    }
    // 3) Unidade D: recuperação observável (R4.10/R4.11). Setup pendente
    // 880102 pelo serviço real (lease viva → PENDING com agenda); navegador
    // prova os avisos NIR/gerencial do incoming, ausência no prior, falha
    // sem agenda, regularização pelo comando oficial e histórico na trilha.
    const s2fixture = s2runVerifyPy([
      'from apps.accounts.models import User',
      'from apps.cases.models import Case, CaseProcedure',
      'from apps.cases.locks import claim_case_lock',
      'from apps.cases import reappearance as R',
      'from django.utils import timezone',
      'from datetime import timedelta',
      'owner = User.objects.get(username="verify-nir")',
      'doctor = User.objects.get(username="verify-doctor")',
      'def awaiting(c):',
      '    c.start_pdf_extraction(user=None, role="system"); c.complete_pdf_extraction(user=None, role="system"); c.complete_anonymization(user=None, role="system"); c.complete_llm_extraction(user=None, role="system"); c.complete_llm_summarization(user=None, role="system")',
      'def make(number):',
      '    c = Case.objects.create(created_by=owner)',
      '    CaseProcedure.objects.create(case=c, procedure_type="angio_art_perif", declared_by_nir=True)',
      '    c.patient_name = "Paciente Sintetico Pendente"; c.agency_record_number = number; c.save(update_fields=["patient_name", "agency_record_number"])',
      '    awaiting(c); return c',
      'existing = list(Case.objects.filter(agency_record_number="880102").order_by("created_at"))',
      'assert not existing, "880102 já semeado nesta run"',
      'prior = make("880102")',
      'Case.objects.filter(pk=prior.pk).update(created_at=timezone.now() - timedelta(days=6)); prior.refresh_from_db()',
      // Lease ANTES do incoming: no verify as tasks rodam inline e o atomic
      // final do incoming adia (PENDING com agenda) em vez de aplicar.
      'claim_case_lock(prior, user=doctor, context="doctor_decision", role="doctor")',
      'incoming = make("880102")',
      'from apps.cases.models import CaseReappearance',
      'link = CaseReappearance.objects.get(incoming_case=incoming, state="pending")',
      'assert link.attempt_count == 1, link.attempt_count',
      'print("S2D_LINK", link.pk)',
      'print("S2D_INCOMING", incoming.case_id)',
      'print("S2D_PRIOR", prior.case_id)',
    ].join('\n'));
    const s2link = s2fixture.match(/S2D_LINK ([0-9a-f-]{36})/)?.[1];
    const s2incoming = s2fixture.match(/S2D_INCOMING ([0-9a-f-]{36})/)?.[1];
    const s2prior = s2fixture.match(/S2D_PRIOR ([0-9a-f-]{36})/)?.[1];
    s2check('s2-d-pending-fixture', !!(s2link && s2incoming && s2prior), `${s2link || 'sem link'}`);
    const WARNINGS = ['Recuperação de reapresentação agendada', 'Falha ao agendar a recuperação de reapresentação', 'Recuperação de reapresentação esgotada'];
    if (s2link && s2incoming && s2prior) {
      {
        const { context, page } = await s2loginAs(VIEWPORTS.desktop, 'verify-nir');
        await page.goto(`${base}/intake/cases/${s2incoming}/`, { waitUntil: 'domcontentloaded' });
        const body = await page.locator('body').innerText();
        s2check('s2-d-nir-incoming-scheduled', body.includes(WARNINGS[0]) && !body.includes(WARNINGS[1]), 'agendada no incoming (NIR)');
        s2check('s2-d-no-reenqueue-button', !body.includes('Reenfileirar') && !body.includes('reenqueue_pending'), 'sem botão/ação no detalhe');
        await s2snap(page, 's2-d-nir-scheduled');
        s2observed.steps.push({ step: 's2-d-nir-scheduled', url: page.url() });
        await page.goto(`${base}/intake/cases/${s2prior}/`, { waitUntil: 'domcontentloaded' });
        const priorBody = await page.locator('body').innerText();
        s2check('s2-d-nir-prior-no-warning', WARNINGS.every((w) => !priorBody.includes(w)), 'prior sem aviso (NIR)');
        await s2snap(page, 's2-d-nir-prior');
        await context.close();
      }
      {
        const { context, page } = await s2loginAs(VIEWPORTS.desktop, 'verify-manager');
        await page.goto(`${base}/dashboard/case/${s2incoming}/`, { waitUntil: 'domcontentloaded' });
        const body = await page.locator('body').innerText();
        s2check('s2-d-manager-incoming-scheduled', body.includes(WARNINGS[0]), 'agendada no incoming (gerencial)');
        await s2snap(page, 's2-d-manager-scheduled');
        s2observed.steps.push({ step: 's2-d-manager-scheduled', url: page.url() });
        await page.goto(`${base}/dashboard/case/${s2prior}/`, { waitUntil: 'domcontentloaded' });
        const priorBody = await page.locator('body').innerText();
        s2check('s2-d-manager-prior-no-warning', WARNINGS.every((w) => !priorBody.includes(w)), 'prior sem aviso (gerencial)');
        await context.close();
      }
      // Falha sem agenda (setup honesto do estado pós-falha) → aviso de falha.
      s2runVerifyPy([
        'from django_q.models import Schedule',
        'from django.utils import timezone',
        'from apps.cases.models import CaseReappearance',
        'from apps.cases import reappearance as R',
        `Schedule.objects.filter(name__startswith="reappearance-retry:${s2link}:").delete()`,
        `CaseReappearance.objects.filter(pk="${s2link}").update(last_reason_code="schedule_failed")`,
        // Evento de falha pelo MESMO helper do caminho real (setup honesto:
        // a trilha precisa conter a falha, não só o campo técnico).
        `link = CaseReappearance.objects.get(pk="${s2link}"); link.next_attempt_at = link.next_attempt_at or timezone.now(); R._record_schedule_failed_event(link, attempts=int(link.attempt_count))`,
        'print("S2D_FAILED_OK")',
      ].join('\n'));
      {
        const { context, page } = await s2loginAs(VIEWPORTS.desktop, 'verify-nir');
        await page.goto(`${base}/intake/cases/${s2incoming}/`, { waitUntil: 'domcontentloaded' });
        const body = await page.locator('body').innerText();
        s2check('s2-d-nir-incoming-failed', body.includes(WARNINGS[1]) && body.toLowerCase().includes('suporte') && !body.includes(WARNINGS[0]), 'falha sem agenda + suporte');
        await s2snap(page, 's2-d-nir-failed');
        s2observed.steps.push({ step: 's2-d-nir-failed', url: page.url() });
        await context.close();
      }
      // Comando oficial: simulação zero-escritas, --apply agenda, repetir NO-OP.
      const simOut = s2runCommand(`reenqueue_pending_reappearances ${s2link}`);
      s2check('s2-d-command-simulation', simOut.includes('SIMULAÇÃO'), simOut.trim().split('\n').pop());
      const applyOut = s2runCommand(`reenqueue_pending_reappearances ${s2link} --apply`);
      s2check('s2-d-command-applied', applyOut.includes('AGENDADO'), applyOut.trim().split('\n').pop());
      const noopOut = s2runCommand(`reenqueue_pending_reappearances ${s2link} --apply`);
      s2check('s2-d-command-noop', noopOut.includes('NO-OP'), noopOut.trim().split('\n').pop());
      {
        const { context, page } = await s2loginAs(VIEWPORTS.desktop, 'verify-nir');
        await page.goto(`${base}/intake/cases/${s2incoming}/`, { waitUntil: 'domcontentloaded' });
        const body = await page.locator('body').innerText();
        s2check('s2-d-regularized-scheduled', body.includes(WARNINGS[0]) && !body.includes(WARNINGS[1]), 'regularizada após reenqueue');
        const trailBtn = page.getByRole('button', { name: 'Mostrar trilha' });
        if ((await trailBtn.count()) > 0) await trailBtn.first().click();
        const trailBody = await page.locator('body').innerText();
        s2check('s2-d-history-preserved', trailBody.includes('Falha ao agendar recuperação de reapresentação') && trailBody.includes('Recuperação de reapresentação reenfileirada'), 'histórico do ciclo na trilha');
        await s2snap(page, 's2-d-nir-regularized');
        s2observed.steps.push({ step: 's2-d-nir-regularized', url: page.url() });
        await context.close();
      }
    }
  } catch (err) {
    console.error(`infra/driver: ${err.message}`);
    writeFileSync(path.join(artifacts, 'observed-state-s2.json'), JSON.stringify(s2observed, null, 2));
    await browser.close();
    process.exit(2);
  }
  s2check('s2-no-failed-static-assets', s2failedAssets.length === 0, s2failedAssets.join('; ') || 'nenhum /static/* >= 400');
  writeFileSync(path.join(artifacts, 'report-s2.json'), JSON.stringify({ runId: manifest.runId, checks: s2checks, failures: s2failures, pass: s2failures.length === 0 }, null, 2));
  writeFileSync(path.join(artifacts, 'observed-state-s2.json'), JSON.stringify(s2observed, null, 2));
  await browser.close();
  console.log(s2failures.length === 0 ? 'DRIVE S2 PASS' : `DRIVE S2 FAIL: ${s2failures.join(', ')}`);
  process.exit(s2failures.length === 0 ? 0 : 1);
}
// Jornada S4 (slice 004) via entrypoint oficial drive.mjs --journey s4.
// Mesma posse/Chromium/assets do default; prova fila, #reapresentacoes-recebidas
// (lazy PDF desktop, viewer mobile + Voltar, imagem), histórico retido vs purgado
// e confirmação real em 1280×800 e 390×844. Artefatos s4-* sem sobrescrever default.
if (isS4) {
  const s4checks = [];
  const s4failures = [];
  const s4failedAssets = [];
  const s4observed = { runId: manifest.runId, base, database: manifest.database, steps: [] };
  function s4check(id, ok, detail) {
    s4checks.push({ id, ok: !!ok, detail: String(detail ?? '') });
    if (!ok) s4failures.push(id);
    console.log(`${ok ? 'PASS' : 'FAIL'} ${id} ${detail ?? ''}`);
  }
  async function s4snap(page, name) {
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
  async function s4loginAs(viewport, username) {
    const context = await browser.newContext(viewport ? { viewport } : {});
    const page = await context.newPage();
    page.on('response', (res) => {
      if (res.url().includes('/static/') && res.status() >= 400) {
        s4failedAssets.push(`${res.status()} ${res.url()}`);
      }
    });
    await page.goto(`${base}/login/`, { waitUntil: 'domcontentloaded' });
    await page.getByLabel('Usuário').fill(username);
    await page.getByLabel('Senha', { exact: true }).fill(PASSWORD);
    await page.getByRole('button', { name: 'Entrar' }).click();
    await page.waitForURL((url) => !url.pathname.endsWith('/login/'), { timeout: 15000 });
    return { context, page };
  }
  const VIEWPORTS = { desktop: { width: 1280, height: 800 }, mobile: { width: 390, height: 844 } };
  let case401Url = '';
  let case401Id = '';
  let case402Url = '';
  let case402Id = '';
  try {
    // ── 1) Desktop 1280×800 ──
    {
      const { context, page } = await s4loginAs(VIEWPORTS.desktop, 'verify-scheduler');
      const reappResponses = [];
      page.on('response', (res) => {
        const u = new URL(res.url()).pathname;
        if (u.includes('/reappearances/')) {
          reappResponses.push({ pathname: u, status: res.status() });
        }
      });

      s4check('s4-desktop-scheduler-home', page.url().includes('/scheduler/'), page.url());
      for (const asset of REQUIRED_ASSETS) {
        const status = await page.evaluate(async (u) => (await fetch(u, { cache: 'no-store' })).status, asset);
        s4check(`s4-static-asset-desktop-${asset.split('/').pop()}`, status === 200, `${asset} HTTP ${status}`);
      }

      const card401 = page.locator('[data-scheduler-queue-card][data-agency-record-number="880401"]');
      s4check('s4-desktop-queue-card-880401', (await card401.count()) === 1, `${await card401.count()} card(s)`);
      const cardText = await card401.first().innerText();
      const waitingText = await card401.first().locator('.waiting-time').innerText();
      s4check('s4-desktop-queue-inherited-wait', /6\s*dias/.test(waitingText), waitingText.trim());
      s4check(
        's4-desktop-queue-counter-badge',
        cardText.includes('Caso reapresentado 2 vezes enquanto aguarda agendamento') &&
          cardText.includes('Último recebimento:'),
        'badge 2 vezes + último recebimento',
      );
      const anchorLink = card401.first().locator('a[href*="#reapresentacoes-recebidas"]');
      s4check('s4-desktop-queue-anchor-link', (await anchorLink.count()) === 1, await anchorLink.getAttribute('href'));
      await s4snap(page, 's4-desktop-queue');
      s4observed.steps.push({ step: 's4-desktop-queue', url: page.url(), waiting: waitingText.trim() });

      // Navega pelo link do card até #reapresentacoes-recebidas
      await anchorLink.click();
      await page.waitForURL(/\/scheduler\/case\/[0-9a-f-]{36}\//, { timeout: 15000 });
      await page.waitForLoadState('networkidle');
      case401Url = page.url().split('#')[0];
      case401Id = case401Url.match(/\/scheduler\/case\/([0-9a-f-]{36})\//)?.[1] || '';

      const confirmForm = page.locator('form[action$="/confirm/"]');
      const denyForm = page.locator('form[action$="/deny/"]');
      s4check(
        's4-desktop-401-post-forms-and-lock',
        (await confirmForm.count()) === 1 &&
          (await denyForm.count()) === 1 &&
          (await confirmForm.locator('input[name="lock_token"]').count()) === 1 &&
          (await denyForm.locator('input[name="lock_token"]').count()) === 1,
        'confirm/deny POST + lock_token',
      );
      const section401 = page.locator('#reapresentacoes-recebidas');
      const section401Text = await section401.innerText();
      s4check(
        's4-desktop-401-disclaimer',
        section401Text.includes('Documentação recebida em reapresentação — não substitui o relatório aprovado pelo médico.'),
        'aviso de não-aprovação presente',
      );
      const body401 = await page.locator('body').innerText();
      s4check('s4-desktop-401-no-original-pdf-card', !body401.includes('Visualizar PDF Original'), 'PDF original oculto');

      const lazyPaths401 = await page.locator('#reapresentacoes-recebidas embed.pdf-lazy-embed').evaluateAll((els) =>
        els.map((el) => el.getAttribute('data-src')).filter(Boolean),
      );
      const eagerPdfFetches401 = reappResponses.filter((r) => lazyPaths401.includes(r.pathname));
      s4check(
        's4-desktop-401-zero-eager-pdf-fetches',
        lazyPaths401.length >= 2 && eagerPdfFetches401.length === 0,
        `embeds=${lazyPaths401.length}, eager=${eagerPdfFetches401.length}`,
      );
      await s4snap(page, 's4-desktop-401-before-expand');

      // Expande sub-collapse do relatório PDF recebido
      const docBtn = section401.locator('button[data-bs-target^="#reapp-doc-"]').first();
      const docTarget = await docBtn.getAttribute('data-bs-target');
      const docSrc = await page.locator(`${docTarget} embed.pdf-lazy-embed`).getAttribute('data-src');
      await docBtn.scrollIntoViewIfNeeded();
      const [docRes] = await Promise.all([
        page.waitForResponse(
          (res) => new URL(res.url()).pathname === docSrc && res.status() === 200,
          { timeout: 15000 },
        ),
        docBtn.click(),
      ]);
      await page.waitForFunction(
        (sel) => {
          const el = document.querySelector(sel);
          return el && el.classList.contains('show') && !el.classList.contains('collapsing');
        },
        docTarget,
        { timeout: 15000 },
      );
      s4check('s4-desktop-401-lazy-doc-pdf-200', docRes.status() === 200, `${docSrc} HTTP ${docRes.status()}`);
      // Fecha o primeiro sub-collapse antes de clicar no próximo para evitar que o plugin PDF do <embed> intercepte o clique.
      await docBtn.click();
      await page.waitForFunction(
        (sel) => {
          const el = document.querySelector(sel);
          return el && !el.classList.contains('show') && !el.classList.contains('collapsing');
        },
        docTarget,
        { timeout: 15000 },
      );

      // Expande sub-collapse do anexo PDF recebido
      const attBtn = section401.locator('button[data-bs-target^="#reapp-att-"]').first();
      const attTarget = await attBtn.getAttribute('data-bs-target');
      const attSrc = await page.locator(`${attTarget} embed.pdf-lazy-embed`).getAttribute('data-src');
      await attBtn.scrollIntoViewIfNeeded();
      const [attRes] = await Promise.all([
        page.waitForResponse(
          (res) => new URL(res.url()).pathname === attSrc && res.status() === 200,
          { timeout: 15000 },
        ),
        attBtn.click(),
      ]);
      s4check('s4-desktop-401-lazy-att-pdf-200', attRes.status() === 200, `${attSrc} HTTP ${attRes.status()}`);

      // Verifica imagem anexa carregada com naturalWidth > 0
      const imgEl = section401.locator('img').first();
      await imgEl.scrollIntoViewIfNeeded();
      await page.waitForFunction(
        () => {
          const img = document.querySelector('#reapresentacoes-recebidas img');
          return img && img.complete && img.naturalWidth > 0;
        },
        { timeout: 15000 },
      );
      const imgSrc = await imgEl.getAttribute('src');
      const imgNaturalWidth = await imgEl.evaluate((el) => el.naturalWidth);
      const imgResp = reappResponses.find((r) => r.pathname === imgSrc && r.status === 200);
      s4check(
        's4-desktop-401-image-att-200',
        !!imgResp && imgNaturalWidth > 0,
        `${imgSrc} HTTP ${imgResp?.status} naturalWidth=${imgNaturalWidth}`,
      );

      // Original case_pdf retorna 404
      const origPdfStatus401 = await page.evaluate(
        async (id) => (await fetch(`/scheduler/case/${id}/pdf/1/`, { cache: 'no-store' })).status,
        case401Id,
      );
      s4check('s4-desktop-401-original-pdf-404', origPdfStatus401 === 404, `HTTP ${origPdfStatus401}`);
      await s4snap(page, 's4-desktop-401-expanded');
      s4observed.steps.push({ step: 's4-desktop-401', url: case401Url });

      // ── 880402 (CLEANED retido histórico) ──
      await page.goto(`${base}/scheduler/historical/?q=880402`, { waitUntil: 'domcontentloaded' });
      const link402 = page.locator('a[href*="/scheduler/historical/"]:has-text("Ver detalhes")').first();
      s4check('s4-desktop-402-in-historical-search', (await link402.count()) === 1, await link402.getAttribute('href'));
      await link402.click();
      await page.waitForURL(/\/scheduler\/historical\/[0-9a-f-]{36}\//, { timeout: 15000 });
      await page.waitForLoadState('networkidle');
      case402Url = page.url().split('#')[0];
      case402Id = case402Url.match(/\/scheduler\/historical\/([0-9a-f-]{36})\//)?.[1] || '';

      const section402 = page.locator('#reapresentacoes-recebidas');
      s4check('s4-desktop-402-section-visible', (await section402.count()) === 1, case402Url);
      const body402 = await page.locator('body').innerText();
      s4check('s4-desktop-402-no-original-pdf-card', !body402.includes('Visualizar PDF Original'), 'PDF original oculto');

      const lazyPaths402 = await section402.locator('embed.pdf-lazy-embed').evaluateAll((els) =>
        els.map((el) => el.getAttribute('data-src')).filter(Boolean),
      );
      const eagerPdfFetches402 = reappResponses.filter((r) => lazyPaths402.includes(r.pathname));
      s4check('s4-desktop-402-zero-eager-pdf-fetches', lazyPaths402.length >= 1 && eagerPdfFetches402.length === 0, `eager=${eagerPdfFetches402.length}`);

      const doc402Btn = section402.locator('button[data-bs-target^="#reapp-doc-"]').first();
      const doc402Target = await doc402Btn.getAttribute('data-bs-target');
      const doc402Src = await page.locator(`${doc402Target} embed.pdf-lazy-embed`).getAttribute('data-src');
      await doc402Btn.scrollIntoViewIfNeeded();
      const [doc402Res] = await Promise.all([
        page.waitForResponse(
          (res) => new URL(res.url()).pathname === doc402Src && res.status() === 200,
          { timeout: 15000 },
        ),
        doc402Btn.click(),
      ]);
      s4check('s4-desktop-402-lazy-doc-pdf-200', doc402Res.status() === 200, `${doc402Src} HTTP ${doc402Res.status()}`);

      const origPdfStatus402 = await page.evaluate(
        async (id) => (await fetch(`/scheduler/case/${id}/pdf/1/`, { cache: 'no-store' })).status,
        case402Id,
      );
      s4check('s4-desktop-402-original-pdf-404', origPdfStatus402 === 404, `HTTP ${origPdfStatus402}`);
      await s4snap(page, 's4-desktop-402-historical');
      s4observed.steps.push({ step: 's4-desktop-402', url: case402Url });

      // ── 880403 (CLEANED minimizado/purgado histórico) ──
      await page.goto(`${base}/scheduler/historical/?q=880403`, { waitUntil: 'domcontentloaded' });
      const link403 = page.locator('a[href*="/scheduler/historical/"]:has-text("Ver detalhes")').first();
      s4check('s4-desktop-403-in-historical-search', (await link403.count()) === 1, await link403.getAttribute('href'));
      await link403.click();
      await page.waitForURL(/\/scheduler\/historical\/[0-9a-f-]{36}\//, { timeout: 15000 });
      const body403 = await page.locator('body').innerText();
      s4check(
        's4-desktop-403-unavailable-notice',
        body403.includes('Documentos removidos por minimização ou purga — indisponíveis para abertura.'),
        'aviso de indisponibilidade',
      );
      const reappLinks403 = await page.locator('a[href*="/reappearances/"], embed[data-src*="/reappearances/"]').count();
      s4check('s4-desktop-403-zero-reappearance-links', reappLinks403 === 0, `links=${reappLinks403}`);
      await s4snap(page, 's4-desktop-403-purged');
      s4observed.steps.push({ step: 's4-desktop-403', url: page.url() });

      await context.close();
    }

    // ── 2) Mobile 390×844 ──
    {
      const { context, page } = await s4loginAs(VIEWPORTS.mobile, 'verify-scheduler');
      const mobileReappResponses = [];
      page.on('response', (res) => {
        const u = new URL(res.url()).pathname;
        if (u.includes('/reappearances/')) {
          mobileReappResponses.push({ pathname: u, status: res.status() });
        }
      });

      await page.goto(case401Url, { waitUntil: 'networkidle' });
      const mobileEmbedPaths = await page.locator('#reapresentacoes-recebidas embed.pdf-lazy-embed').evaluateAll((els) =>
        els.map((el) => el.getAttribute('data-src')).filter(Boolean),
      );
      const mobileEagerFetches = mobileReappResponses.filter((r) => mobileEmbedPaths.includes(r.pathname));
      s4check('s4-mobile-401-zero-eager-embed-fetches', mobileEagerFetches.length === 0, `eager=${mobileEagerFetches.length}`);
      await s4snap(page, 's4-mobile-401-detail');

      // Viewer mobile do relatório PDF recebido + Voltar
      const docViewerLink = page.locator('#reapresentacoes-recebidas a[href*="/reappearances/"][href*="/documents/"][href*="/viewer/"]').first();
      await docViewerLink.scrollIntoViewIfNeeded();
      const [docViewerRes, docServeRes] = await Promise.all([
        page.waitForResponse(
          (res) => new URL(res.url()).pathname.includes('/reappearances/') && new URL(res.url()).pathname.endsWith('/viewer/') && res.status() === 200,
          { timeout: 15000 },
        ),
        page.waitForResponse(
          (res) => new URL(res.url()).pathname.includes('/reappearances/') && !new URL(res.url()).pathname.endsWith('/viewer/') && new URL(res.url()).pathname.includes('/documents/') && res.status() === 200,
          { timeout: 15000 },
        ),
        docViewerLink.click(),
      ]);
      await page.waitForURL(/\/viewer\//, { timeout: 15000 });
      s4check('s4-mobile-401-doc-viewer-200', docViewerRes.status() === 200 && docServeRes.status() === 200, `viewer=${docViewerRes.status()} serve=${docServeRes.status()}`);
      await s4snap(page, 's4-mobile-401-doc-viewer');

      await page.locator('a:has-text("Voltar")').first().click();
      await page.waitForURL(new RegExp(`/scheduler/case/${case401Id}/$`), { timeout: 15000 });
      s4check('s4-mobile-401-doc-viewer-back', page.url().includes(`/scheduler/case/${case401Id}/`), page.url());

      // Viewer mobile do anexo PDF recebido + Voltar
      const attViewerLink = page.locator('#reapresentacoes-recebidas a[href*="/reappearances/"][href*="/attachments/"][href*="/viewer/"]').first();
      await attViewerLink.scrollIntoViewIfNeeded();
      const [attServeRes] = await Promise.all([
        page.waitForResponse(
          (res) => new URL(res.url()).pathname.includes('/reappearances/') && !new URL(res.url()).pathname.endsWith('/viewer/') && new URL(res.url()).pathname.includes('/attachments/') && res.status() === 200,
          { timeout: 15000 },
        ),
        attViewerLink.click(),
      ]);
      await page.waitForURL(/\/viewer\//, { timeout: 15000 });
      s4check('s4-mobile-401-att-viewer-200', attServeRes.status() === 200, `serve=${attServeRes.status()}`);
      await s4snap(page, 's4-mobile-401-att-viewer');

      await page.locator('a:has-text("Voltar")').first().click();
      await page.waitForURL(new RegExp(`/scheduler/case/${case401Id}/$`), { timeout: 15000 });
      s4check('s4-mobile-401-att-viewer-back', page.url().includes(`/scheduler/case/${case401Id}/`), page.url());

      // Histórico 880402 no mobile → viewer mobile → Voltar
      await page.goto(case402Url, { waitUntil: 'domcontentloaded' });
      const histViewerLink = page.locator('#reapresentacoes-recebidas a[href*="/reappearances/"][href*="/documents/"][href*="/viewer/"]').first();
      await histViewerLink.scrollIntoViewIfNeeded();
      const [histServeRes] = await Promise.all([
        page.waitForResponse(
          (res) => new URL(res.url()).pathname.includes('/reappearances/') && !new URL(res.url()).pathname.endsWith('/viewer/') && new URL(res.url()).pathname.includes('/documents/') && res.status() === 200,
          { timeout: 15000 },
        ),
        histViewerLink.click(),
      ]);
      await page.waitForURL(/\/viewer\//, { timeout: 15000 });
      s4check('s4-mobile-402-hist-viewer-200', histServeRes.status() === 200, `serve=${histServeRes.status()}`);
      await page.locator('a:has-text("Voltar")').first().click();
      await page.waitForURL(new RegExp(`/scheduler/historical/${case402Id}/$`), { timeout: 15000 });
      s4check('s4-mobile-402-hist-viewer-back', page.url().includes(`/scheduler/historical/${case402Id}/`), page.url());
      await s4snap(page, 's4-mobile-402-back');

      await context.close();
    }

    // ── 3) Confirmação real de agendamento em 880401 (sem pergunta clínica/suspensão) ──
    {
      const { context, page } = await s4loginAs(VIEWPORTS.desktop, 'verify-scheduler');
      await page.goto(case401Url, { waitUntil: 'domcontentloaded' });
      const confirmForm = page.locator('form[action$="/confirm/"]');
      await confirmForm.locator('select[name="unit"]').selectOption('1');
      await confirmForm.locator('input[name="scheduled_date"]').fill('2026-12-15');
      await confirmForm.locator('input[name="scheduled_time"]').fill('10:30');
      await confirmForm.locator('input[name="scheduled_location"]').fill('Sala Hemodinamica 1');
      await confirmForm.getByRole('button', { name: 'Confirmar' }).click();
      await page.waitForLoadState('domcontentloaded');
      const postBody = await page.locator('body').innerText();
      s4check(
        's4-confirm-880401-success-no-clinical-gate',
        postBody.includes('Agendamento confirmado.') &&
          !postBody.toLowerCase().includes('revisão clínica') &&
          !postBody.toLowerCase().includes('suspenso'),
        page.url(),
      );
      await s4snap(page, 's4-confirm-880401');
      s4observed.steps.push({ step: 's4-confirm-880401', url: page.url() });
      await context.close();
    }
  } catch (err) {
    console.error(`infra/driver: ${err.message}`);
    writeFileSync(path.join(artifacts, 'observed-state-s4.json'), JSON.stringify(s4observed, null, 2));
    await browser.close();
    process.exit(2);
  }
  s4check('s4-no-failed-static-assets', s4failedAssets.length === 0, s4failedAssets.join('; ') || 'nenhum /static/* >= 400');
  writeFileSync(path.join(artifacts, 'report-s4.json'), JSON.stringify({ runId: manifest.runId, checks: s4checks, failures: s4failures, pass: s4failures.length === 0 }, null, 2));
  writeFileSync(path.join(artifacts, 'observed-state-s4.json'), JSON.stringify(s4observed, null, 2));
  await browser.close();
  console.log(s4failures.length === 0 ? 'DRIVE S4 PASS' : `DRIVE S4 FAIL: ${s4failures.join(', ')}`);
  process.exit(s4failures.length === 0 ? 0 : 1);
}
try {
  // 1) Médico: fila médica em leitura.
  {
    const { context, page } = await loginAs(browser, 'verify-doctor', 'doctor');
    check('doctor-home-url', page.url().includes('/doctor/'), page.url());
    check('doctor-queue-heading', await page.getByRole('heading', { name: 'Casos aguardando decisão' }).count() === 1);
    const body = await page.locator('body').innerText();
    check('doctor-sees-VERIFY-001', body.includes('VERIFY-001'), 'cartão sintético 001');
    check('doctor-sees-patient', body.includes('Paciente Sintético Um'), 'nome sintético');
    await snap(page, 'doctor-queue');
    observed.steps.push({ step: 'doctor-queue', url: page.url() });
    // Assets reais servidos pela instância: cada um precisa responder 200.
    for (const asset of REQUIRED_ASSETS) {
      const status = await page.evaluate(
        async (u) => (await fetch(u, { cache: 'no-store' })).status,
        asset,
      );
      check(`static-asset-${asset.split('/').pop()}`, status === 200, `${asset} HTTP ${status}`);
    }
    const card = page.locator('[data-doctor-queue-card] a.stretched-link').first();
    check('doctor-card-link', (await card.count()) === 1);
    await card.click();
    await page.waitForURL(/\/doctor\/case\//);
    check('doctor-case-read-url', page.url().includes('/doctor/case/'), page.url());
    await snap(page, 'doctor-case-read');
    observed.steps.push({ step: 'doctor-case-read', url: page.url() });
    await context.close();
  }

  // 2) Homes por papel (login real pelo form em cada um).
  const homes = [
    ['verify-nir', 'nir', '/intake/', 'Envio de relatório de regulação'],
    ['verify-scheduler', 'scheduler', '/scheduler/', 'Solicitações de agendamento aguardando confirmação'],
    ['verify-manager', 'manager', '/dashboard/', 'Painel gerencial'],
    ['verify-admin', 'admin', '/dashboard/', 'Painel gerencial'],
  ];
  for (const [user, label, urlPart, heading] of homes) {
    const { context, page } = await loginAs(browser, user, label);
    check(`${label}-home-url`, page.url().includes(urlPart), page.url());
    check(
      `${label}-heading`,
      (await page.getByRole('heading', { name: heading }).count()) >= 1,
      heading,
    );
    await snap(page, `${label}-home`);
    observed.steps.push({ step: `${label}-home`, url: page.url() });
    await context.close();
  }
} catch (err) {
  console.error(`infra/driver: ${err.message}`);
  writeFileSync(path.join(artifacts, 'observed-state.json'), JSON.stringify(observed, null, 2));
  await browser.close();
  process.exit(2);
}

check('no-failed-static-assets', failedAssets.length === 0, failedAssets.join('; ') || 'nenhum /static/* >= 400');

writeFileSync(
  path.join(artifacts, 'report.json'),
  JSON.stringify({ runId: manifest.runId, checks, failures, pass: failures.length === 0 }, null, 2),
);
writeFileSync(path.join(artifacts, 'observed-state.json'), JSON.stringify(observed, null, 2));
await browser.close();
console.log(failures.length === 0 ? 'DRIVE PASS' : `DRIVE FAIL: ${failures.join(', ')}`);
process.exit(failures.length === 0 ? 0 : 1);

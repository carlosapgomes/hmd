// Drive S2 da reapresentação médica (change reapresentacao-casos-filas,
// slice 002): original pendente → novo processado → original histórico
// vinculado → um card novo com espera herdada. Seed é setup (primitiva +
// pipeline + substituição reais); o navegador prova filas, detalhe,
// histórico e espera. Artefatos em <run>/artifacts/s2-* (sem sobrescrever
// report.json/observed-state.json do drive oficial).
// Exit: 0 PASS · 1 falha observada da aplicação · 2 bloqueador infra/driver.
import { execSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync, readlinkSync, writeFileSync } from 'node:fs';
import path from 'node:path';

import { chromium } from 'playwright';

const runArg = process.argv.indexOf('--run');
if (runArg < 0 || !process.argv[runArg + 1]) {
  console.error('uso: drive-reappearance-s2.mjs --run <tmp/verification/hmd-verify-...>');
  process.exit(2);
}
const runDir = path.resolve(process.argv[runArg + 1]);
const manifest = JSON.parse(readFileSync(path.join(runDir, 'manifest.json'), 'utf8'));
const base = manifest.url;
const artifacts = path.join(runDir, 'artifacts');
const PASSWORD = manifest.seedPassword;

if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base) || Number(new URL(base).port) !== manifest.port) {
  console.error(`infra: manifest.url=${base} não é a origem loopback verificada — recuso dirigir.`);
  process.exit(2);
}
if (!existsSync(artifacts)) {
  console.error(`infra: diretório de artefatos ausente em ${artifacts} — recuso dirigir.`);
  process.exit(2);
}

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
  const cmdline = readFileSync(`/proc/${pid}/cmdline`, 'utf8').replaceAll('\0', ' ');
  if (!cmdline.includes('config.settings.verify') || !cmdline.includes(`127.0.0.1:${manifest.port}`)) {
    refuseOwned(`PID ${pid} não é o runserver de verificação desta porta`);
  }
}
assertOwnedInstance();

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

async function loginAs(browser, username, label, viewport) {
  const context = await browser.newContext(viewport ? { viewport } : {});
  const page = await context.newPage();
  await page.goto(`${base}/login/`, { waitUntil: 'domcontentloaded' });
  await page.getByLabel('Usuário').fill(username);
  await page.getByLabel('Senha', { exact: true }).fill(PASSWORD);
  await page.getByRole('button', { name: 'Entrar' }).click();
  await page.waitForURL((url) => !url.pathname.endsWith('/login/'), { timeout: 15000 });
  return { context, page };
}

const VIEWPORTS = {
  desktop: { width: 1280, height: 800 },
  mobile: { width: 390, height: 844 },
};

let browser;
try {
  browser = await chromium.launch({ headless: true });
} catch (err) {
  console.error(`infra BLOCKED: Chromium pinado indisponível (${err.message}) — nenhum substituto.`);
  process.exit(2);
}

const observed = { runId: manifest.runId, base, database: manifest.database, steps: [] };
try {
  // 1) Médico: fila com UM card 880101 e espera herdada de 6 dias.
  for (const [label, viewport] of Object.entries(VIEWPORTS)) {
    const { context, page } = await loginAs(browser, 'verify-doctor', `doctor-${label}`, viewport);
    check(`s2-doctor-home-${label}`, page.url().includes('/doctor/'), page.url());
    const cards = page.locator('[data-doctor-queue-card][data-agency-record-number="880101"]');
    check(`s2-single-new-card-${label}`, (await cards.count()) === 1, `${await cards.count()} card(s) 880101`);
    const waiting = await cards.first().locator('.waiting-time').innerText();
    check(`s2-inherited-wait-${label}`, /6\s*dias/.test(waiting), waiting.trim());
    await snap(page, `s2-doctor-queue-${label}`);
    observed.steps.push({ step: `s2-doctor-queue-${label}`, url: page.url(), waiting: waiting.trim() });
    // Detalhe read-only do representante (sem claim de reserva: case_detail, não decide).
    const decideHref = await cards.first().locator('a.stretched-link').getAttribute('href');
    const m = decideHref && decideHref.match(/\/doctor\/case\/([0-9a-f-]{36})\//);
    check(`s2-card-href-${label}`, !!m, decideHref);
    if (m) {
      await page.goto(`${base}/doctor/case/${m[1]}/`, { waitUntil: 'domcontentloaded' });
      const detailBody = await page.locator('body').innerText();
      check(`s2-representative-detail-${label}`, detailBody.includes('880101'), page.url());
      await snap(page, `s2-doctor-detail-${label}`);
      observed.steps.push({ step: `s2-doctor-detail-${label}`, url: page.url() });
    }
    await context.close();
  }

  // 2) NIR: busca de encerrados → original histórico vinculado → representante.
  {
    const { context, page } = await loginAs(browser, 'verify-nir', 'nir');
    await page.goto(`${base}/intake/closed-cases/?q=880101`, { waitUntil: 'domcontentloaded' });
    const searchBody = await page.locator('body').innerText();
    check('s2-history-search-finds-original', searchBody.includes('880101'), 'nº 880101 na busca');
    await snap(page, 's2-nir-search');
    observed.steps.push({ step: 's2-nir-search', url: page.url() });
    const closedLink = page.locator('a.btn[href*="/intake/closed-cases/"]:has-text("Ver detalhes")').first();
    check('s2-history-link', (await closedLink.count()) >= 1);
    await closedLink.click();
    await page.waitForURL(/\/intake\/closed-cases\//);
    const historyBody = await page.locator('body').innerText();
    check('s2-history-substitution-card', historyBody.includes('Reapresentação do mesmo pedido'), 'card de vínculo');
    const immediateHref = await page.locator('dt:has-text("Substituído por") + dd a').getAttribute('href');
    const representativeHref = await page.locator('dt:has-text("Representante atual") + dd a').getAttribute('href');
    check('s2-immediate-link', !!immediateHref, immediateHref);
    check('s2-representative-link', !!representativeHref, representativeHref);
    // Cadeia de um salto (A→B): elo imediato e representante coincidem em B.
    // A cadeia A→B→C (elo ≠ representante) é provada pelo teste HTTP de
    // ``test_reappearance.py``; aqui o runtime prova o caso de um salto.
    check('s2-single-hop-chain', !!immediateHref && immediateHref === representativeHref);
    await snap(page, 's2-nir-history');
    observed.steps.push({ step: 's2-nir-history', url: page.url(), immediateHref, representativeHref });
    if (representativeHref) {
      await page.goto(`${base}${representativeHref}`, { waitUntil: 'domcontentloaded' });
      const repBody = await page.locator('body').innerText();
      check('s2-representative-active', page.url().includes('/intake/cases/') && repBody.includes('880101'), page.url());
      await snap(page, 's2-nir-representative');
      observed.steps.push({ step: 's2-nir-representative', url: page.url() });
    }
    await context.close();
  }
} catch (err) {
  console.error(`infra/driver: ${err.message}`);
  writeFileSync(path.join(artifacts, 'observed-state-s2.json'), JSON.stringify(observed, null, 2));
  await browser.close();
  process.exit(2);
}

writeFileSync(
  path.join(artifacts, 'report-s2.json'),
  JSON.stringify({ runId: manifest.runId, checks, failures, pass: failures.length === 0 }, null, 2),
);
writeFileSync(path.join(artifacts, 'observed-state-s2.json'), JSON.stringify(observed, null, 2));
await browser.close();
console.log(failures.length === 0 ? 'DRIVE S2 PASS' : `DRIVE S2 FAIL: ${failures.join(', ')}`);
process.exit(failures.length === 0 ? 0 : 1);

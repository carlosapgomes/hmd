// Drive Playwright pinado do verify-hmd — ui-parity-intake-detail slice 002.
// Jornada combobox-procedimento: login NIR real pelo form → combobox com
// filtro por label/código ignorando acentos, teclado ↑↓/Enter, edição e
// limpeza pós-seleção limpando o canônico, sem-match com mensagem, submit
// sem confirmar rejeitado com erro acessível, submit válido via combobox
// → sem JS o select nativo submete sozinho → reenvio corrigido de verdade
// no combobox (VERIFY-001 decidido/confirmado até CLEANED e reenviado).
// Tudo pela UI real, sem mutação direta de banco nem mudança de
// seed/backend. Evidência em <run>/artifacts/.
// Exit: 0 PASS · 1 falha observada · 2 infra/driver.
import { execSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync, readlinkSync, writeFileSync } from 'node:fs';
import path from 'node:path';

import { chromium } from 'playwright';

const runArg = process.argv.indexOf('--run');
if (runArg < 0 || !process.argv[runArg + 1]) {
  console.error('uso: drive-combobox-procedimento.mjs --run <tmp/verification/hmd-verify-...>');
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

const REQUIRED_ASSETS = ['/static/css/app.css', '/static/js/sw.js', '/static/js/intake-upload.js', '/static/js/procedure_combobox.js'];
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

// PDF mínimo válido (1 página com texto) — o processamento inline pode
// falhar depois (sem LLM no verify), mas a criação do caso é anterior.
function makePdf(label) {
  const stream = `BT /F1 24 Tf 72 720 Td (${label}) Tj ET`;
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>',
    `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`,
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
  ];
  let pdf = '%PDF-1.4\n';
  const offsets = [];
  objects.forEach((body, i) => {
    offsets.push(Buffer.byteLength(pdf));
    pdf += `${i + 1} 0 obj\n${body}\nendobj\n`;
  });
  const xref = Buffer.byteLength(pdf);
  pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  for (const off of offsets) pdf += `${String(off).padStart(10, '0')} 00000 n \n`;
  pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(pdf, 'utf8');
}
const PDF_A = { name: 'paciente-a.pdf', mimeType: 'application/pdf', buffer: makePdf('Paciente A') };
const PDF_CORRIGIDO = { name: 'corrigido.pdf', mimeType: 'application/pdf', buffer: makePdf('Corrigido') };

const COMBOBOX_INPUT = '#id_procedure_type-combobox';
const COMBOBOX_LIST = '#id_procedure_type-list';
const CANONICAL_SELECT = '#id_procedure_type';

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

async function canonicalValue(page) {
  return page.evaluate(() => document.querySelector('#id_procedure_type')?.value ?? null);
}
async function visibleOptions(page) {
  return page.evaluate(() => Array.from(document.querySelectorAll('#id_procedure_type-list li:not([hidden])')).map((el) => el.textContent));
}

let browser;
try {
  browser = await chromium.launch({ headless: true });
} catch (err) {
  console.error(`infra BLOCKED: Chromium pinado do Playwright indisponível (${err.message}).`);
  process.exit(2);
}

const observed = { runId: manifest.runId, base, database: manifest.database, steps: [] };
let resubmitCaseId = null;
try {
  // ── R1/R3 com JS: filtro por label/código, teclado, edição limpa ──
  {
    const { context, page } = await loginAs(browser, 'verify-nir', 'nir');
    check('nir-combobox-url', page.url().includes('/intake/'), page.url());
    check('combobox-input-visible', await page.locator(COMBOBOX_INPUT).isVisible());
    check('combobox-role', (await page.locator(COMBOBOX_INPUT).getAttribute('role')) === 'combobox');
    check('combobox-select-hidden-but-present', (await page.locator(CANONICAL_SELECT).count()) === 1);
    check('no-radios', (await page.locator('input[name="procedure_type"][type="radio"]').count()) === 0);
    check('combobox-hint-visible', await page.locator('#procedure-type-search-hint').isVisible());
    const hintText = await page.locator('#procedure-type-search-hint').innerText();
    check('combobox-hint-no-synonyms', !hintText.toLowerCase().includes('sinônimo'), hintText);
    for (const asset of REQUIRED_ASSETS) {
      const status = await page.evaluate(async (u) => (await fetch(u, { cache: 'no-store' })).status, asset);
      check(`static-asset-${asset.split('/').pop()}`, status === 200, `${asset} HTTP ${status}`);
    }
    await snap(page, 'nir-combobox-before');

    // Filtro por label sem acento: "cardiaco" → só Cateterismo cardíaco.
    const input = page.locator(COMBOBOX_INPUT);
    await input.click();
    await input.pressSequentially('cardiaco', { delay: 20 });
    await page.waitForFunction(() => document.querySelector('#id_procedure_type-list') && !document.querySelector('#id_procedure_type-list').hidden);
    const filtered = await visibleOptions(page);
    check('combobox-filter-label-no-accent', filtered.length === 1 && filtered[0].includes('Cateterismo'), filtered.join(' | '));
    check('combobox-expanded', (await input.getAttribute('aria-expanded')) === 'true');
    check('combobox-activedescendant', !!(await input.getAttribute('aria-activedescendant')));
    await snap(page, 'nir-combobox-filtered');

    // Teclado: Enter confirma a opção ativa → canônico + texto espelhado.
    await input.press('Enter');
    check('combobox-keyboard-select-canonical', (await canonicalValue(page)) === 'cat_cardiaco');
    check('combobox-keyboard-select-visible-text', (await input.inputValue()) === 'Cateterismo cardíaco');
    check('combobox-selected-row', await page.evaluate(() => document.querySelector('.procedure-combobox__option--selected')?.getAttribute('data-value') === 'cat_cardiaco'));
    await snap(page, 'nir-combobox-selected');

    // Edição pós-seleção limpa o canônico (nunca diverge).
    await input.pressSequentially(' x', { delay: 20 });
    check('combobox-edit-clears-canonical', (await canonicalValue(page)) === '');
    await input.fill('');
    check('combobox-clear-clears-canonical', (await canonicalValue(page)) === '');
    await snap(page, 'nir-combobox-edit-cleared');

    // Filtro por código canônico + clique na opção.
    await input.pressSequentially('nefrostomia', { delay: 20 });
    const byCode = await visibleOptions(page);
    check('combobox-filter-by-code', byCode.length === 1 && byCode[0].includes('Nefrostomia'), byCode.join(' | '));
    await page.locator(`${COMBOBOX_LIST} li:not([hidden])`).first().click();
    check('combobox-click-selects-canonical', (await canonicalValue(page)) === 'nefrostomia');
    check('combobox-click-visible-text', (await input.inputValue()) === 'Nefrostomia percutânea');

    // Sem match: mensagem textual e canônico vazio.
    await input.fill('');
    await input.pressSequentially('stent', { delay: 20 });
    check('combobox-nomatch-message', (await page.locator('#id_procedure_type-empty').innerText()).includes('Nenhum procedimento encontrado'));
    check('combobox-nomatch-canonical-empty', (await canonicalValue(page)) === '');
    await snap(page, 'nir-combobox-nomatch');

    // Texto digitado sem confirmar não submete valor (R2).
    await input.fill('');
    await input.pressSequentially('cateterismo', { delay: 20 });
    check('combobox-unconfirmed-canonical-empty', (await canonicalValue(page)) === '');
    await page.locator('#id_documents').setInputFiles([PDF_A]);
    await page.getByRole('button', { name: 'Criar caso' }).click();
    await page.waitForLoadState('domcontentloaded');
    const unconfirmedBody = await page.locator('body').innerText();
    check('combobox-unconfirmed-submit-rejected', unconfirmedBody.includes('Este campo é obrigatório.') && !page.url().includes('/intake/cases/'), page.url());
    check('combobox-error-associated', await page.evaluate(() => {
      const sel = document.querySelector('#id_procedure_type');
      return sel?.getAttribute('aria-invalid') === 'true' && (sel?.getAttribute('aria-describedby') || '').includes('id_procedure_type_error')
        && !!document.querySelector('#id_procedure_type_error[role="alert"]');
    }));
    check('combobox-input-inherits-error-aria', await page.evaluate(() => {
      const inp = document.querySelector('#id_procedure_type-combobox');
      return inp?.getAttribute('aria-invalid') === 'true' && (inp?.getAttribute('aria-describedby') || '').includes('id_procedure_type_error');
    }));
    await snap(page, 'nir-combobox-unconfirmed-error');
    observed.steps.push({ step: 'nir-combobox-unconfirmed', url: page.url() });

    // Submit válido via combobox (reconfirma pelo teclado). O POST do
    // erro anterior limpou o input de arquivos (o navegador nunca
    // repovoa file inputs) — por isso o PDF é selecionado de novo.
    await page.locator('#id_documents').setInputFiles([PDF_A]);
    await input.fill('');
    await input.pressSequentially('cardiaco', { delay: 20 });
    await input.press('Enter');
    check('combobox-reconfirm-canonical', (await canonicalValue(page)) === 'cat_cardiaco');
    await page.getByRole('button', { name: 'Criar caso' }).click();
    await page.waitForURL(/\/intake\/cases\//, { timeout: 20000 });
    check('combobox-valid-submit-creates-case', page.url().includes('/intake/cases/'), page.url());
    const detailBody = await page.locator('body').innerText();
    check('combobox-case-lists-pdf', detailBody.includes('paciente-a.pdf'), 'detalhe lista o PDF');
    await snap(page, 'nir-combobox-submitted-detail');
    observed.steps.push({ step: 'nir-combobox-submitted', url: page.url() });
    await context.close();
  }

  // ── R2 sem JS: o select nativo submete sozinho ──
  {
    const { context, page } = await loginAs(browser, 'verify-nir', 'nirnojs', { javaScriptEnabled: false });
    check('nojs-combobox-url', page.url().includes('/intake/'), page.url());
    check('nojs-select-visible', await page.locator(CANONICAL_SELECT).isVisible());
    check('nojs-select-accessible-name', (await page.getByRole('combobox', { name: 'Tipo de procedimento' }).count()) === 1);
    check('nojs-no-combobox-input', (await page.locator(COMBOBOX_INPUT).count()) === 0);
    await snap(page, 'nir-combobox-nojs-before');
    await page.locator('#id_documents').setInputFiles([PDF_A]);
    await page.locator(CANONICAL_SELECT).selectOption('cat_cardiaco');
    await page.getByRole('button', { name: 'Criar caso' }).click();
    await page.waitForLoadState('domcontentloaded');
    const nojsBody = await page.locator('body').innerText();
    check('nojs-native-submit-creates-case', nojsBody.includes('criado com sucesso') || page.url().includes('/intake/cases/'), page.url());
    await snap(page, 'nir-combobox-nojs-after');
    observed.steps.push({ step: 'nir-combobox-nojs', url: page.url() });
    await context.close();
  }

  // ── P1-A/P1-B (fix-1): nome acessível, clique no label e scroll ──
  // do ativo até a última opção (End) — bloco isolado para não
  // perturbar os fluxos acima; o nome é provado pelo papel `combobox`
  // com nome, o clique no label foca o input e o End arma a última
  // opção já visível na listbox (commit com Enter).
  {
    const { context, page } = await loginAs(browser, 'verify-nir', 'nir-label-scroll');
    check('label-combobox-accessible-name', (await page.getByRole('combobox', { name: 'Tipo de procedimento' }).count()) === 1);
    check('label-aria-labelledby-points-to-label', await page.evaluate(() => {
      const inp = document.querySelector('#id_procedure_type-combobox');
      const label = document.querySelector('label[for="id_procedure_type"]');
      return !!inp && !!label && inp.getAttribute('aria-labelledby') === label.id && (label.textContent || '').includes('Tipo de procedimento');
    }));
    check('label-native-for-retained', await page.evaluate(() => document.querySelector('label[for="id_procedure_type"]')?.id === 'id_procedure_type_label'));
    await page.locator('label[for="id_procedure_type"]').click();
    check('label-click-focuses-combobox-input', await page.evaluate(() => document.activeElement?.id === 'id_procedure_type-combobox'));
    check('label-click-opens-listbox', (await page.locator(COMBOBOX_INPUT).getAttribute('aria-expanded')) === 'true');
    const input = page.locator(COMBOBOX_INPUT);
    await input.press('End');
    const endState = await page.evaluate(() => {
      const list = document.querySelector('#id_procedure_type-list');
      const activeId = document.querySelector('#id_procedure_type-combobox')?.getAttribute('aria-activedescendant');
      const active = activeId ? document.getElementById(activeId) : null;
      if (!list || !active) return { ok: false, detail: 'sem ativo' };
      const listRect = list.getBoundingClientRect();
      const activeRect = active.getBoundingClientRect();
      const visible = activeRect.top >= listRect.top - 1 && activeRect.bottom <= listRect.bottom + 1 && activeRect.top < listRect.bottom && activeRect.bottom > listRect.top;
      return { ok: visible, detail: `${activeId} value=${active.getAttribute('data-value')} listTop=${listRect.top} activeTop=${activeRect.top} activeBottom=${activeRect.bottom} listBottom=${listRect.bottom}` };
    });
    check('end-active-row-visible-in-listbox', endState.ok, endState.detail);
    check('end-arms-last-option', await page.evaluate(() => document.querySelector('#id_procedure_type-combobox')?.getAttribute('aria-activedescendant')?.includes('nefrostomia') || document.querySelector('#id_procedure_type-combobox')?.getAttribute('aria-activedescendant') === document.querySelector('#id_procedure_type-list li:last-child')?.id));
    await snap(page, 'nir-combobox-end-visible');
    await input.press('Enter');
    check('end-enter-commits-last-option', (await canonicalValue(page)) === 'nefrostomia');
    await snap(page, 'nir-combobox-label-scroll');
    observed.steps.push({ step: 'nir-combobox-label-scroll', url: page.url() });
    await context.close();
  }

  // ── R2 no reenvio: combobox do reenvio corrigido (UI real) ──
  {
    const { context, page } = await loginAs(browser, 'verify-doctor', 'doctor-resub');
    await page.locator('[data-doctor-queue-card] a.stretched-link').first().waitFor({ timeout: 15000 }).catch(() => {});
    const doctorBody = await page.locator('body').innerText();
    check('resubmit-doctor-sees-VERIFY-001', doctorBody.includes('VERIFY-001'), 'cartão sintético 001');
    const decideHref = await page.locator('[data-agency-record-number="VERIFY-001"] a.stretched-link').getAttribute('href').catch(() => null);
    const decideMatch = (decideHref || '').match(/\/doctor\/case\/([0-9a-f-]+)\/decide\//);
    check('resubmit-cleaned-case-found-in-queue', !!decideMatch, decideHref || 'sem link');
    if (decideMatch) {
      resubmitCaseId = decideMatch[1];
      await page.goto(`${base}/doctor/case/${resubmitCaseId}/decide/`, { waitUntil: 'domcontentloaded' });
      await snap(page, 'doctor-resubmit-decide');
      const dispositions = page.locator('select[name$="__disposition"]');
      const reasons = page.locator('textarea[name$="__reason"]');
      const dispositionCount = await dispositions.count();
      const reasonCount = await reasons.count();
      check('resubmit-decide-procedures-present', dispositionCount >= 1 && reasonCount >= 1, `${dispositionCount} decisões, ${reasonCount} motivos`);
      for (let i = 0; i < dispositionCount; i++) {
        await dispositions.nth(i).selectOption('denied');
      }
      for (let i = 0; i < reasonCount; i++) {
        await reasons.nth(i).fill('motivo sintético da jornada de verificação');
      }
      await page.getByRole('button', { name: 'Registrar decisão' }).click();
      await page.waitForURL(new RegExp(`/doctor/case/${resubmitCaseId}/`), { timeout: 20000 }).catch(() => {});
      await snap(page, 'doctor-resubmit-decided');
      observed.steps.push({ step: 'doctor-resubmit-decided', url: page.url() });
    }
    await context.close();
  }
  {
    if (!resubmitCaseId) {
      check('resubmit-nir-cleaned', false, 'caseId não encontrado na fila');
      check('resubmit-combobox-submit', false, 'caseId não encontrado na fila');
    } else {
      const { context, page } = await loginAs(browser, 'verify-nir', 'nir-resubmit');
      await page.goto(`${base}/intake/cases/${resubmitCaseId}/`, { waitUntil: 'domcontentloaded' });
      const ackButton = page.getByRole('button', { name: 'Confirmar recebimento' });
      check('resubmit-nir-ack-visible', (await ackButton.count()) === 1);
      await snap(page, 'nir-resubmit-before-ack');
      await ackButton.click();
      await page.waitForLoadState('domcontentloaded');
      const cleanedLinkCount = await page.locator('a[href*="/resubmit/"]').count();
      check('resubmit-nir-cleaned', cleanedLinkCount >= 1, page.url());
      await snap(page, 'nir-resubmit-cleaned');
      observed.steps.push({ step: 'nir-resubmit-cleaned', url: page.url() });
      await page.goto(`${base}/intake/cases/${resubmitCaseId}/resubmit/`, { waitUntil: 'domcontentloaded' });
      check('resubmit-combobox-input-visible', await page.locator(COMBOBOX_INPUT).isVisible());
      check('resubmit-combobox-accessible-name', (await page.getByRole('combobox', { name: 'Tipo de procedimento' }).count()) === 1);
      check('resubmit-label-click-focuses-input', await page.evaluate(async () => { const label = document.querySelector('label[for="id_procedure_type"]'); if (!label) return false; label.click(); return document.activeElement?.id === 'id_procedure_type-combobox'; }));
      check('resubmit-no-radios', (await page.locator('input[name="procedure_type"][type="radio"]').count()) === 0);
      check('resubmit-hint-visible', await page.locator('#procedure-type-search-hint').isVisible());
      await snap(page, 'nir-resubmit-form');
      await page.locator('#id_documents').setInputFiles([PDF_CORRIGIDO]);
      await page.locator('textarea[name="correction_reason"]').fill('laudo ilegível, reenvio com foto do exame');
      const input = page.locator(COMBOBOX_INPUT);
      await input.click();
      await input.pressSequentially('nefrostomia', { delay: 20 });
      const byCode = await visibleOptions(page);
      check('resubmit-combobox-filter-by-code', byCode.length === 1 && byCode[0].includes('Nefrostomia'), byCode.join(' | '));
      await input.press('Enter');
      check('resubmit-combobox-canonical', (await canonicalValue(page)) === 'nefrostomia');
      await snap(page, 'nir-resubmit-combobox');
      await page.getByRole('button', { name: 'Criar reenvio corrigido' }).click();
      await page.waitForURL(/\/intake\/cases\/[0-9a-f-]+\/?$/, { timeout: 20000 }).catch(() => {});
      const resubmitBody = await page.locator('body').innerText();
      const landedOnDetail = page.url().includes('/intake/cases/') && !page.url().includes('/resubmit/');
      check('resubmit-combobox-submit', landedOnDetail, page.url());
      check('resubmit-new-case-lists-corrected-pdf', resubmitBody.includes('corrigido.pdf'), 'detalhe lista o corrigido');
      check('resubmit-new-case-is-different', !page.url().includes(resubmitCaseId), page.url());
      await snap(page, 'nir-resubmit-submitted-detail');
      observed.steps.push({ step: 'nir-resubmit-submitted', url: page.url() });
      await context.close();
    }
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

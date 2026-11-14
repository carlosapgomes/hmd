// Drive Playwright pinado do verify-hmd — ui-parity-intake-detail slice 001.
// Jornada upload-dragdrop: login NIR real pelo form → zona/preview com
// seleção nativa e drag-and-drop simulado no browser (DataTransfer) →
// toggle de anexos 1→2→1 → o ARRASTADO é submetido (fica no preview
// até o submit e o caso criado lista o nome dele) → submit com anexo
// SEM confirmação (R4) → lote misto com erro server-side nomeado,
// incluindo não-PDF pela extensão (P1-A: o cliente nunca exclui) →
// anexo inválido com 1 PDF aborta no servidor (nenhum caso) → nomes
// duplicados submetidos arquivo a arquivo → lote acima do limite
// rejeitado inteiro pelo servidor → sem JS o form nativo submete o
// mesmo lote multi-arquivo (R2) → 1 relatório + anexo e arrastar o 2º
// relatório: o anexo JÁ selecionado é submetido com >1 relatório (P1-1,
// fix-2) e o servidor cria os casos sem anexos com o erro não-bloqueante
// nomeado → reenvio corrigido de verdade (P1-2, fix-2): o VERIFY-001
// sintético (AWAITING_DOCTOR no seed) é decidido (negado), confirmado
// pelo NIR até CLEANED e reenviado no browser (widget + submit sem
// confirmação) — tudo pela UI real, sem mutação direta de banco nem
// mudança de seed/backend. O anexo inválido do reenvio segue por
// paridade pytest. Evidência em <run>/artifacts/.
// Exit: 0 PASS · 1 falha observada · 2 infra/driver.
import { execSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync, readlinkSync, writeFileSync } from 'node:fs';
import path from 'node:path';

import { chromium } from 'playwright';

const runArg = process.argv.indexOf('--run');
if (runArg < 0 || !process.argv[runArg + 1]) {
  console.error('uso: drive-upload-dragdrop.mjs --run <tmp/verification/hmd-verify-...>');
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

const REQUIRED_ASSETS = ['/static/css/app.css', '/static/js/sw.js', '/static/js/intake-upload.js'];
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
const PDF_B = { name: 'paciente-b.pdf', mimeType: 'application/pdf', buffer: makePdf('Paciente B') };
const PDF_ARRASTADO = { name: 'arrastado.pdf', mimeType: 'application/pdf', buffer: makePdf('Arrastado') };
// Nomes duplicados (conteúdo distinto): o cliente não colapsa — o
// servidor processa arquivo a arquivo, um caso por válido (R1).
const PDF_DUP_A = { name: 'relatorio.pdf', mimeType: 'application/pdf', buffer: makePdf('Duplicado A') };
const PDF_DUP_B = { name: 'relatorio.pdf', mimeType: 'application/pdf', buffer: makePdf('Duplicado B') };
// Passa na extensão client-side (.pdf) mas falha no servidor
// (content-type) — é assim que o lote misto server-side é alcançável
// no browser: o servidor é a autoridade para o conteúdo (R1).
const JPG_AS_DOC = { name: 'invalido.pdf', mimeType: 'image/jpeg', buffer: Buffer.from('jpeg fake') };
// Não-PDF pela extensão: o cliente antigo filtrava antes do submit;
// agora o arquivo é submetido e o servidor o nomeia (P1-A).
const JPG_DOC = { name: 'foto.jpg', mimeType: 'image/jpeg', buffer: Buffer.from('jpeg fake') };
const ATTACHMENT = { name: 'foto.jpg', mimeType: 'image/jpeg', buffer: Buffer.from('jpeg fake anexo') };
// Anexo com MIME não aceito: com 1 PDF, o servidor ABORTA o envio —
// filtrá-lo no cliente permitiria a criação (specs/attachments).
const ATTACHMENT_GIF = { name: 'foto.gif', mimeType: 'image/gif', buffer: Buffer.from('gif fake anexo') };
// Reenvio corrigido (P1-2): PDF corrigido válido gerado no drive.
const PDF_CORRIGIDO = { name: 'corrigido.pdf', mimeType: 'application/pdf', buffer: makePdf('Corrigido') };

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


// P2 (slice-002 fix-1): o radio de procedure_type foi removido —
// jornadas com JS usam o combobox (digita o código + Enter) e a
// jornada sem JS usa o select nativo. Migração só de seletores.
async function selectProcedureTypeViaCombobox(page) {
  const input = page.locator('#id_procedure_type-combobox');
  await input.click();
  await input.pressSequentially('art_perif', { delay: 20 });
  await input.press('Enter');
  const value = await page.evaluate(() => document.querySelector('#id_procedure_type')?.value ?? null);
  if (value !== 'art_perif') {
    throw new Error(`combobox não confirmou art_perif (canônico=${value})`);
  }
}
async function selectProcedureTypeNative(page) {
  await page.locator('#id_procedure_type').selectOption('art_perif');
}

let browser;
try {
  browser = await chromium.launch({ headless: true });
} catch (err) {
  console.error(`infra BLOCKED: Chromium pinado do Playwright indisponível (${err.message}).`);
  process.exit(2);
}

const observed = { runId: manifest.runId, base, database: manifest.database, steps: [] };
let createdCaseUrl = null;
let resubmitCaseId = null;
try {
  // ── R1/R4 com JS: preview, drag-drop, toggle de anexos e submit ──
  {
    const { context, page } = await loginAs(browser, 'verify-nir', 'nir');
    check('nir-upload-url', page.url().includes('/intake/'), page.url());
    check('nir-upload-heading', (await page.getByRole('heading', { name: 'Envio de relatório de regulação' }).count()) >= 1);
    check('upload-zone-visible', await page.locator('#upload-zone').isVisible());
    check('upload-preview-hidden-initially', await page.locator('#upload-preview').isHidden());
    check('documents-input-visible-nojs', await page.locator('#id_documents').isVisible());
    await snap(page, 'nir-upload-before');
    for (const asset of REQUIRED_ASSETS) {
      const status = await page.evaluate(async (u) => (await fetch(u, { cache: 'no-store' })).status, asset);
      check(`static-asset-${asset.split('/').pop()}`, status === 200, `${asset} HTTP ${status}`);
    }

    // Seleção nativa de 2 PDFs → preview com lista, contagem e tamanho.
    await page.locator('#id_documents').setInputFiles([PDF_A, PDF_B]);
    await page.locator('#upload-preview').waitFor({ state: 'visible' });
    check('preview-batch-count-2', (await page.locator('#batch-count').innerText()).includes('2 arquivo'));
    check('preview-file-list-2', (await page.locator('#file-list li').count()) === 2);
    check('preview-total-size-shown', (await page.locator('#batch-total-size').innerText()).length > 0);
    check('attachments-disabled-with-2', await page.locator('#id_attachments').isDisabled());
    check('attachments-hint-visible-with-2', await page.locator('#intake-attachments-single-hint').isVisible());
    await snap(page, 'nir-upload-preview-2');

    // Remover um → 1 arquivo, anexos reabilitam (toggle 2→1).
    await page.locator('.btn-remove-file').first().click();
    await page.waitForFunction(() => document.querySelector('#batch-count')?.textContent.includes('1 arquivo'));
    check('preview-batch-count-1-after-remove', true);
    check('attachments-enabled-with-1', await page.locator('#id_attachments').isEnabled());
    check('attachments-hint-hidden-with-1', await page.locator('#intake-attachments-single-hint').isHidden());

    // Drag-and-drop: drop com DataTransfer no browser → entra no preview.
    await page.evaluate(() => {
      const zone = document.getElementById('upload-zone');
      const dt = new DataTransfer();
      dt.items.add(new File(['%PDF-1.4 fake'], 'arrastado.pdf', { type: 'application/pdf' }));
      zone.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: dt }));
      zone.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: dt }));
    });
    await page.waitForFunction(() => document.querySelector('#batch-count')?.textContent.includes('2 arquivo'));
    const listText = await page.locator('#file-list').innerText();
    check('dragdrop-added-to-preview', listText.includes('arrastado.pdf'), listText.replace(/\n/g, ' | '));
    await snap(page, 'nir-upload-dragdrop');

    // O arrastado é o submetido: remove o OUTRO item (arrastado é o
    // último) e mantém o arrastado no preview até o submit.
    await page.locator('.btn-remove-file').first().click();
    await page.waitForFunction(() => document.querySelector('#batch-count')?.textContent.includes('1 arquivo'));
    const listAfterRemove = await page.locator('#file-list').innerText();
    check('dragged-kept-in-preview-through-submit', listAfterRemove.includes('arrastado.pdf'), listAfterRemove.replace(/\n/g, ' | '));
    const submittedNames = await page.evaluate(() => Array.from(document.getElementById('id_documents').files).map((f) => f.name));
    check('dragged-synced-to-input', submittedNames.includes('arrastado.pdf'), submittedNames.join(', '));

    // Anexo com 1 relatório + submit SEM confirmação (R4).
    await page.locator('#id_attachments').setInputFiles([ATTACHMENT]);
    await page.locator('#attachment-preview').waitFor({ state: 'visible' });
    check('attachment-preview-1', (await page.locator('#attachment-count').innerText()).includes('1 anexo'));
    check('no-confirmation-checkbox', (await page.locator('#attachment-confirm').count()) === 0);
    await selectProcedureTypeViaCombobox(page);
    await page.getByRole('button', { name: 'Criar caso' }).click();
    await page.waitForURL(/\/intake\/cases\//, { timeout: 20000 });
    createdCaseUrl = page.url();
    check('submit-with-attachment-no-confirmation', true, createdCaseUrl);
    const detailBody = await page.locator('body').innerText();
    check('created-case-is-dragged-file', detailBody.includes('arrastado.pdf'), 'detalhe lista o arrastado');
    await snap(page, 'nir-upload-submitted-detail');
    observed.steps.push({ step: 'nir-upload-submitted', url: createdCaseUrl });

    // R3 (gate): reenvio só com caso CLEANED — o caso novo não é CLEANED,
    // então a URL de reenvio redireciona ao detalhe (widget em pytest).
    const caseId = createdCaseUrl.split('/intake/cases/')[1]?.split('/')[0];
    if (caseId) {
      await page.goto(`${base}/intake/cases/${caseId}/resubmit/`, { waitUntil: 'domcontentloaded' });
      check('resubmit-gated-non-cleaned', page.url().includes(`/intake/cases/${caseId}`) && !page.url().includes('/resubmit/'), page.url());
      await snap(page, 'nir-resubmit-gated');
    } else {
      check('resubmit-gated-non-cleaned', false, 'caseId não extraído');
    }
    await context.close();
  }

  // ── R1 lote misto: válido cria, inválidos são nomeados, contexto fica ──
  {
    const { context, page } = await loginAs(browser, 'verify-nir', 'nir2');
    // Inclui não-PDF pela extensão (foto.jpg): o cliente antigo o
    // filtrava; agora ele é submetido e o servidor o nomeia (P1-A).
    await page.locator('#id_documents').setInputFiles([PDF_A, JPG_AS_DOC, JPG_DOC]);
    await selectProcedureTypeViaCombobox(page);
    await page.getByRole('button', { name: 'Criar caso' }).click();
    await page.waitForLoadState('domcontentloaded');
    const body = await page.locator('body').innerText();
    // Lote com erro → página de resultado na própria home (sem redirect),
    // com a contagem de criados e os erros nomeando os inválidos.
    check('mixed-batch-1-created', body.includes('1 caso criado'), page.url());
    check('mixed-batch-invalid-named', body.includes('invalido.pdf'), 'erro nomeia o inválido');
    check('mixed-batch-ext-filtered-invalid-named', body.includes('foto.jpg'), 'erro nomeia o filtrado por extensão');
    check('mixed-batch-context-preserved', body.includes('Envio de relatório de regulação'), 'contexto do envio preservado');
    await snap(page, 'nir-upload-mixed-result');
    observed.steps.push({ step: 'nir-upload-mixed', url: page.url() });
    await context.close();
  }

  // ── R1/P1-A anexo inválido com 1 PDF aborta o envio inteiro ──
  {
    const { context, page } = await loginAs(browser, 'verify-nir', 'nir3');
    await page.locator('#id_documents').setInputFiles([PDF_A]);
    await page.locator('#id_attachments').setInputFiles([ATTACHMENT_GIF]);
    // O cliente mantém o anexo inválido no preview (P1-A) — filtrá-lo
    // permitiria a criação que specs/attachments proíbe.
    await page.locator('#attachment-preview').waitFor({ state: 'visible' });
    check('invalid-attachment-kept-in-preview', (await page.locator('#attachment-count').innerText()).includes('1 anexo'));
    await selectProcedureTypeViaCombobox(page);
    await page.getByRole('button', { name: 'Criar caso' }).click();
    await page.waitForLoadState('domcontentloaded');
    const body = await page.locator('body').innerText();
    const aborted = !page.url().includes('/intake/cases/') && body.includes('foto.gif');
    check('invalid-attachment-aborts-no-case', aborted, page.url());
    check('invalid-attachment-zero-created', body.includes('0 casos criados') || body.includes('foto.gif'), 'nada criado');
    await snap(page, 'nir-upload-invalid-attachment');
    observed.steps.push({ step: 'nir-upload-invalid-attachment', url: page.url() });
    await context.close();
  }

  // ── R1 nomes duplicados: submetidos arquivo a arquivo ──
  {
    const { context, page } = await loginAs(browser, 'verify-nir', 'nir4');
    await page.locator('#id_documents').setInputFiles([PDF_DUP_A, PDF_DUP_B]);
    await page.locator('#upload-preview').waitFor({ state: 'visible' });
    check('duplicates-both-in-preview', (await page.locator('#file-list li').count()) === 2);
    const dupList = await page.locator('#file-list').innerText();
    check('duplicates-named-in-preview', (dupList.match(/relatorio\.pdf/g) || []).length >= 2, dupList.replace(/\n/g, ' | '));
    await selectProcedureTypeViaCombobox(page);
    await page.getByRole('button', { name: 'Criar caso' }).click();
    await page.waitForLoadState('domcontentloaded');
    const body = await page.locator('body').innerText();
    check('duplicates-2-cases-created', body.includes('2 casos criados'), page.url());
    await snap(page, 'nir-upload-duplicates-result');
    observed.steps.push({ step: 'nir-upload-duplicates', url: page.url() });
    await context.close();
  }

  // ── R1/P1-A lote acima do limite: rejeição do lote inteiro ──
  {
    const { context, page } = await loginAs(browser, 'verify-nir', 'nir5');
    const lote = Array.from({ length: 31 }, (_, i) => ({
      name: `lote-${i + 1}.pdf`,
      mimeType: 'application/pdf',
      buffer: makePdf(`Lote ${i + 1}`),
    }));
    await page.locator('#id_documents').setInputFiles(lote);
    await page.locator('#upload-preview').waitFor({ state: 'visible' });
    check('overlimit-all-in-preview', (await page.locator('#file-list li').count()) === 31);
    const overlimitCount = await page.evaluate(() => document.getElementById('id_documents').files.length);
    check('overlimit-all-synced-to-input', overlimitCount === 31, String(overlimitCount));
    await selectProcedureTypeViaCombobox(page);
    await page.getByRole('button', { name: 'Criar caso' }).click();
    await page.waitForLoadState('domcontentloaded');
    const body = await page.locator('body').innerText();
    check('overlimit-batch-rejected-whole', body.includes('0 casos criados') && body.includes('Máximo de 30'), 'lote inteiro rejeitado, nada criado');
    await snap(page, 'nir-upload-overlimit');
    observed.steps.push({ step: 'nir-upload-overlimit', url: page.url() });
    await context.close();
  }

  // ── R2 sem JS: o form nativo submete o mesmo lote multi-arquivo ──
  {
    const { context, page } = await loginAs(browser, 'verify-nir', 'nirnojs', { javaScriptEnabled: false });
    check('nojs-upload-url', page.url().includes('/intake/'), page.url());
    check('nojs-documents-visible', await page.locator('#id_documents').isVisible());
    await snap(page, 'nir-upload-nojs-before');
    await page.locator('#id_documents').setInputFiles([PDF_A, PDF_B]);
    await selectProcedureTypeNative(page);
    await page.getByRole('button', { name: 'Criar caso' }).click();
    await page.waitForLoadState('domcontentloaded');
    const body = await page.locator('body').innerText();
    check('nojs-native-submit-multifile', body.includes('2 casos criados'), page.url());
    await snap(page, 'nir-upload-nojs-after');
    observed.steps.push({ step: 'nir-upload-nojs', url: page.url() });
    await context.close();
  }

  // ── P1-1 (fix-2): 1 relatório + anexo, arrastar o 2º → o anexo JÁ ──
  // selecionado é submetido com >1 relatório; o servidor cria os 2 casos
  // sem anexos e emite o erro não-bloqueante nomeado (R1). Antes do fix,
  // o input `disabled` omitia o anexo e o erro nunca aparecia.
  {
    const { context, page } = await loginAs(browser, 'verify-nir', 'nir-multiatt');
    await page.locator('#id_documents').setInputFiles([PDF_A]);
    await page.locator('#upload-preview').waitFor({ state: 'visible' });
    await page.locator('#id_attachments').setInputFiles([ATTACHMENT]);
    await page.locator('#attachment-preview').waitFor({ state: 'visible' });
    check('multiatt-attachment-preview-1-before-drag', (await page.locator('#attachment-count').innerText()).includes('1 anexo'));
    check('multiatt-attachments-enabled-with-1-before-drag', await page.locator('#id_attachments').isEnabled());
    // Arrastar o 2º relatório: a edição desabilita os anexos e mostra o
    // hint, mas o preview do anexo e os arquivos no input são preservados.
    await page.evaluate(() => {
      const zone = document.getElementById('upload-zone');
      const dt = new DataTransfer();
      dt.items.add(new File(['%PDF-1.4 fake'], 'segundo.pdf', { type: 'application/pdf' }));
      zone.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: dt }));
      zone.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: dt }));
    });
    await page.waitForFunction(() => document.querySelector('#batch-count')?.textContent.includes('2 arquivo'));
    check('multiatt-preview-2-after-drag', (await page.locator('#file-list li').count()) === 2);
    const multiList = await page.locator('#file-list').innerText();
    check('multiatt-dragged-in-preview', multiList.includes('segundo.pdf'), multiList.replace(/\n/g, ' | '));
    check('multiatt-attachments-disabled-with-2-after-drag', await page.locator('#id_attachments').isDisabled());
    check('multiatt-attachments-hint-visible-after-drag', await page.locator('#intake-attachments-single-hint').isVisible());
    check('multiatt-attachment-preview-kept-after-drag', (await page.locator('#attachment-count').innerText()).includes('1 anexo'));
    const namesBeforeSubmit = await page.evaluate(() => ({
      docs: Array.from(document.getElementById('id_documents').files).map((f) => f.name),
      atts: Array.from(document.getElementById('id_attachments').files).map((f) => f.name),
    }));
    check('multiatt-inputs-hold-docs-and-attachment', namesBeforeSubmit.docs.length === 2 && namesBeforeSubmit.atts.includes('foto.jpg'), JSON.stringify(namesBeforeSubmit));
    await snap(page, 'nir-upload-multi-attachment-before');
    await selectProcedureTypeViaCombobox(page);
    await page.getByRole('button', { name: 'Criar caso' }).click();
    await page.waitForLoadState('domcontentloaded');
    const multiBody = await page.locator('body').innerText();
    check('multiatt-2-cases-created-without-attachments', multiBody.includes('2 casos criados'), page.url());
    check('multiatt-named-nonblocking-error', multiBody.includes('exatamente 1'), 'erro nomeado do servidor');
    check('multiatt-stays-on-result', !page.url().includes('/intake/cases/'), page.url());
    await snap(page, 'nir-upload-multi-attachment-result');
    observed.steps.push({ step: 'nir-upload-multi-attachment', url: page.url() });
    await context.close();
  }

  // ── P1-2 (fix-2): reenvio corrigido de verdade no browser ──
  // O VERIFY-001 sintético nasce AWAITING_DOCTOR no seed: médico nega
  // todos os procedimentos (resposta final publicada), o NIR criador
  // confirma o recebimento até CLEANED e reenvia 1 PDF + anexo no
  // widget (submit sem confirmação) até o detalhe do NOVO caso. Tudo
  // pela UI real — nenhuma mutação direta de banco, seed ou backend.
  {
    const { context, page } = await loginAs(browser, 'verify-doctor', 'doctor-resub');
    // Fila médica: o cartão do VERIFY-001 é o caminho até o caso.
    await page.locator('a.list-group-item-action, div.list-group-item').first().waitFor({ timeout: 15000 }).catch(() => {});
    check('resubmit-doctor-queue-heading', (await page.getByRole('heading', { name: 'Fila médica' }).count()) >= 1);
    const doctorBody = await page.locator('body').innerText();
    check('resubmit-doctor-sees-VERIFY-001', doctorBody.includes('VERIFY-001'), 'cartão sintético 001');
    const decideHref = await page.locator('[data-agency-record-number="VERIFY-001"] a.list-group-item-action').getAttribute('href').catch(() => null);
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
      const decidedBody = await page.locator('body').innerText();
      check('resubmit-doctor-decision-recorded', page.url().includes(`/doctor/case/${resubmitCaseId}`) || decidedBody.includes('Decisão registrada'), page.url());
      await snap(page, 'doctor-resubmit-decided');
      observed.steps.push({ step: 'doctor-resubmit-decided', url: page.url() });
    }
    await context.close();
  }
  {
    if (!resubmitCaseId) {
      check('resubmit-nir-cleaned', false, 'caseId não encontrado na fila');
      check('resubmit-widget-submit', false, 'caseId não encontrado na fila');
    } else {
      const { context, page } = await loginAs(browser, 'verify-nir', 'nir-resubmit');
      // Ciência do criador até CLEANED: detalhe do caso -> Confirmar.
      await page.goto(`${base}/intake/cases/${resubmitCaseId}/`, { waitUntil: 'domcontentloaded' });
      const ackButton = page.getByRole('button', { name: 'Confirmar recebimento' });
      check('resubmit-nir-ack-visible', (await ackButton.count()) === 1);
      await snap(page, 'nir-resubmit-before-ack');
      await ackButton.click();
      await page.waitForLoadState('domcontentloaded');
      const cleanedBody = await page.locator('body').innerText();
      const cleanedLinkCount = await page.locator('a[href*="/resubmit/"]').count();
      check('resubmit-nir-cleaned', cleanedBody.includes('Caso concluído') || cleanedLinkCount > 0, page.url());
      check('resubmit-link-visible-when-cleaned', cleanedLinkCount >= 1, page.url());
      await snap(page, 'nir-resubmit-cleaned');
      observed.steps.push({ step: 'nir-resubmit-cleaned', url: page.url() });
      // Widget do reenvio: 1 PDF + anexo, submit sem confirmação (R3/R4).
      await page.goto(`${base}/intake/cases/${resubmitCaseId}/resubmit/`, { waitUntil: 'domcontentloaded' });
      check('resubmit-form-heading', (await page.getByRole('heading', { name: 'Reenviar corrigido' }).count()) >= 1, page.url());
      check('resubmit-zone-visible', await page.locator('#upload-zone').isVisible());
      check('resubmit-preview-hidden-initially', await page.locator('#upload-preview').isHidden());
      const resubmitMultiple = await page.locator('#id_documents').evaluate((el) => el.hasAttribute('multiple'));
      check('resubmit-documents-single', !resubmitMultiple, 'sem multiple');
      check('resubmit-documents-visible', await page.locator('#id_documents').isVisible());
      await snap(page, 'nir-resubmit-form');
      await page.locator('#id_documents').setInputFiles([PDF_CORRIGIDO]);
      await page.locator('#upload-preview').waitFor({ state: 'visible' });
      check('resubmit-preview-1', (await page.locator('#batch-count').innerText()).includes('1 arquivo'));
      await page.locator('#id_attachments').setInputFiles([ATTACHMENT]);
      await page.locator('#attachment-preview').waitFor({ state: 'visible' });
      check('resubmit-attachment-preview-1', (await page.locator('#attachment-count').innerText()).includes('1 anexo'));
      check('resubmit-no-confirmation-checkbox', (await page.locator('#attachment-confirm').count()) === 0);
      await selectProcedureTypeViaCombobox(page);
      await page.locator('textarea[name="correction_reason"]').fill('laudo ilegível, reenvio com foto do exame');
      await snap(page, 'nir-resubmit-preview');
      await page.getByRole('button', { name: 'Criar reenvio corrigido' }).click();
      await page.waitForURL(/\/intake\/cases\/[0-9a-f-]+\/?$/, { timeout: 20000 }).catch(() => {});
      const resubmitResultBody = await page.locator('body').innerText();
      const resubmitLandedOnDetail = page.url().includes('/intake/cases/') && !page.url().includes('/resubmit/');
      check('resubmit-widget-submit', resubmitLandedOnDetail, page.url());
      check('resubmit-new-case-lists-corrected-pdf', resubmitResultBody.includes('corrigido.pdf'), 'detalhe lista o corrigido');
      check('resubmit-new-case-lists-attachment', resubmitResultBody.includes('foto.jpg'), 'detalhe lista o anexo');
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

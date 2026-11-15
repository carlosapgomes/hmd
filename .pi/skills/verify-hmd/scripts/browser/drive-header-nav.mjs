// Driver Playwright pinado do verify-hmd — slice 004 header/nav/sino.
// Login REAL pelo form (sem addCookies), header+nav por papel, toggler
// mobile e sino com polling zero→1 sem reload. Evidência em
// <run>/artifacts/. Exit: 0 PASS · 1 falha observada · 2 infra/driver.
import { execSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync, readlinkSync, writeFileSync } from 'node:fs';
import path from 'node:path';

import { chromium } from 'playwright';

const runArg = process.argv.indexOf('--run');
if (runArg < 0 || !process.argv[runArg + 1]) {
  console.error('uso: drive-header-nav.mjs --run <tmp/verification/hmd-verify-...>');
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

const REQUIRED_ASSETS = ['/static/css/app.css', '/static/js/sw.js', '/static/js/bell_poll.js'];
const failedAssets = [];
const checks = [];
const failures = [];
function check(id, ok, detail) {
  checks.push({ id, ok: !!ok, detail: String(detail ?? '') });
  if (!ok) failures.push(id);
  console.log(`${ok ? 'PASS' : 'FAIL'} ${id} ${detail ?? ''}`);
}
// OLI-005: nenhuma captura antes de o shell terminar o fade-in —
// aguarda `.app-shell` com computed opacity >= 0.99 (sem sleep fixo).
async function waitShell(page) {
  await page.waitForFunction(
    () => {
      const el = document.querySelector('.app-shell');
      return !!el && parseFloat(getComputedStyle(el).opacity) >= 0.99;
    },
    { timeout: 10000 },
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
  await snap(page, `${label}-login-before`);
  await page.getByLabel('Usuário').fill(username);
  await page.getByLabel('Senha', { exact: true }).fill(PASSWORD);
  await page.getByRole('button', { name: 'Entrar' }).click();
  await page.waitForURL((url) => !url.pathname.endsWith('/login/'), { timeout: 15000 });
  return { context, page };
}
async function headerCommon(page, label) {
  const body = await page.locator('body').innerText();
  const html = await page.content();
  check(`${label}-brand`, body.includes('SAR – HMD'), 'marca');
  check(`${label}-icon32`, html.includes('width="32"') && html.includes('height="32"'), 'ícone 32px');
  check(`${label}-session`, body.includes('Verificação'), 'sessão Nome · Papel');
  check(`${label}-avatar`, await page.locator('.avatar-circle').count() >= 1, 'avatar');
  check(`${label}-logout`, body.includes('Sair'), 'sair');
  check(`${label}-manual`, html.includes('Manual do usuário'), 'manual ?');
  check(`${label}-htmx`, html.includes('htmx.org@2.0.4'), 'htmx fundação');
  check(`${label}-favicon`, html.includes('rel="icon"'), 'favicon');
  check(`${label}-apple`, html.includes('apple-touch-icon'), 'apple-touch-icon');
  check(`${label}-polljs`, html.includes('bell_poll.js'), 'bell_poll.js');
  const bells = page.locator('[data-notifications-badge]');
  check(`${label}-bells`, (await bells.count()) >= 2, `sinos=${await bells.count()}`);
  const unread = await page.evaluate(() => document.querySelector('[data-notifications-badge]')?.getAttribute('data-unread-count-url'));
  check(`${label}-unread-url`, unread === '/notifications/unread-count/', String(unread));
}

let browser;
try {
  browser = await chromium.launch({ headless: true });
} catch (err) {
  console.error(`infra BLOCKED: Chromium pinado indisponível (${err.message}).`);
  process.exit(2);
}
const observed = { runId: manifest.runId, base, database: manifest.database, steps: [] };
const oli005Nav = {};
try {
  // Papéis do painel (1): NIR, médico, agendador, gerente.
  const jobs = [
    ['verify-nir', 'nir', '/intake/', 'Novo Encaminhamento', 'Meus Casos'],
    ['verify-doctor', 'doctor', '/doctor/', 'Pendentes', 'Decididos'],
    ['verify-scheduler', 'scheduler', '/scheduler/', 'Pendentes', 'Processados'],
    ['verify-manager', 'manager', '/dashboard/', 'Painel gerencial', null],
  ];
  for (const [user, label, urlPart, tabA, tabB] of jobs) {
    const { context, page } = await loginAs(browser, user, label);
    check(`${label}-home-url`, page.url().includes(urlPart), page.url());
    await headerCommon(page, label);
    if (label === 'manager') {
      check('manager-no-nav', (await page.locator('nav.app-nav').count()) === 0, 'painel sem faixa');
      check('manager-heading', (await page.getByRole('heading', { name: 'Painel gerencial' }).count()) >= 1, 'painel');
      check('manager-panel-link', (await page.getByRole('link', { name: 'Painel gerencial' }).count()) >= 1, 'link Painel gerencial');
    } else {
      check(`${label}-nav`, (await page.locator('nav.app-nav').count()) >= 1, 'faixa app-nav');
      // A faixa ocupa a linha de largura total abaixo da marca/sessão.
      const navBelow = await page.evaluate(() => {
        const nav = document.querySelector('nav.app-nav');
        const brand = document.querySelector('.navbar-brand');
        if (!nav || !brand) return false;
        const n = nav.getBoundingClientRect();
        const b = brand.getBoundingClientRect();
        return n.top >= b.bottom - 1 && n.left <= b.left + 1;
      });
      check(`${label}-nav-below-header`, navBelow, 'nav em linha própria abaixo do header');
      // Contadores podem acompanhar o rótulo (ex.: "Pendentes 2"): sem exact.
      check(`${label}-tab-a`, (await page.getByRole('link', { name: tabA }).count()) >= 1, tabA);
      if (tabB) check(`${label}-tab-b`, (await page.getByRole('link', { name: tabB }).count()) >= 1, tabB);
      const active = page.locator('nav.app-nav a.nav-link.active');
      check(`${label}-active`, (await active.count()) === 1, `ativas=${await active.count()}`);
      const activeText = await active.first().innerText().catch(() => '');
      check(`${label}-active-tab`, activeText.includes(tabA), activeText.trim());
      // OLI-005 (R1/R2/R3/R4): pills/contadores no padrão HMD.
      await waitShell(page);
      const activeHref = await active.first().getAttribute('href');
      const activeAria = await active.first().getAttribute('aria-current');
      check(`oli005-${label}-active-aria`, activeAria === 'page', `aria-current=${activeAria}`);
      const activeStyle = await active.first().evaluate((el) => {
        const cs = getComputedStyle(el);
        return { backgroundColor: cs.backgroundColor, color: cs.color, minHeight: cs.minHeight };
      });
      check(`oli005-${label}-active-computed`, activeStyle.backgroundColor === 'rgb(255, 255, 255)' && activeStyle.color === 'rgb(13, 74, 138)', JSON.stringify(activeStyle));
      const noInlineBadge = await page.evaluate(() => document.querySelectorAll('nav.app-nav a span.badge').length);
      check(`oli005-${label}-no-inline-badge`, noInlineBadge === 0, `spans=${noInlineBadge}`);
      const navLinks = await page.evaluate(() =>
        [...document.querySelectorAll('nav.app-nav a.nav-link')].map((el) => ({
          text: el.textContent.trim(),
          count: el.getAttribute('data-count'),
          href: el.getAttribute('href'),
          cls: el.className,
          aria: el.getAttribute('aria-current'),
          afterDisplay: getComputedStyle(el, '::after').display,
          afterContent: getComputedStyle(el, '::after').content,
          afterBackground: getComputedStyle(el, '::after').backgroundColor,
          height: el.getBoundingClientRect().height,
          top: el.getBoundingClientRect().top,
        })),
      );
      oli005Nav[label] = { url: page.url(), activeHref, activeStyle, navLinks };
      const dupFree = navLinks.every((l) => !/[0-9]/.test(l.text));
      check(`oli005-${label}-no-duplicate-count-dom`, dupFree, JSON.stringify(navLinks.map((l) => l.text)));
      const targetsOk = navLinks.every((l) => l.height >= 44);
      check(`oli005-${label}-target-44`, targetsOk, JSON.stringify(navLinks.map((l) => l.height)));
      const sameRow = navLinks.length < 2 || navLinks.every((l) => Math.abs(l.top - navLinks[0].top) < 1);
      check(`oli005-${label}-desktop-aligned`, sameRow, JSON.stringify(navLinks.map((l) => l.top)));
      if (label === 'doctor') {
        const pend = navLinks.find((l) => l.text === 'Pendentes');
        const dec = navLinks.find((l) => l.text === 'Decididos');
        check('oli005-doctor-pending-action', pend && pend.count === '2' && pend.afterContent === '"2"' && pend.afterDisplay !== 'none' && pend.afterBackground === 'rgb(181, 53, 53)' && pend.cls.includes('nav-count-badge--danger'), JSON.stringify(pend));
        check('oli005-doctor-decided-zero-hidden', dec && dec.count === '0' && dec.afterDisplay === 'none' && dec.cls.includes('nav-count-badge--neutral'), JSON.stringify(dec));
        check('oli005-doctor-active-href', activeHref === '/doctor/?tab=aguardando', String(activeHref));
        // Hover no link inativo: fundo branco translúcido + texto branco.
        // O Bootstrap transiciona background/color (150ms): aguarda o
        // estado computado, sem sleep fixo.
        await page.locator('nav.app-nav a.nav-link', { hasText: 'Decididos' }).hover();
        await page.locator('nav.app-nav a.nav-link', { hasText: 'Decididos' }).evaluate((el) => el.matches(':hover'));
        await page.waitForFunction(
          () => {
            const el = [...document.querySelectorAll('nav.app-nav a.nav-link')].find((a) => a.textContent.trim() === 'Decididos');
            return !!el && getComputedStyle(el).backgroundColor === 'rgba(255, 255, 255, 0.12)';
          },
          { timeout: 5000 },
        );
        const hoverStyle = await page.locator('nav.app-nav a.nav-link', { hasText: 'Decididos' }).evaluate((el) => {
          const cs = getComputedStyle(el);
          return { backgroundColor: cs.backgroundColor, color: cs.color };
        });
        check('oli005-doctor-hover-computed', hoverStyle.backgroundColor === 'rgba(255, 255, 255, 0.12)' && hoverStyle.color === 'rgb(255, 255, 255)', JSON.stringify(hoverStyle));
        oli005Nav.doctorHover = hoverStyle;
        // Focus acessível via teclado (Tab) até um link da faixa.
        let focusStyle = null;
        for (let i = 0; i < 40; i++) {
          await page.keyboard.press('Tab');
          const onNav = await page.evaluate(() => {
            const el = document.activeElement;
            return !!el && el.classList && el.classList.contains('nav-link') && !!el.closest('nav.app-nav');
          });
          if (onNav) {
            focusStyle = await page.evaluate(() => {
              const cs = getComputedStyle(document.activeElement);
              return { outlineStyle: cs.outlineStyle, outlineWidth: cs.outlineWidth, outlineColor: cs.outlineColor };
            });
            break;
          }
        }
        check('oli005-doctor-focus-visible', !!focusStyle && focusStyle.outlineStyle !== 'none' && focusStyle.outlineWidth !== '0px' && focusStyle.outlineColor === 'rgb(212, 238, 243)', JSON.stringify(focusStyle));
        oli005Nav.doctorFocus = focusStyle;
      }
      if (label === 'scheduler') {
        const allZeroHidden = navLinks.every((l) => l.count === '0' && l.afterDisplay === 'none');
        check('oli005-scheduler-zero-hidden', allZeroHidden, JSON.stringify(navLinks));
        check('oli005-scheduler-active-href', activeHref === '/scheduler/?tab=aguardando', String(activeHref));
      }
      if (label === 'nir') {
        // Home do NIR não tem contadores (caso sem data-count).
        const noCounts = navLinks.every((l) => l.count === null);
        check('oli005-nir-home-no-counts', noCounts, JSON.stringify(navLinks.map((l) => l.count)));
        check('oli005-nir-active-href', activeHref === '/intake/', String(activeHref));
      }
    }
    for (const asset of REQUIRED_ASSETS) {
      const status = await page.evaluate(async (u) => (await fetch(u, { cache: 'no-store' })).status, asset);
      check(`static-${label}-${asset.split('/').pop()}`, status === 200, `${asset} HTTP ${status}`);
    }
    await snap(page, `${label}-header`);
    observed.steps.push({ step: `${label}-header`, url: page.url() });
    await context.close();
  }

  // OLI-005: NIR com contadores (meus casos) + filter_qs preservado.
  {
    const { context, page } = await loginAs(browser, 'verify-nir', 'nir-my-cases');
    await page.goto(`${base}/intake/cases/?tab=active&q=VERIFY&status=FAILED`, { waitUntil: 'domcontentloaded' });
    await waitShell(page);
    const nirNav = await page.evaluate(() =>
      [...document.querySelectorAll('nav.app-nav a.nav-link')].map((el) => ({
        text: el.textContent.trim(),
        count: el.getAttribute('data-count'),
        href: el.getAttribute('href'),
        cls: el.className,
        aria: el.getAttribute('aria-current'),
        afterDisplay: getComputedStyle(el, '::after').display,
        afterContent: getComputedStyle(el, '::after').content,
        afterBackground: getComputedStyle(el, '::after').backgroundColor,
      })),
    );
    oli005Nav.nirMyCases = { url: page.url(), nirNav };
    const myLink = nirNav.find((l) => l.text === 'Meus Casos');
    const closedLink = nirNav.find((l) => l.text === 'Casos Encerrados');
    check('oli005-nir-mycases-neutral-nonzero', !!myLink && myLink.count === '2' && myLink.afterContent === '"2"' && myLink.afterBackground === 'rgb(76, 102, 119)' && myLink.cls.includes('nav-count-badge--neutral'), JSON.stringify(myLink));
    check('oli005-nir-closed-zero-hidden', !!closedLink && closedLink.count === '0' && closedLink.afterDisplay === 'none', JSON.stringify(closedLink));
    check('oli005-nir-filter-qs-preserved', !!myLink && myLink.href.includes('q=VERIFY') && myLink.href.includes('status=FAILED') && !!closedLink && closedLink.href.includes('q=VERIFY') && closedLink.href.includes('status=FAILED'), JSON.stringify(nirNav.map((l) => l.href)));
    check('oli005-nir-mycases-aria', !!myLink && myLink.aria === 'page', JSON.stringify(myLink));
    const nirNoInline = await page.evaluate(() => document.querySelectorAll('nav.app-nav a span.badge').length);
    check('oli005-nir-no-inline-badge', nirNoInline === 0, `spans=${nirNoInline}`);
    await snap(page, 'nir-my-cases-header');
    observed.steps.push({ step: 'nir-my-cases-header', url: page.url() });
    await context.close();
  }

  // Admin no painel (mesmo destino do gerente).
  {
    const { context, page } = await loginAs(browser, 'verify-admin', 'admin');
    check('admin-home-url', page.url().includes('/dashboard/'), page.url());
    await headerCommon(page, 'admin');
    check('admin-no-nav', (await page.locator('nav.app-nav').count()) === 0, 'admin no painel sem faixa');
    check('admin-cross-doctor', (await page.locator('a[href="/doctor/?tab=aguardando"]').count()) >= 1, 'admin -> fila médica');
    check('admin-cross-scheduler', (await page.locator('a[href="/scheduler/?tab=aguardando"]').count()) >= 1, 'admin -> fila agendamento');
    check('admin-panel-link', (await page.getByRole('link', { name: 'Painel gerencial' }).count()) >= 1, 'admin -> painel');
    await snap(page, 'admin-header');
    observed.steps.push({ step: 'admin-header', url: page.url() });
    await context.close();
  }

  // Mobile estreito: toggler abre a sessão sem overflow.
  {
    const { context, page } = await loginAs(browser, 'verify-doctor', 'doctor-mobile', {
      width: 390,
      height: 844,
    });
    const toggler = page.getByRole('button', { name: 'Menu da sessão' });
    check('mobile-toggler', (await toggler.count()) === 1, 'toggler');
    await toggler.click();
    await page.waitForFunction(() => document.querySelector('#sessionMenu')?.classList.contains('show'), { timeout: 5000 });
    check('mobile-session-open', await page.locator('#sessionMenu').isVisible(), 'sessão aberta');
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    check('mobile-no-overflow', overflow <= 1, `overflow=${overflow}`);
    // OLI-005 (R4): pills fazem wrap, alvo >=44px, zero oculto, sem overflow.
    await waitShell(page);
    const mobileNav = await page.evaluate(() => {
      const links = [...document.querySelectorAll('nav.app-nav a.nav-link')].map((el) => ({
        text: el.textContent.trim(),
        height: el.getBoundingClientRect().height,
        top: el.getBoundingClientRect().top,
        count: el.getAttribute('data-count'),
        afterDisplay: getComputedStyle(el, '::after').display,
      }));
      return {
        links,
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: window.innerWidth,
        navHeight: document.querySelector('nav.app-nav')?.getBoundingClientRect().height ?? 0,
      };
    });
    oli005Nav.doctorMobile = mobileNav;
    // Médico (2 rótulos curtos) cabe numa linha em 390px; o wrap é
    // provado no NIR (3 rótulos longos), logo abaixo.
    const flexWrap = await page.evaluate(() => getComputedStyle(document.querySelector('nav.app-nav .nav')).flexWrap);
    check('oli005-mobile-nav-flexwrap', flexWrap === 'wrap', `flexWrap=${flexWrap}`);
    check('oli005-mobile-target-44', mobileNav.links.every((l) => l.height >= 44), JSON.stringify(mobileNav.links.map((l) => l.height)));
    check('oli005-mobile-no-overflow', mobileNav.scrollWidth <= mobileNav.clientWidth, `${mobileNav.scrollWidth}<=${mobileNav.clientWidth}`);
    const decidedMobile = mobileNav.links.find((l) => l.text === 'Decididos');
    check('oli005-mobile-zero-hidden', !!decidedMobile && decidedMobile.count === '0' && decidedMobile.afterDisplay === 'none', JSON.stringify(decidedMobile));
    // Estabilidade mobile: altura estável em duplo rAF após opacity >=0.99.
    const stable = await page.evaluate(async () => {
      const h = () => document.querySelector('nav.app-nav')?.getBoundingClientRect().height ?? 0;
      const a = h();
      await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
      return Math.abs(h() - a) < 1;
    });
    check('oli005-mobile-layout-stable', stable, 'altura da faixa estável em duplo rAF');
    await snap(page, 'doctor-header-mobile');
    observed.steps.push({ step: 'doctor-header-mobile', url: page.url() });
    await context.close();
  }

  // OLI-005 (R4): wrap real em 390px — NIR com 3 rótulos longos.
  {
    const { context, page } = await loginAs(browser, 'verify-nir', 'nir-mobile', {
      width: 390,
      height: 844,
    });
    await waitShell(page);
    const nirMobile = await page.evaluate(() => ({
      links: [...document.querySelectorAll('nav.app-nav a.nav-link')].map((el) => ({
        text: el.textContent.trim(),
        height: el.getBoundingClientRect().height,
        top: el.getBoundingClientRect().top,
      })),
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: window.innerWidth,
    }));
    oli005Nav.nirMobile = nirMobile;
    const nirWrapped = new Set(nirMobile.links.map((l) => Math.round(l.top))).size > 1;
    check('oli005-mobile-nir-nav-wrap', nirWrapped, JSON.stringify(nirMobile.links.map((l) => l.top)));
    check('oli005-mobile-nir-target-44', nirMobile.links.every((l) => l.height >= 44), JSON.stringify(nirMobile.links.map((l) => l.height)));
    check('oli005-mobile-nir-no-overflow', nirMobile.scrollWidth <= nirMobile.clientWidth, `${nirMobile.scrollWidth}<=${nirMobile.clientWidth}`);
    await snap(page, 'nir-header-mobile');
    observed.steps.push({ step: 'nir-header-mobile', url: page.url() });
    await context.close();
  }

  // Sino: 0 → 1 sem reload (desktop aguarda o poll de 45 s). Prova sobre o
  // sino VISÍVEL (:visible — no desktop é o sino #notification-badge, não o
  // mobile oculto) e sobre o número exibido no selo, não só data-count.
  // Detecção direta de reload: token em window (reload o apaga).
  const runVerifyPy = (code) => {
    const py = existsSync('.venv/bin/python') ? '.venv/bin/python' : 'uv run python';
    execSync(
      `${py} manage.py shell --settings=config.settings.verify -c "${code.replace(/"/g, '\\"')}"`,
      {
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
      },
    );
  };
  const createNotification = (username, ref) =>
    runVerifyPy(
      "from apps.accounts.models import UserNotification; " +
        "from django.contrib.auth import get_user_model; " +
        "from apps.cases.models import Case; " +
        `u=get_user_model().objects.get(username='${username}'); ` +
        `c=Case.objects.filter(agency_record_number='${ref}').first(); ` +
        "UserNotification.objects.create(recipient=u, case=c, event=None, notification_type='final_reply_posted', title='Aviso de verificacao', body_preview='Polling do sino'); " +
        "print('created')",
    );
  const visibleBellState = (page) =>
    page.evaluate(() => {
      const bells = [...document.querySelectorAll('[data-notifications-badge]')];
      const bell = bells.find((el) => el.offsetParent !== null) || bells[0];
      if (!bell) return { count: null, shown: null, onBell: false, label: null };
      const span = bell.querySelector('span.badge');
      let onBell = false;
      if (span) {
        const b = bell.getBoundingClientRect();
        const s = span.getBoundingClientRect();
        onBell = s.left >= b.left - 5 && s.right <= b.right + 20 && s.top >= b.top - 20 && s.bottom <= b.bottom + 6;
      }
      return { count: bell.getAttribute('data-count'), shown: span ? span.textContent.trim() : null, onBell, label: bell.getAttribute('aria-label') };
    });
  {
    const { context, page } = await loginAs(browser, 'verify-doctor', 'bell');
    const urlBefore = page.url();
    await page.evaluate(() => { window.__hmdBellToken = 'bell-token-desktop'; });
    let state = await visibleBellState(page);
    check('bell-initial-zero', state.count === '0' && state.shown === null, `data-count=${state.count} selo=${state.shown}`);
    // Notificação sintética no banco isolado (única escrita da journey).
    createNotification('verify-doctor', 'VERIFY-001');
    // Poll de 45 s + margem (até 60 s do contrato).
    for (let i = 0; i < 30; i++) {
      await page.waitForTimeout(2000);
      state = await visibleBellState(page);
      if (state.count === '1') break;
    }
    check('bell-polls-to-one', state.count === '1', `data-count=${state.count} sem reload`);
    check('bell-visible-number', state.shown === '1', `selo visível=${state.shown}`);
    check('bell-badge-on-bell', state.onBell, 'selo posicionado sobre o sino visível');
    const token = await page.evaluate(() => window.__hmdBellToken);
    check('bell-no-reload', page.url() === urlBefore && token === 'bell-token-desktop', `${page.url()} token=${token}`);
    check('bell-aria', String(state.label).includes('1 não lida'), String(state.label));
    await snap(page, 'bell-polling');
    writeFileSync(path.join(artifacts, 'bell-polling.json'), JSON.stringify({ urlBefore, urlAfter: page.url(), count: state.count, shown: state.shown, onBell: state.onBell }, null, 2));
    observed.steps.push({ step: 'bell-polling', url: page.url() });
    await context.close();
  }

  // Mobile: 0 → 1 com poll imediato ao voltar (visibilitychange). A
  // notificação da fase desktop é marcada como lida antes — o mobile
  // também começa em zero, sobre o sino visível (o mobile, <lg).
  {
    runVerifyPy(
      "from apps.accounts.models import UserNotification; " +
        "from django.contrib.auth import get_user_model; " +
        "from django.utils import timezone; " +
        "u=get_user_model().objects.get(username='verify-doctor'); " +
        "UserNotification.objects.filter(recipient=u, read_at__isnull=True).update(read_at=timezone.now()); " +
        "print('marked-read')",
    );
    const { context, page } = await loginAs(browser, 'verify-doctor', 'bell-mobile', {
      width: 390,
      height: 844,
    });
    let state = await visibleBellState(page);
    check('bell-mobile-initial-zero', state.count === '0' && state.shown === null, `data-count=${state.count} selo=${state.shown}`);
    await page.evaluate(() => { window.__hmdBellToken = 'bell-token-mobile'; });
    createNotification('verify-doctor', 'VERIFY-002');
    const urlBefore = page.url();
    await page.evaluate(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await page.waitForTimeout(1500);
    state = await visibleBellState(page);
    check('bell-mobile-immediate', state.count === '1', `data-count=${state.count} via visibilitychange`);
    check('bell-mobile-visible-number', state.shown === '1', `selo visível=${state.shown}`);
    check('bell-mobile-badge-on-bell', state.onBell, 'selo posicionado sobre o sino mobile');
    const token = await page.evaluate(() => window.__hmdBellToken);
    check('bell-mobile-no-reload', page.url() === urlBefore && token === 'bell-token-mobile', `${page.url()} token=${token}`);
    await snap(page, 'bell-polling-mobile');
    observed.steps.push({ step: 'bell-polling-mobile', url: page.url() });
    await context.close();
  }

  // Fix-2 (revisão-2 P1): faixa do papel em página de detalhe/decisão e
  // no perfil compartilhado — sem aba ativa (padrão das notificações).
  // O caso sintético é o primeiro card da fila médica (VERIFY-001);
  // o mesmo UUID serve ao detalhe do médico (decide + detalhe), do
  // agendador e do NIR (criador). Admin no perfil mantém o acesso
  // cruzado, sem faixa de abas.
  let verifyCaseId = null;
  {
    const { context, page } = await loginAs(browser, 'verify-doctor', 'doctor-detail');
    await page.goto(`${base}/doctor/?tab=aguardando`, { waitUntil: 'domcontentloaded' });
    const decideHref = await page.locator('a[href*="/doctor/case/"][href$="/decide/"]').first().getAttribute('href');
    check('doctor-detail-case-link', typeof decideHref === 'string' && decideHref.includes('/doctor/case/'), String(decideHref));
    verifyCaseId = decideHref ? decideHref.match(/\/doctor\/case\/([^/]+)\//)?.[1] ?? null : null;
    await page.goto(`${base}${decideHref}`, { waitUntil: 'domcontentloaded' });
    check('doctor-decide-nav', (await page.locator('nav.app-nav').count()) >= 1, 'faixa app-nav na decisão');
    check('doctor-decide-no-active', (await page.locator('nav.app-nav a.nav-link.active').count()) === 0, 'sem aba ativa na decisão');
    check('doctor-decide-tabs', (await page.getByRole('link', { name: 'Pendentes' }).count()) >= 1 && (await page.getByRole('link', { name: 'Decididos' }).count()) >= 1, 'Pendentes/Decididos');
    await snap(page, 'doctor-detail');
    observed.steps.push({ step: 'doctor-detail', url: page.url() });
    await page.goto(`${base}${decideHref.replace(/\/decide\/$/, '/')}`, { waitUntil: 'domcontentloaded' });
    check('doctor-case-detail-nav', (await page.locator('nav.app-nav').count()) >= 1, 'faixa app-nav no detalhe');
    check('doctor-case-detail-no-active', (await page.locator('nav.app-nav a.nav-link.active').count()) === 0, 'sem aba ativa no detalhe');
    await snap(page, 'doctor-case-detail');
    observed.steps.push({ step: 'doctor-case-detail', url: page.url() });
    await page.goto(`${base}/profile/`, { waitUntil: 'domcontentloaded' });
    check('doctor-profile-nav', (await page.locator('nav.app-nav').count()) >= 1, 'faixa app-nav no perfil');
    check('doctor-profile-no-active', (await page.locator('nav.app-nav a.nav-link.active').count()) === 0, 'sem aba ativa no perfil');
    check('doctor-profile-tabs', (await page.getByRole('link', { name: 'Pendentes' }).count()) >= 1 && (await page.getByRole('link', { name: 'Decididos' }).count()) >= 1, 'Pendentes/Decididos no perfil');
    await snap(page, 'doctor-profile');
    observed.steps.push({ step: 'doctor-profile', url: page.url() });
    await context.close();
  }
  {
    const { context, page } = await loginAs(browser, 'verify-scheduler', 'scheduler-detail');
    await page.goto(`${base}/scheduler/case/${verifyCaseId}/`, { waitUntil: 'domcontentloaded' });
    check('scheduler-detail-nav', (await page.locator('nav.app-nav').count()) >= 1, 'faixa app-nav no detalhe');
    check('scheduler-detail-no-active', (await page.locator('nav.app-nav a.nav-link.active').count()) === 0, 'sem aba ativa no detalhe');
    check('scheduler-detail-tabs', (await page.getByRole('link', { name: 'Pendentes' }).count()) >= 1 && (await page.getByRole('link', { name: 'Processados' }).count()) >= 1, 'Pendentes/Processados');
    await snap(page, 'scheduler-detail');
    observed.steps.push({ step: 'scheduler-detail', url: page.url() });
    await page.goto(`${base}/profile/`, { waitUntil: 'domcontentloaded' });
    check('scheduler-profile-nav', (await page.locator('nav.app-nav').count()) >= 1, 'faixa app-nav no perfil');
    check('scheduler-profile-no-active', (await page.locator('nav.app-nav a.nav-link.active').count()) === 0, 'sem aba ativa no perfil');
    check('scheduler-profile-tabs', (await page.getByRole('link', { name: 'Pendentes' }).count()) >= 1 && (await page.getByRole('link', { name: 'Processados' }).count()) >= 1, 'Pendentes/Processados no perfil');
    await snap(page, 'scheduler-profile');
    observed.steps.push({ step: 'scheduler-profile', url: page.url() });
    await context.close();
  }
  {
    const { context, page } = await loginAs(browser, 'verify-nir', 'nir-detail');
    await page.goto(`${base}/intake/cases/${verifyCaseId}/`, { waitUntil: 'domcontentloaded' });
    check('nir-detail-nav', (await page.locator('nav.app-nav').count()) >= 1, 'faixa app-nav no detalhe');
    check('nir-detail-no-active', (await page.locator('nav.app-nav a.nav-link.active').count()) === 0, 'sem aba ativa no detalhe');
    await snap(page, 'nir-detail');
    observed.steps.push({ step: 'nir-detail', url: page.url() });
    await page.goto(`${base}/profile/`, { waitUntil: 'domcontentloaded' });
    check('nir-profile-nav', (await page.locator('nav.app-nav').count()) >= 1, 'faixa app-nav no perfil');
    check('nir-profile-no-active', (await page.locator('nav.app-nav a.nav-link.active').count()) === 0, 'sem aba ativa no perfil');
    await snap(page, 'nir-profile');
    observed.steps.push({ step: 'nir-profile', url: page.url() });
    await context.close();
  }
  {
    const { context, page } = await loginAs(browser, 'verify-admin', 'admin-profile');
    await page.goto(`${base}/profile/`, { waitUntil: 'domcontentloaded' });
    check('admin-profile-no-nav', (await page.locator('nav.app-nav').count()) === 0, 'admin no perfil sem faixa');
    check('admin-profile-cross-doctor', (await page.locator('a[href="/doctor/?tab=aguardando"]').count()) >= 1, 'admin -> fila médica no perfil');
    check('admin-profile-cross-scheduler', (await page.locator('a[href="/scheduler/?tab=aguardando"]').count()) >= 1, 'admin -> fila agendamento no perfil');
    check('admin-profile-panel-link', (await page.getByRole('link', { name: 'Painel gerencial' }).count()) >= 1, 'admin -> painel no perfil');
    await snap(page, 'admin-profile');
    observed.steps.push({ step: 'admin-profile', url: page.url() });
    await context.close();
  }
} catch (err) {
  console.error(`infra/driver: ${err.message}`);
  writeFileSync(path.join(artifacts, 'observed-state.json'), JSON.stringify(observed, null, 2));
  await browser.close();
  process.exit(2);
}
check('no-failed-static-assets', failedAssets.length === 0, failedAssets.join('; ') || 'nenhum /static/* >= 400');
writeFileSync(path.join(artifacts, 'oli005-nav.json'), JSON.stringify(oli005Nav, null, 2));
// Relatório em nome exclusivo: `report.json` pertence ao drive genérico
// (`drive.mjs`) — escrever aqui o mesmo nome sobrescrevia os 180 checks
// do header quando os drives rodavam na mesma run (revisão OLI-005).
writeFileSync(
  path.join(artifacts, 'report-header-nav-4-papeis.json'),
  JSON.stringify({ runId: manifest.runId, journey: 'header-nav-4-papeis', checks, failures, pass: failures.length === 0 }, null, 2),
);
writeFileSync(path.join(artifacts, 'observed-state.json'), JSON.stringify(observed, null, 2));
await browser.close();
console.log(failures.length === 0 ? 'DRIVE PASS' : `DRIVE FAIL: ${failures.join(', ')}`);
process.exit(failures.length === 0 ? 0 : 1);

/*
 * Isolated browser QA against a LOCAL frontend with intercepted API fixtures.
 * No production authentication bypass is installed or shipped by this script.
 * Run: node tools/ui-smoke.cjs (UI_URL defaults to http://127.0.0.1:5173).
 * A passing result verifies UI behavior with fixtures, not live source or Telegram.
 */
const path = require('node:path');
const fs = require('node:fs/promises');
const assert = require('node:assert/strict');
process.env.PLAYWRIGHT_BROWSERS_PATH ||= path.resolve(__dirname, '../.pw-browsers');
const { chromium, webkit, expect } = require('@playwright/test');
const ENGINE = process.env.PW_ENGINE === 'webkit' ? 'webkit' : 'chromium';

const UI_URL = process.env.UI_URL || 'http://127.0.0.1:5173';
if (!['localhost', '127.0.0.1', '[::1]'].includes(new URL(UI_URL).hostname)) {
  throw new Error('Fixture QA refuses non-local UI_URL. Never run intercepted auth tests against production.');
}
const OUT = path.resolve(__dirname, '../artifacts/ui', ENGINE);
const PRIMARY = '11111111', SECOND = '22222222', GOLD = '33333333';
const TOKEN = 'isolated-ui-fixture-session';
const INIT_DATA = 'isolated-ui-fixture-init-data-not-a-telegram-signature';
const VERSION = 'so2-v1-preliminary';
const results = [], failures = [], notes = [
  'All API responses and Telegram methods in these tests are isolated browser fixtures.',
  'This does not verify real game responses, production authentication, native Telegram, or a native screen keyboard.',
  'The keyboard/safe-area checks simulate Telegram viewport events in a desktop browser engine, not native Telegram.',
];

const rounded = n => (Math.round((n + Number.EPSILON) * 100) / 100).toFixed(2);
const color = n => { const d = Number(rounded(n)); return d < .9 ? 'red' : d <= 1.1 ? 'white' : d <= 1.8 ? 'green' : 'gold'; };
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

function profile(uid, mode = 'competitive', scope = 'last20', empty = false) {
  const count = empty ? 0 : uid === SECOND ? 5 : 20;
  // 13/20 (65%) are above 1.00; arithmetic mean is 1.104 -> white 1.10.
  const values = [.894, .895, 1.104, 1.105, 1.804, 1.805, ...Array(5).fill(.99), ...Array(9).fill(9.523 / 9)];
  const length = scope === 'archive' && count ? count + 5 : count;
  const matches = Array.from({ length }, (_, i) => {
    const rating = uid === SECOND ? 1.2 : uid === GOLD ? 1.805 : values[i % values.length];
    const kills = 15 + i % 4;
    return { id: `${mode}-${uid}-${i}`, map: i % 2 ? 'Sandstone' : 'Zone 9',
      ended_at: new Date(Date.UTC(2026, 10, 15 - i, 9, 30)).toISOString(), mode,
      score: i % 2 ? [8, 10] : [10, 8], result: i % 2 ? 'loss' : 'win',
      kills, deaths: 10 + i % 3, assists: i % 5, headshots: Math.floor(kills * .45), rounds: 18,
      rating, rating_display: rounded(rating), rating_color: color(rating), tier: 'rounds',
      formula_version: VERSION, mmr_before: 1200 - i * 2, mmr_after: 1202 - i * 2 };
  });
  const latest = matches.slice(0, count);
  const sum = key => latest.reduce((total, m) => total + m[key], 0);
  const rating = count ? sum('rating') / count : null;
  const summary = { count, rated_count: count, rating,
    rating_display: rating == null ? null : rounded(rating), rating_color: rating == null ? null : color(rating),
    kd: count ? sum('kills') / sum('deaths') : null, kd_infinite: false,
    avg: count ? sum('kills') / count : null, assists: count ? sum('assists') / count : null,
    hs_percent: count ? sum('headshots') / sum('kills') * 100 : null,
    above_100: count ? latest.filter(m => m.rating > 1).length / count * 100 : null,
    above_110: count ? latest.filter(m => m.rating > 1.1).length / count * 100 : null,
    median: count ? 1.058 : null, stddev: count ? .24 : null, tier: count ? 'rounds' : null,
    formula_version: VERSION, formula_versions: count ? [VERSION] : [],
  };
  return { player: { uid, nickname: uid === SECOND ? 'ТЕСТ · Игрок с длинным именем' : uid === GOLD ? 'ТЕСТ · Золотой' : 'ТЕСТ · Север',
      avatar_url: null, current_mmr: { competitive: 1202, allies: 950 } }, mode, scope, summary, matches,
    updated_at: '2026-11-15T09:31:00Z', refresh_state: 'done', source_error: null,
    gaps: scope === 'archive' && count ? [{ detected_at: '2026-11-10T10:00:00Z', after_at: '2026-11-01T00:00:00Z', before_at: '2026-11-03T00:00:00Z', reason: 'Тестовый обнаруженный пробел наблюдений' }] : [],
    history_complete: false, archive_truncated: false };
}

function fixtureState(overrides = {}) {
  return { me: { primary_uid: null, mode: 'competitive' }, sourceAvailable: true,
    failResolve: false, authFail: false, empty: false, profileDelay: 35,
    mutations: [], calls: [], unexpected: [], ...overrides };
}

async function installFixtures(page, state, inside) {
  await page.route('**/telegram-web-app.js*', route => route.abort());
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url());
    const apiIndex = url.pathname.indexOf('/api/');
    const endpoint = url.pathname.slice(apiIndex + 4);
    const method = request.method();
    let body = {};
    try { body = request.postDataJSON() || {}; } catch { /* GET has no JSON body. */ }
    state.calls.push({ endpoint, method, mode: url.searchParams.get('mode'), scope: url.searchParams.get('scope') });
    const respond = (json, status = 200) => route.fulfill({ status, contentType: 'application/json; charset=utf-8',
      headers: { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'Authorization,Content-Type',
        'Access-Control-Allow-Methods': 'GET,POST,PUT,OPTIONS', 'Cache-Control': 'no-store' }, body: JSON.stringify(json) });
    if (method === 'OPTIONS') return respond({});
    if (endpoint === '/status') return respond({ source_available: state.sourceAvailable,
      source_message: state.sourceAvailable ? '' : 'Тестовое состояние: игровой источник временно недоступен.',
      bot_username: 'so2_check_bot', formula_version: VERSION });
    if (endpoint === '/auth') {
      if (state.authFail) return respond({ detail: 'Тестовое состояние: подпись Telegram отклонена.' }, 401);
      if (body.init_data !== INIT_DATA) {
        state.unexpected.push('Auth did not send raw init_data');
        return respond({ detail: 'Fixture expected raw init_data' }, 401);
      }
      return respond({ ...state.me, session_token: TOKEN, expires_in: 43200, token_type: 'bearer' });
    }
    if (request.headers().authorization !== `Bearer ${TOKEN}`) {
      state.unexpected.push(`Missing memory Bearer authorization on ${method} ${endpoint}`);
      return respond({ detail: 'Fixture expected memory Bearer session' }, 401);
    }
    if (endpoint === '/me') {
      if (method === 'PUT') {
        state.mutations.push(structuredClone(body));
        if (body.primary_uid && state.failResolve) return respond({ detail: 'Тестовое состояние: источник статистики временно недоступен.' }, 503);
        if (body.primary_uid) state.me.primary_uid = body.primary_uid;
        if (body.mode) state.me.mode = body.mode;
      }
      return respond(state.me);
    }
    const mode = url.searchParams.get('mode') || state.me.mode;
    const playerMatch = endpoint.match(/^\/players\/(\d+)$/);
    if (playerMatch) {
      await delay(state.profileDelay);
      if (state.failResolve) return respond({ detail: 'Тестовое состояние: источник статистики временно недоступен.' }, 503);
      return respond(profile(playerMatch[1], mode, url.searchParams.get('scope') || 'last20', state.empty));
    }
    if (/^\/players\/\d+\/refresh$/.test(endpoint)) return respond({ state: 'cached', job_id: null });
    if (endpoint.startsWith('/matches/')) {
      const id = decodeURIComponent(endpoint.slice('/matches/'.length));
      const matchMode = id.startsWith('allies-') ? 'allies' : 'competitive';
      const uid = url.searchParams.get('uid') || PRIMARY;
      const match = profile(uid, matchMode).matches.find(m => m.id === id) || profile(uid, matchMode).matches[0];
      const participants = [PRIMARY, SECOND].map((uid, i) => ({ ...match, uid,
        nickname: i ? 'ТЕСТ · Второй участник' : 'ТЕСТ · Север', team: i ? 'B' : 'A', rank: i + 1,
        game_score: 20 + i, adr: null }));
      return respond({ match, participants, explanation: { version: VERSION, tier: 'rounds',
        inputs: { kills: match.kills, deaths: match.deaths, assists: match.assists, rounds: match.rounds },
        parameters: { opponent_adjustment: false }, factors: { kills: .18, deaths: -.07, assists: .04 } }, gaps: [] });
    }
    if (endpoint === '/top') {
      const row = uid => { const p = profile(uid, mode); return { ...p.summary, uid, nickname: p.player.nickname }; };
      const simplified = { ...row('44444444'), uid: '44444444', nickname: 'ТЕСТ · Упрощённая оценка', tier: 'match' };
      return respond({ players: [row(GOLD), row(PRIMARY)], provisional: [row(SECOND), simplified],
        scope: 'Среди отслеживаемых игроков', formula_version: VERSION });
    }
    state.unexpected.push(`Unexpected fixture API ${method} ${endpoint}`);
    return respond({ detail: 'Unknown isolated fixture endpoint' }, 404);
  });
  await page.addInitScript(({ inside, initData }) => {
    window.__uiFixture = true;
    if (!inside) return;
    const events = new Map(), back = new Set();
    window.__uiBack = () => { for (const fn of [...back]) fn(); };
    window.__uiEmit = name => { for (const fn of events.get(name) || []) fn(); };
    window.Telegram = { WebApp: { initData, viewportHeight: innerHeight, viewportStableHeight: innerHeight,
      safeAreaInset: { top: 0, bottom: 18, left: 0, right: 0 },
      contentSafeAreaInset: { top: 24, bottom: 12, left: 0, right: 0 },
      ready() {}, expand() {}, setHeaderColor() {}, setBackgroundColor() {},
      onEvent(name, fn) { if (!events.has(name)) events.set(name, new Set()); events.get(name).add(fn); },
      offEvent(name, fn) { events.get(name)?.delete(fn); },
      BackButton: { show() {}, hide() {}, onClick(fn) { back.add(fn); }, offClick(fn) { back.delete(fn); } },
      HapticFeedback: { selectionChanged() {} }, openTelegramLink() {},
    } };
  }, { inside, initData: INIT_DATA });
}

async function noHorizontal(page, label, width) {
  await page.evaluate(() => document.fonts.ready);
  const dimensions = await page.evaluate(() => ({ width: innerWidth,
    document: document.documentElement.scrollWidth, body: document.body.scrollWidth,
    overflowing: [...document.querySelectorAll('body *')].filter(e => {
      const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
      return r.width > 0 && s.visibility !== 'hidden' && (r.right > innerWidth + 2 || r.left < -2) &&
        s.position !== 'fixed' && !e.closest('.recharts-wrapper');
    }).slice(0, 8).map(e => ({ tag: e.tagName, class: e.className?.baseVal ?? e.className })) }));
  assert.ok(dimensions.document <= dimensions.width + 1 && dimensions.body <= dimensions.width + 1,
    `${label} @${width}: horizontal overflow ${JSON.stringify(dimensions)}`);
  await page.screenshot({ path: path.join(OUT, `${width}-${label}.png`), fullPage: true, animations: 'disabled' });
  results.push({ width, screen: label, horizontal_scroll: false });
}

async function newCase(browser, width, state, inside = true) {
  const context = await browser.newContext({ viewport: { width, height: width >= 1000 ? 900 : 844 },
    deviceScaleFactor: 1, isMobile: width < 1000, hasTouch: width < 1000, locale: 'ru-RU',
    reducedMotion: 'reduce', colorScheme: 'dark' });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await installFixtures(page, state, inside);
  await page.goto(UI_URL, { waitUntil: 'domcontentloaded' });
  return { page, context, errors, state };
}

async function closeCase(item) {
  assert.deepEqual(item.state.unexpected, [], 'Unexpected API calls or missing Bearer header');
  assert.deepEqual(item.errors, [], 'Uncaught browser exceptions');
  await item.context.close();
}

async function runWidth(browser, width) {
  let item;
  try {
    item = await newCase(browser, width, fixtureState(), false);
    await expect(item.page.getByRole('link', { name: 'Открыть через Telegram' })).toBeVisible();
    assert.equal(item.state.calls.some(c => c.endpoint === '/auth'), false, 'Outside Telegram must not authenticate');
    await noHorizontal(item.page, 'intro', width);
    await closeCase(item); item = null;

    const state = fixtureState();
    item = await newCase(browser, width, state);
    const page = item.page;
    await expect(page.getByLabel('Ваш игровой ID Standoff 2')).toBeVisible();
    await noHorizontal(page, 'onboarding', width);
    await expect(page.getByLabel('Ваш игровой ID Standoff 2')).toHaveAttribute('inputmode', 'numeric');
    state.failResolve = true;
    await page.getByLabel('Ваш игровой ID Standoff 2').fill(PRIMARY);
    await page.getByRole('button', { name: 'Продолжить', exact: true }).click();
    await expect(page.getByRole('alert')).toContainText('временно недоступен');
    assert.equal(state.me.primary_uid, null, 'Failed source lookup must not bind primary ID');
    await noHorizontal(page, 'source-error', width);
    state.failResolve = false;
    await page.getByRole('button', { name: 'Продолжить', exact: true }).click();
    await expect(page.locator('.rating-large')).toBeVisible();
    assert.equal(state.me.primary_uid, PRIMARY);
    await expect(page.locator('.rating-large')).toHaveText('1.10');
    await expect(page.locator('.rating-large')).toHaveClass(/\bwhite\b/);
    await expect(page.locator('.stability-row')).toContainText('65%');
    assert.ok(!/6[\s\u00a0\u202f]*500/.test(await page.locator('.stability-row').innerText()), 'Stability percent was multiplied twice');
    await noHorizontal(page, 'profile', width);

    const dots = page.locator('.recharts-line-dots circle');
    await expect(dots.first()).toBeAttached();
    await dots.nth(5).scrollIntoViewIfNeeded();
    await dots.nth(5).click();
    await expect(page.locator('.selected-match, .chart-tooltip').first()).toBeVisible();
    results.push({ width, check: 'chart_touch_point', passed: true });
    await noHorizontal(page, 'chart-point', width);
    await page.getByRole('button', { name: 'Сохранённая история', exact: true }).click();
    await expect(page.locator('.chart-warning')).toContainText('пропуски');
    await expect(page.locator('.rating-large')).toHaveText('1.10');
    await noHorizontal(page, 'archive-gap', width);
    await page.getByRole('button', { name: 'Последние 20', exact: true }).click();

    const nav = title => page.getByRole('navigation', { name: 'Основная навигация' }).getByRole('button', { name: title, exact: true });
    await nav('Матчи').click();
    await expect(page.locator('.match-card')).toHaveCount(20);
    const boundaries = [['0.89', 'red'], ['0.90', 'white'], ['1.10', 'white'], ['1.11', 'green'], ['1.80', 'green'], ['1.81', 'gold']];
    for (let i = 0; i < boundaries.length; i++) {
      const rating = page.locator('.match-card').nth(i).locator('.rating');
      await expect(rating).toHaveText(boundaries[i][0]);
      await expect(rating).toHaveClass(new RegExp(`\\b${boundaries[i][1]}\\b`));
    }
    results.push({ width, check: 'rounded_rating_color_boundaries', passed: true });
    await noHorizontal(page, 'matches', width);
    await page.locator('.match-card').first().click();
    await expect(page.getByRole('heading', { name: 'Участники матча' })).toBeVisible();
    await noHorizontal(page, 'match-detail', width);
    const primaryWritesBeforeNavigation = state.mutations.filter(m => m.primary_uid).length;
    await page.locator('.participant-row').filter({ hasText: 'Второй участник' }).click();
    await expect(page.locator('.copy-id')).toContainText(SECOND);
    assert.equal(state.me.primary_uid, PRIMARY, 'Participant navigation changed primary ID');
    await page.evaluate(() => window.__uiBack());
    await expect(page.locator('.copy-id')).toContainText(PRIMARY);
    results.push({ width, check: 'telegram_back_button', passed: true });

    await page.getByRole('button', { name: 'Найти игрока', exact: true }).click();
    await page.getByLabel('Игровой ID', { exact: true }).fill(SECOND);
    await noHorizontal(page, 'search', width);
    await page.getByRole('button', { name: 'Найти', exact: true }).click();
    await expect(page.locator('.copy-id')).toContainText(SECOND);
    assert.equal(state.me.primary_uid, PRIMARY, 'Search changed primary ID');
    assert.equal(state.mutations.filter(m => m.primary_uid).length, primaryWritesBeforeNavigation, 'Read navigation wrote primary ID');
    await noHorizontal(page, 'other-profile', width);
    await page.getByRole('button', { name: 'Мой аккаунт', exact: true }).click();
    await expect(page.locator('.copy-id')).toContainText(PRIMARY);
    results.push({ width, check: 'primary_id_survives_search_and_participant_navigation', passed: true });

    await page.getByRole('button', { name: /Напарники/ }).click();
    await expect(page.getByRole('button', { name: /Напарники/ })).toHaveAttribute('aria-pressed', 'true');
    await nav('Матчи').click();
    await expect(page.locator('.match-card')).toHaveCount(20);
    assert.equal(state.me.mode, 'allies');
    assert.ok(state.calls.some(c => c.endpoint === `/players/${PRIMARY}` && c.mode === 'allies'));
    await nav('Сравнение').click();
    await page.getByLabel('ID второго игрока').fill(SECOND);
    await page.getByRole('button', { name: 'Сравнить', exact: true }).click();
    await expect(page.locator('.comparison-table')).toBeVisible();
    const sample = page.locator('.comparison-row').filter({ hasText: 'Учтено матчей' });
    await expect(sample).toContainText('20 / 20'); await expect(sample).toContainText('5 / 20');
    await expect(page.locator('.comparison-row').filter({ hasText: 'Матчей выше 1.00' })).toContainText('65%');
    await expect(page.getByRole('button', { name: /Напарники/ })).toHaveAttribute('aria-pressed', 'true');
    await noHorizontal(page, 'comparison', width);
    await nav('Топ').click();
    await expect(page.getByRole('heading', { name: 'Топ наблюдаемых' })).toBeVisible();
    await expect(page.locator('.leaderboard-row').first()).toBeVisible();
    await expect(page.locator('.top-footnote')).toContainText('не мировой рейтинг');
    await noHorizontal(page, 'leaderboard', width);

    await page.getByRole('button', { name: 'Настройки основного ID', exact: true }).click();
    await expect(page.getByLabel('Сменить основной игровой ID')).toHaveValue(PRIMARY);
    await noHorizontal(page, 'settings', width);
    await page.getByLabel('Сменить основной игровой ID').focus();
    await page.evaluate(() => { window.Telegram.WebApp.viewportHeight = innerHeight - 300; window.__uiEmit('viewportChanged'); });
    await expect(page.locator('body')).toHaveClass(/keyboard-open/);
    await expect(page.getByLabel('Сменить основной игровой ID')).toBeVisible();
    await noHorizontal(page, 'keyboard-simulated', width);
    await page.evaluate(() => { window.Telegram.WebApp.viewportHeight = innerHeight; window.__uiEmit('viewportChanged'); });
    await page.getByLabel('Сменить основной игровой ID').fill(SECOND);
    await page.getByRole('button', { name: 'Сохранить', exact: true }).click();
    await expect(page.locator('.copy-id')).toContainText(SECOND);
    assert.equal(state.me.primary_uid, SECOND, 'Explicit settings change must persist primary ID');
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.locator('.copy-id')).toContainText(SECOND);
    await expect(page.getByRole('button', { name: /Напарники/ })).toHaveAttribute('aria-pressed', 'true');
    results.push({ width, check: 'settings_and_mode_persist_on_reload', passed: true });
    state.sourceAvailable = false;
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.locator('.source-banner')).toContainText('не подключён');
    await expect(page.locator('.rating-large')).toBeVisible();
    await noHorizontal(page, 'cached-source-unavailable', width);
    await closeCase(item); item = null;

    item = await newCase(browser, width, fixtureState({ me: { primary_uid: PRIMARY, mode: 'competitive' }, empty: true }));
    await expect(item.page.locator('.rating-large')).toHaveText('—');
    await expect(item.page.getByRole('heading', { name: 'График начнётся с первого матча' })).toBeVisible();
    await noHorizontal(item.page, 'no-matches', width);
    await closeCase(item); item = null;

    item = await newCase(browser, width, fixtureState({ authFail: true }));
    await expect(item.page.getByRole('alert')).toContainText('подпись Telegram отклонена');
    assert.equal(item.state.calls.some(c => c.endpoint === '/me'), false, 'Auth failure must not fetch settings');
    await noHorizontal(item.page, 'auth-error', width);
    await closeCase(item); item = null;

    item = await newCase(browser, width, fixtureState({ me: { primary_uid: PRIMARY, mode: 'competitive' }, profileDelay: 1200 }));
    await expect(item.page.getByText('Загружаем данные…', { exact: true })).toBeVisible();
    await noHorizontal(item.page, 'loading', width);
    await expect(item.page.locator('.rating-large')).toBeVisible();
    await closeCase(item); item = null;
  } catch (error) {
    failures.push({ width, message: String(error?.stack || error) });
    if (item) {
      await item.page.screenshot({ path: path.join(OUT, `${width}-FAILURE.png`), fullPage: true }).catch(() => {});
      failures.push(...item.state.unexpected.map(message => ({ width, message })), ...item.errors.map(message => ({ width, message })));
      await item.context.close().catch(() => {});
    }
  }
}

(async () => {
  await fs.mkdir(OUT, { recursive: true });
  const browser = await (ENGINE === 'webkit' ? webkit : chromium).launch({ headless: true });
  try {
    for (const width of [360, 390, 430, 1280]) {
      console.log(`Isolated UI fixtures: checking ${width}px`);
      await runWidth(browser, width);
    }
  } finally { await browser.close(); }
  const report = { environment: 'isolated-local-browser-fixtures', ui_url: UI_URL,
    checked_at: new Date().toISOString(), notes, results, failures,
    passed: failures.length === 0, checks: results.length };
  await fs.writeFile(path.join(OUT, 'report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ passed: report.passed, checks: results.length, failures: failures.length,
    report: path.join(OUT, 'report.json'), limitations: notes }));
  for (const failure of failures) console.error(`UI QA ${failure.width}px: ${failure.message}`);
  if (failures.length) process.exitCode = 1;
})().catch(async error => {
  console.error(`Could not execute isolated browser QA: ${error.message}`);
  process.exitCode = 1;
});

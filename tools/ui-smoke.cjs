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
const WIDTHS = (process.env.UI_WIDTHS || '360,390,430,1280').split(',').map(Number);
if (!WIDTHS.length || new Set(WIDTHS).size !== WIDTHS.length || WIDTHS.some(width => ![360, 390, 430, 1280].includes(width))) {
  throw new Error('UI_WIDTHS must select distinct supported QA widths.');
}
const REPORT_NAME = process.env.UI_WIDTHS ? `report-${WIDTHS.join('-')}.json` : 'report.json';

const UI_URL = process.env.UI_URL || 'http://127.0.0.1:5173';
if (!['localhost', '127.0.0.1', '[::1]'].includes(new URL(UI_URL).hostname)) {
  throw new Error('Fixture QA refuses non-local UI_URL. Never run intercepted auth tests against production.');
}
const OUT = path.resolve(__dirname, '../artifacts/ui', ENGINE);
const PRIMARY = '11111111', SECOND = '22222222';
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
    const rating = uid === SECOND ? 1.2 : values[i % values.length];
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
  return { player: { uid, nickname: uid === SECOND ? 'ТЕСТ · Игрок с длинным именем' : 'ТЕСТ · Север',
      avatar_url: null, current_mmr: { competitive: 1202, allies: 950 } }, mode, scope, summary, matches,
    updated_at: '2026-11-15T09:31:00Z', refresh_state: 'done', source_error: null,
    gaps: scope === 'archive' && count ? [{ detected_at: '2026-11-10T10:00:00Z', after_at: '2026-11-01T00:00:00Z', before_at: '2026-11-03T00:00:00Z', reason: 'Тестовый обнаруженный пробел наблюдений' }] : [],
    history_complete: false, archive_truncated: false };
}

function personalSnapshot({ source = 'screenshot', mode = 'competitive', id = 'screenshot:1', kills = 22, deaths = 7, assists = 0, nickname = 'ТЕСТ · Подтверждённый игрок', requestedNickname = nickname } = {}) {
  const norms = mode === 'allies' ? [10, 10, 2] : [15, 15, 4];
  const calculate = (k, d, a) => Math.max(0, 1 + .65 * (k / norms[0] - 1) - .25 * (d / norms[1] - 1) + .1 * (a / norms[2] - 1));
  const rating = calculate(kills, deaths, assists), count = mode === 'allies' ? 4 : 10;
  const players = Array.from({ length: count }, (_, index) => {
    const own = index === 0, partial = index === count - 1;
    const k = own ? kills : partial ? null : 12, d = own ? deaths : 11, a = own ? assists : 3;
    const playerRating = k == null ? null : calculate(k, d, a);
    return { nickname: own ? nickname : `ТЕСТ · Участник ${index + 1}`, aliases: [], uid: source === 'automatic' ? String(60000000 + index) : null,
      team: index < count / 2 ? 'ct' : 't', kills: k, deaths: d, assists: a,
      game_score: partial ? null : own ? 46 : 27, rank: index % (count / 2) + 1,
      rating: playerRating, rating_display: playerRating == null ? null : rounded(playerRating), rating_color: playerRating == null ? null : color(playerRating),
      selected: own, user_confirmed: own && source === 'screenshot' };
  });
  return { id, screenshot_id: source === 'screenshot' ? Number(id.split(':')[1]) : null,
    source, source_label: source === 'screenshot' ? 'Со скриншота' : 'Из игровой истории', mode,
    match_id: '2188356974', map: mode === 'allies' ? 'Rust' : 'Sandstone', score: [8, 6],
    ended_at: source === 'automatic' ? '2026-11-15T09:30:00Z' : null, date_label: source === 'screenshot' ? 'Вчера в 13:41' : null,
    loaded_at: '2026-11-16T02:00:00Z', nickname, requested_nickname: requestedNickname, player_uid: source === 'automatic' ? PRIMARY : null,
    kills, deaths, assists, rating, rating_display: rounded(rating), rating_color: color(rating), formula_version: VERSION,
    rating_tier: 'match', method_note: 'Единый расчёт по K/D/A', selected_index: 0, players,
    original_rating: source === 'automatic' ? 1.8 : null, original_rating_tier: source === 'automatic' ? 'rounds' : null };
}

function fixtureState(overrides = {}) {
  return { me: { primary_uid: null, mode: 'competitive' }, sourceAvailable: true,
    failResolve: false, authFail: false, empty: false, profileDelay: 35,
    personal: { competitive: { best: null, matches: [] }, allies: { best: null, matches: [] } }, bestFail: false,
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
    if (endpoint === '/me/best') {
      if (state.bestFail) return respond({ detail: 'Тестовое состояние: сохранённый рекорд временно недоступен.' }, 503);
      return respond({ best: state.personal[mode].best });
    }
    if (endpoint === '/me/screenshot-matches') {
      const saved = state.personal[mode];
      return respond({ matches: saved.matches, total: saved.total ?? saved.matches.length, limit: 50, truncated: saved.truncated ?? false });
    }
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
    await expect(page.getByRole('heading', { name: 'Лучший матч ещё не сохранён' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Разобрать скриншот в боте', exact: true })).toHaveAttribute('href', 'https://t.me/so2_check_bot?start=match');
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
    const redStyle = await page.locator('.match-card').nth(0).locator('.rating').evaluate(e => getComputedStyle(e).color);
    const greenStyle = await page.locator('.match-card').nth(3).locator('.rating').evaluate(e => getComputedStyle(e).color);
    assert.equal(redStyle, 'rgb(255, 82, 97)');
    assert.equal(greenStyle, 'rgb(37, 229, 130)');
    const gold = page.locator('.match-card').nth(5).locator('.rating');
    const reducedGold = await gold.evaluate(e => ({ background: getComputedStyle(e).backgroundImage, animation: getComputedStyle(e).animationName }));
    assert.ok(reducedGold.background.includes('linear-gradient'));
    assert.equal(reducedGold.animation, 'none');
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    assert.equal(await gold.evaluate(e => getComputedStyle(e).animationName), 'rating-gold-sheen');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    results.push({ width, check: 'saturated_rating_colors_gold_sheen_and_reduced_motion', passed: true });
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
    const navigation = page.getByRole('navigation', { name: 'Основная навигация' });
    await expect(navigation.getByRole('button')).toHaveCount(3);
    assert.deepEqual(await navigation.getByRole('button').allTextContents(), ['Профиль', 'Матчи', 'Сравнение']);
    await expect(navigation.getByRole('button', { name: 'Топ', exact: true })).toHaveCount(0);
    assert.equal(state.calls.some(call => call.endpoint === '/top'), false, 'Removed leaderboard must never be requested');
    await page.evaluate(() => {
      const removedView = { tab: 'top', uid: null, match: null, settings: false, search: false };
      window.history.pushState(removedView, '');
      window.dispatchEvent(new PopStateEvent('popstate', { state: removedView }));
    });
    await expect(nav('Профиль')).toHaveAttribute('aria-current', 'page');
    await expect(page.locator('.rating-large')).toBeVisible();
    await expect(page.getByRole('button', { name: /Напарники/ })).toHaveAttribute('aria-pressed', 'true');
    results.push({ width, check: 'three_tabs_and_removed_tab_history_fallback', passed: true });
    await noHorizontal(page, 'navigation-without-top', width);

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

async function runPersonalWidth(browser, width) {
  let item;
  try {
    let original = personalSnapshot({ nickname: 'harny', requestedNickname: 'hamy' });
    const alliesUpload = personalSnapshot({ mode: 'allies', id: 'screenshot:3', kills: 12, deaths: 9, assists: 2 });
    const alliesBest = personalSnapshot({ source: 'automatic', mode: 'allies', id: 'automatic:allies-best', kills: 15, deaths: 8, assists: 4 });
    const state = fixtureState({ sourceAvailable: false, personal: {
      competitive: { best: original, matches: [original] },
      allies: { best: alliesBest, matches: [alliesUpload], total: 52, truncated: true },
    } });
    item = await newCase(browser, width, state);
    const page = item.page;
    const navigation = page.getByRole('navigation', { name: 'Основная навигация' });
    const nav = name => navigation.getByRole('button', { name, exact: true });
    const best = page.locator('.personal-best');
    await expect(page.locator('.source-banner')).toContainText('не подключён');
    await expect(page.getByLabel('Ваш игровой ID Standoff 2')).toBeVisible();
    await expect(best.locator('.saved-rating')).toHaveText('1.34');
    await expect(best.locator('.saved-source')).toHaveText('Со скриншота');
    await expect(best.locator('.saved-nickname')).toHaveText('hamy');
    await expect(best.locator('.saved-ocr-nickname')).toHaveText('На скриншоте распознано: harny');
    assert.equal(state.calls.some(call => call.endpoint.startsWith('/players/')), false, 'Private record without primary ID must not wait for game lookup');
    await noHorizontal(page, 'best-without-primary-id', width);
    await best.getByRole('button', { name: 'Открыть лучший матч Sandstone', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Участники матча', exact: true })).toBeVisible();
    await expect(page.locator('.saved-player-row')).toHaveCount(10);
    const ownRow = page.locator('.saved-player-row.is-selected');
    await expect(ownRow).toHaveCount(1);
    await expect(ownRow).toContainText('Ваша строка · подтверждена');
    await expect(ownRow).toContainText('Очки: 46');
    await expect(ownRow).toContainText('#1');
    await expect(ownRow.locator('.saved-player-name strong')).toContainText('harny');
    await expect(ownRow.locator('.saved-player-name strong')).not.toContainText('hamy');
    await expect(page.locator('.saved-provenance')).toContainText('могут содержать ошибки');
    const missing = page.locator('.saved-player-row').last().locator('.saved-missing');
    await expect(missing).toHaveText('—');
    await expect(missing).toHaveAttribute('title', 'Нет данных');
    await expect(missing).toHaveAttribute('aria-label', 'Нет данных');
    await expect(page.locator('.saved-data-legend')).toHaveText('— нет данных');
    await expect(page.locator('.saved-detail-identity strong')).toHaveText('hamy');
    await expect(page.locator('.saved-detail-identity .saved-ocr-nickname')).toHaveText('На скриншоте распознано: harny');
    await expect(page.locator('.saved-detail-hero').getByText('Sandstone', { exact: true })).toHaveCount(1);
    await expect(page.locator('.saved-detail-hero')).toContainText('Единый расчёт по K/D/A');
    await noHorizontal(page, 'saved-scoreboard-mobile', width);
    original = personalSnapshot({ kills: 23, nickname: 'harny', requestedNickname: 'hamy' });
    state.personal.competitive = { best: original, matches: [original] };
    await page.evaluate(() => window.__uiEmit('activated'));
    await expect(page.locator('.saved-detail-hero .saved-rating')).toHaveText('1.38');
    await expect(page.locator('.saved-detail-kda')).toContainText('23 / 7 / 0');
    results.push({ width, check: 'opened_saved_detail_refreshes_corrected_snapshot', passed: true });
    await page.evaluate(() => window.__uiBack());
    await expect(best).toBeVisible();

    const replacement = personalSnapshot({ id: 'screenshot:2', kills: 40, deaths: 7, assists: 10, nickname: 'ТЕСТ · Новый личный рекорд' });
    state.personal.competitive = { best: replacement, matches: [replacement, original] };
    const recordsBeforeReturn = state.calls.filter(call => call.endpoint === '/me/best').length;
    await page.evaluate(() => { window.__uiEmit('activated'); window.dispatchEvent(new Event('focus')); document.dispatchEvent(new Event('visibilitychange')); });
    await expect(best.locator('.saved-rating')).toHaveText('2.37');
    await expect(best.locator('.saved-rating')).toHaveClass(/\bgold\b/);
    assert.equal(state.calls.filter(call => call.endpoint === '/me/best').length, recordsBeforeReturn + 1, 'Return-to-app events should coalesce into one private refresh');
    assert.equal(state.calls.some(call => call.endpoint.startsWith('/players/')), false, 'Record refresh must stay independent of game source');
    results.push({ width, check: 'best_replaced_on_return_without_primary_or_game_source', passed: true });
    await noHorizontal(page, 'best-updated-on-return', width);

    await page.getByRole('button', { name: /Напарники/ }).click();
    await expect(best.locator('.saved-rating')).toHaveText('1.48');
    await expect(best.locator('.saved-source')).toHaveText('Из игровой истории');
    await expect(best).toContainText('Rust');
    await nav('Матчи').click();
    await expect(page.locator('.uploaded-match-card')).toHaveCount(1);
    await expect(page.locator('.saved-upload-limit')).toHaveText('Показаны последние 1 из 52 загруженных матчей.');
    await expect(page.locator('.match-card')).toHaveCount(0);
    await expect(page.locator('.uploaded-matches')).toContainText('не входит автоматически в последние 20');
    await noHorizontal(page, 'uploaded-matches-separate-from-game', width);
    await page.locator('.uploaded-match-card').click();
    await expect(page.locator('.saved-player-row')).toHaveCount(4);
    await expect(page.locator('.saved-detail-hero')).toContainText('Напарники');
    await page.evaluate(() => window.__uiBack());
    await page.getByRole('button', { name: /Соревновательный/ }).click();

    state.me.primary_uid = PRIMARY;
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.locator('.rating-large')).toHaveText('1.10');
    await expect(best.locator('.saved-rating')).toHaveText('2.37');
    const autoRecord = personalSnapshot({ source: 'automatic', id: 'automatic:competitive-record', kills: 45, deaths: 8, assists: 7, nickname: 'ТЕСТ · Автоматический игрок', requestedNickname: null });
    state.personal.competitive.best = autoRecord;
    await best.getByRole('button', { name: 'Обновить лучший матч', exact: true }).click();
    await expect(best.locator('.saved-rating')).toHaveText('2.49');
    await expect(best.locator('.saved-source')).toHaveText('Из игровой истории');
    await expect(best.locator('.saved-nickname')).toHaveText('ТЕСТ · Автоматический игрок');
    await expect(best.locator('.saved-ocr-nickname')).toHaveCount(0);
    await expect(page.locator('.rating-large')).toHaveText('1.10');
    await noHorizontal(page, 'automatic-record-keeps-last20-average', width);

    await page.getByRole('button', { name: 'Найти игрока', exact: true }).click();
    await page.getByLabel('Игровой ID', { exact: true }).fill(SECOND);
    await page.getByRole('button', { name: 'Найти', exact: true }).click();
    await expect(page.locator('.copy-id')).toContainText(SECOND);
    await expect(best).toHaveCount(0);
    await nav('Матчи').click();
    await expect(page.locator('.uploaded-matches')).toHaveCount(0);
    assert.equal(state.me.primary_uid, PRIMARY, 'Private record navigation must never change primary ID');
    await page.getByRole('button', { name: 'Мой аккаунт', exact: true }).click();
    await expect(best.locator('.saved-rating')).toHaveText('2.49');
    results.push({ width, check: 'private_records_hidden_on_other_public_uid', passed: true });

    await page.getByRole('button', { name: 'Настройки основного ID', exact: true }).click();
    await page.getByLabel('Сменить основной игровой ID').fill(SECOND);
    state.personal.competitive.best = replacement;
    await page.getByRole('button', { name: 'Сохранить', exact: true }).click();
    await expect(page.locator('.copy-id')).toContainText(SECOND);
    await expect(best.locator('.saved-rating')).toHaveText('2.37');
    await expect(best.locator('.saved-source')).toHaveText('Со скриншота');
    await page.getByRole('button', { name: 'Настройки основного ID', exact: true }).click();
    await page.getByLabel('Сменить основной игровой ID').fill(PRIMARY);
    state.personal.competitive.best = autoRecord;
    await page.getByRole('button', { name: 'Сохранить', exact: true }).click();
    await expect(page.locator('.copy-id')).toContainText(PRIMARY);
    await expect(best.locator('.saved-rating')).toHaveText('2.49');
    results.push({ width, check: 'record_refreshes_when_primary_uid_changes', passed: true });

    state.failResolve = true;
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('alert')).toContainText('временно недоступен');
    await expect(best.locator('.saved-rating')).toHaveText('2.49');
    await best.getByRole('button', { name: 'Открыть лучший матч Sandstone', exact: true }).click();
    await expect(page.locator('.saved-provenance')).toContainText('сохранён из игровой истории');
    await expect(page.locator('.saved-player-row')).toHaveCount(10);
    await noHorizontal(page, 'personal-record-while-source-errors', width);
    await closeCase(item); item = null;
  } catch (error) {
    failures.push({ width, message: String(error?.stack || error) });
    if (item) {
      await item.page.screenshot({ path: path.join(OUT, `${width}-PERSONAL-FAILURE.png`), fullPage: true }).catch(() => {});
      failures.push(...item.state.unexpected.map(message => ({ width, message })), ...item.errors.map(message => ({ width, message })));
      await item.context.close().catch(() => {});
    }
  }
}

(async () => {
  await fs.mkdir(OUT, { recursive: true });
  const browser = await (ENGINE === 'webkit' ? webkit : chromium).launch({ headless: true });
  try {
    for (const width of WIDTHS) {
      console.log(`Isolated UI fixtures: checking ${width}px`);
      await runWidth(browser, width);
      await runPersonalWidth(browser, width);
    }
  } finally { await browser.close(); }
  const report = { environment: 'isolated-local-browser-fixtures', ui_url: UI_URL,
    checked_at: new Date().toISOString(), notes, results, failures,
    passed: failures.length === 0, checks: results.length };
  await fs.writeFile(path.join(OUT, REPORT_NAME), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ passed: report.passed, checks: results.length, failures: failures.length,
    report: path.join(OUT, REPORT_NAME), limitations: notes }));
  for (const failure of failures) console.error(`UI QA ${failure.width}px: ${failure.message}`);
  if (failures.length) process.exitCode = 1;
})().catch(async error => {
  console.error(`Could not execute isolated browser QA: ${error.message}`);
  process.exitCode = 1;
});

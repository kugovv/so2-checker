import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { ArrowDownUp, ArrowLeft, ArrowRight, Check, ChevronRight, Copy, Crosshair, ExternalLink, History, Info, LoaderCircle, Medal, RefreshCw, Search, Settings2, ShieldCheck, Target, TrendingUp, UserRound, UsersRound, X, WifiOff, Swords } from 'lucide-react';
import { api, errorText, setSessionToken } from './api';
import { date, modeLabel, number, ratingColor, ratingText, stat } from './format';
import type { Detail, Match, Me, Mode, Profile, Scope, Status, Tab, Top, TopPlayer } from './types';
import { HistoryChart } from './Chart';

type View = { tab: Tab; uid: string | null; match: string | null; settings: boolean; search: boolean };
const initialView: View = { tab: 'profile', uid: null, match: null, settings: false, search: false };
const tabs: { id: Tab; title: string; icon: typeof UserRound }[] = [
  { id: 'profile', title: 'Профиль', icon: UserRound }, { id: 'matches', title: 'Матчи', icon: Swords },
  { id: 'compare', title: 'Сравнение', icon: ArrowDownUp }, { id: 'top', title: 'Топ', icon: Medal },
];

export function Rating({ value, display, large = false }: { value: number | null; display?: string | null; large?: boolean }) {
  return <span className={`rating ${ratingColor(display ? Number(display) : value) ?? ''} ${large ? 'rating-large' : ''}`}>{display ?? ratingText(value)}</span>;
}
function Spinner({ text = 'Загружаем данные…' }: { text?: string }) { return <div className="loading" role="status"><LoaderCircle size={21} className="spin" /><span>{text}</span></div>; }
export function Empty({ icon = <Target size={28} />, title, children, action }: { icon?: ReactNode; title: string; children: ReactNode; action?: ReactNode }) {
  return <div className="empty panel"><div className="empty-icon">{icon}</div><h2>{title}</h2><p>{children}</p>{action}</div>;
}
function ErrorBox({ error, retry }: { error: string; retry?: () => void }) { return <div className="error-box" role="alert"><WifiOff size={19} /><div><p>{error}</p>{retry && <button className="text-button" onClick={retry}>Повторить запрос <RefreshCw size={13} /></button>}</div></div>; }
function UidForm({ label, button, value = '', loading, onSubmit }: { label: string; button: string; value?: string; loading: boolean; onSubmit: (uid: string) => void }) {
  const [input, setInput] = useState(value);
  const [validation, setValidation] = useState('');
  function submit(e: FormEvent) {
    e.preventDefault();
    const uid = input.trim();
    if (!/^\d{1,20}$/.test(uid)) { setValidation('Введите игровой ID: только цифры, до 20 знаков.'); return; }
    setValidation(''); onSubmit(uid);
  }
  return <form onSubmit={submit} className="uid-form"><label htmlFor={`uid-${button}`}>{label}</label><div className="input-row"><span className="input-prefix">ID</span><input id={`uid-${button}`} inputMode="numeric" pattern="[0-9]*" autoComplete="off" enterKeyHint="search" placeholder="Введите игровой ID" maxLength={20} value={input} onChange={e => setInput(e.target.value.replace(/\D/g, ''))} aria-describedby={validation ? `invalid-${button}` : undefined} /><button className="button orange" disabled={loading || !input.trim()} type="submit">{loading ? <LoaderCircle className="spin" size={18} /> : <><span>{button}</span><ArrowRight size={17} /></>}</button></div>{validation && <p id={`invalid-${button}`} className="field-error">{validation}</p>}</form>;
}

function Intro({ bot, authError, retry }: { bot: string; authError?: string; retry?: () => void }) {
  return <main className="intro"><div className="eyebrow"><span className="orange-dot" /> ОПЕРАТИВНАЯ СТАТИСТИКА</div><div className="intro-emblem" aria-hidden="true"><Crosshair strokeWidth={1} size={92} /><span>SO2</span></div><h1>Знай свою<br /><span>реальную форму.</span></h1><p className="intro-description">Последние 20 матчей Standoff 2.<br />Собственный рейтинг. Понятный разбор игры.</p><div className="intro-features"><div><Target size={21} /><strong>Личная форма</strong><span>Каждый матч имеет вес</span></div><div><UsersRound size={21} /><strong>Сравнение</strong><span>Игроки в одном режиме</span></div><div><TrendingUp size={21} /><strong>Динамика</strong><span>История под наблюдением</span></div></div>{authError ? <ErrorBox error={authError} retry={retry} /> : <div className="telegram-note"><ShieldCheck size={18} /><p>Войдите через Telegram, чтобы сохранить основной ID и открыть статистику.</p></div>}<a className="button orange full" href={`https://t.me/${bot.replace('@', '')}`}><ExternalLink size={18} />Открыть через Telegram</a><p className="micro intro-footnote">Соревновательный · Напарники<br />Независимый проект. Не связан с AXLEBOLT.</p></main>;
}

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [auth, setAuth] = useState<'loading' | 'outside' | 'ready' | 'error'>('loading');
  const [authError, setAuthError] = useState('');
  const [view, setView] = useState<View>(initialView);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [settingError, setSettingError] = useState('');
  const [saving, setSaving] = useState(false);
  const [epoch, setEpoch] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [toast, setToast] = useState('');
  const [scope, setScope] = useState<Scope>('last20');
  const [archive, setArchive] = useState<Profile | null>(null);
  const [archiveError, setArchiveError] = useState('');
  const viewRef = useRef(view); viewRef.current = view;
  const meRef = useRef(me); meRef.current = me;
  const mode = me?.mode ?? 'competitive';
  const uid = view.uid ?? me?.primary_uid ?? null;
  const bot = status?.bot_username ?? 'so2_check_bot';

  const navigate = useCallback((next: Partial<View>, replace = false) => {
    setView(previous => { const state = { ...previous, ...next }; window.history[replace ? 'replaceState' : 'pushState'](state, ''); return state; });
    window.Telegram?.WebApp?.HapticFeedback?.selectionChanged();
    window.scrollTo({ top: 0, behavior: 'instant' });
  }, []);
  const goBack = useCallback(() => {
    const v = viewRef.current;
    if (v.match) navigate({ match: null }, true);
    else if (v.settings) navigate({ settings: false }, true);
    else if (v.search) navigate({ search: false }, true);
    else if (v.uid && v.uid !== meRef.current?.primary_uid) navigate({ uid: meRef.current?.primary_uid ?? null, tab: 'profile' }, true);
    else navigate({ tab: 'profile', uid: meRef.current?.primary_uid ?? null }, true);
  }, [navigate]);

  useEffect(() => {
    window.history.replaceState(initialView, '');
    const pop = (e: PopStateEvent) => setView(e.state?.tab ? e.state : initialView);
    window.addEventListener('popstate', pop);
    const tg = window.Telegram?.WebApp;
    try { tg?.ready(); tg?.expand(); tg?.setHeaderColor('#171A1D'); tg?.setBackgroundColor('#171A1D'); } catch { /* Older Telegram versions keep their own chrome. */ }
    const resize = () => {
      const height = Math.min(tg?.viewportHeight || window.innerHeight, window.visualViewport?.height || window.innerHeight);
      document.documentElement.style.setProperty('--app-height', `${height}px`);
      document.documentElement.style.setProperty('--safe-top', `${Math.max(tg?.safeAreaInset?.top ?? 0, tg?.contentSafeAreaInset?.top ?? 0)}px`);
      document.documentElement.style.setProperty('--safe-bottom', `${Math.max(tg?.safeAreaInset?.bottom ?? 0, tg?.contentSafeAreaInset?.bottom ?? 0)}px`);
      document.body.classList.toggle('keyboard-open', window.innerHeight - height > 140);
    };
    resize(); window.visualViewport?.addEventListener('resize', resize); window.addEventListener('resize', resize);
    tg?.onEvent('viewportChanged', resize); tg?.onEvent('safeAreaChanged', resize); tg?.onEvent('contentSafeAreaChanged', resize);
    return () => { window.removeEventListener('popstate', pop); window.removeEventListener('resize', resize); window.visualViewport?.removeEventListener('resize', resize); tg?.offEvent('viewportChanged', resize); tg?.offEvent('safeAreaChanged', resize); tg?.offEvent('contentSafeAreaChanged', resize); };
  }, []);
  useEffect(() => {
    const tg = window.Telegram?.WebApp;
    if (view.match || view.settings || view.search || view.tab !== 'profile' || (view.uid && view.uid !== me?.primary_uid)) tg?.BackButton.show(); else tg?.BackButton.hide();
    tg?.BackButton.onClick(goBack);
    return () => tg?.BackButton.offClick(goBack);
  }, [view, me?.primary_uid, goBack]);
  useEffect(() => { if (!toast) return; const timer = window.setTimeout(() => setToast(''), 3000); return () => window.clearTimeout(timer); }, [toast]);

  const signIn = useCallback(async () => {
    setSessionToken(null);
    setAuth('loading'); setAuthError('');
    try {
      const s = await api<Status>('/status'); setStatus(s);
      const initData = window.Telegram?.WebApp?.initData;
      if (!initData) { setAuth('outside'); return; }
      const session = await api<{ session_token: string }>('/auth', { method: 'POST', body: JSON.stringify({ init_data: initData }) });
      setSessionToken(session.session_token);
      const user = await api<Me>('/me'); setMe(user); setAuth('ready');
    } catch (e) { setAuthError(errorText(e)); setAuth('error'); }
  }, []);
  useEffect(() => { void signIn(); }, [signIn]);

  useEffect(() => {
    if (auth !== 'ready' || !uid) { setProfile(null); return; }
    let active = true;
    setLoading(true); setError(''); setProfile(previous => previous?.player.uid === uid && previous.mode === mode ? previous : null);
    api<Profile>(`/players/${encodeURIComponent(uid)}?mode=${mode}&scope=last20`).then(p => { if (active) setProfile(p); }).catch(e => { if (active) setError(errorText(e)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [auth, uid, mode, epoch]);
  useEffect(() => {
    setArchive(null); setArchiveError('');
    if (auth !== 'ready' || !uid || scope !== 'archive') return;
    let active = true;
    api<Profile>(`/players/${encodeURIComponent(uid)}?mode=${mode}&scope=archive`).then(p => { if (active) setArchive(p); }).catch(e => { if (active) setArchiveError(errorText(e)); });
    return () => { active = false; };
  }, [scope, uid, mode, epoch, auth]);

  async function savePrimary(newUid: string) {
    setSaving(true); setSettingError('');
    try {
      const user = await api<Me>('/me', { method: 'PUT', body: JSON.stringify({ primary_uid: newUid }) }); setMe(user);
      navigate({ uid: newUid, settings: false, search: false, tab: 'profile', match: null }); setToast('Основной профиль сохранён');
    } catch (e) { setSettingError(errorText(e)); } finally { setSaving(false); }
  }
  async function changeMode(next: Mode) {
    if (!me || mode === next) return;
    setSaving(true);
    try { const user = await api<Me>('/me', { method: 'PUT', body: JSON.stringify({ mode: next }) }); setMe(user); navigate({ match: null }, true); }
    catch (e) { setToast(errorText(e)); } finally { setSaving(false); }
  }
  async function refresh() {
    if (!uid || refreshing) return;
    setRefreshing(true); setError('');
    try {
      await api(`/players/${encodeURIComponent(uid)}/refresh?mode=${mode}`, { method: 'POST' });
      setToast('Обновление поставлено в очередь');
      for (let attempt = 0; attempt < 15; attempt++) {
        await new Promise(resolve => window.setTimeout(resolve, 2000));
        const p = await api<Profile>(`/players/${encodeURIComponent(uid)}?mode=${mode}&scope=last20`);
        if ((viewRef.current.uid ?? meRef.current?.primary_uid) !== uid || meRef.current?.mode !== mode) break;
        setProfile(p);
        if (!['queued', 'running', 'pending'].includes(p.refresh_state ?? '')) break;
      }
      setEpoch(e => e + 1);
    } catch (e) { setError(errorText(e)); } finally { setRefreshing(false); }
  }
  const openPlayer = (id: string) => { setScope('last20'); navigate({ uid: id, tab: 'profile', match: null, search: false, settings: false }); };

  return <div className="app-shell"><header className="app-header"><button className="brand" aria-label="На главную" onClick={() => auth === 'ready' && navigate({ ...initialView, uid: me?.primary_uid ?? null })}><span className="brand-mark"><Crosshair size={23} /></span><span>SO2<span className="brand-accent"> CHECK</span><small>СТАТИСТИКА И ФОРМА</small></span></button><span className="version-tag">V1 / БЕТА</span>{auth === 'ready' && <div className="header-actions"><button className="icon-button" aria-label="Найти игрока" onClick={() => navigate({ search: !view.search, settings: false, match: null })}><Search size={20} /></button><button className="icon-button" aria-label="Настройки основного ID" onClick={() => { setSettingError(''); navigate({ settings: !view.settings, search: false, match: null }); }}><Settings2 size={20} /></button></div>}</header>
    {auth === 'loading' ? <main className="auth-loading"><Spinner text="Подключаемся к приложению…" /></main> : auth !== 'ready' ? <Intro bot={bot} authError={auth === 'error' ? authError : undefined} retry={signIn} /> : <>
      <main className="main-content">
        <div className="page-topline"><span className="eyebrow">{view.settings ? 'НАСТРОЙКИ' : view.search ? 'ПОИСК ИГРОКА' : tabs.find(t => t.id === view.tab)?.title.toUpperCase()}</span>{me?.primary_uid && <button className="text-button" onClick={() => openPlayer(me.primary_uid!)}><UserRound size={14} /> Мой аккаунт</button>}</div>
        {status && !status.source_available && <div className="source-banner" role="status"><span className="status-indicator" /><div><strong>Источник статистики не подключён</strong><p>{status.source_message || 'Доступ к игровым данным пока недоступен. Повторите позже.'}</p></div></div>}
        {view.settings ? <section className="panel settings-panel"><button className="back-link" onClick={goBack}><ArrowLeft size={16} /> Назад</button><h1>Основной профиль</h1><p>«Мой аккаунт» всегда открывает этот игровой ID. Поиск других игроков его не меняет.</p><UidForm label="Сменить основной игровой ID" button="Сохранить" value={me?.primary_uid ?? ''} loading={saving} onSubmit={savePrimary} />{settingError && <ErrorBox error={settingError} />}<div className="info-note"><Info size={17} /><span>Привязка сохраняет предпочтение для просмотра. Она не подтверждает владение игровым аккаунтом.</span></div><div className="settings-separator" /><FormulaInfo /></section> : view.search ? <section className="panel search-panel"><button className="back-link" onClick={goBack}><ArrowLeft size={16} /> Назад</button><h1>Открыть профиль</h1><p>Найдите игрока по публичному ID Standoff 2.</p><UidForm label="Игровой ID" button="Найти" loading={false} onSubmit={openPlayer} /><p className="micro">Ваш основной ID останется прежним.</p></section> : <>
          <div className="mode-switch" role="group" aria-label="Режим игры">{(['competitive', 'allies'] as Mode[]).map(m => <button key={m} aria-pressed={mode === m} className={mode === m ? 'active' : ''} disabled={saving} onClick={() => void changeMode(m)}><span>{modeLabel[m]}</span><small>{m === 'competitive' ? '5 × 5' : '2 × 2'}</small></button>)}</div>
          {view.match && uid ? <MatchDetail id={view.match} uid={uid} onBack={goBack} openPlayer={openPlayer} /> : view.tab === 'top' ? <Leaderboard mode={mode} onPlayer={openPlayer} /> : view.tab === 'compare' ? <Comparison primaryUid={me?.primary_uid ?? null} primary={profile?.player.uid === me?.primary_uid ? profile : null} mode={mode} openPlayer={openPlayer} /> : !uid ? <section className="panel onboarding"><div className="onboard-icon"><Target size={32} /></div><span className="eyebrow">ВАША ТОЧКА ОТСЧЁТА</span><h1>Вся форма —<br />в одном профиле.</h1><p>Закрепите игровой ID и следите за последними 20 матчами выбранного режима.</p><UidForm label="Ваш игровой ID Standoff 2" button="Продолжить" loading={saving} onSubmit={savePrimary} />{settingError && <ErrorBox error={settingError} />}<div className="info-note"><ShieldCheck size={17} /><span>Это сохранение профиля для просмотра, а не подтверждение владения аккаунтом.</span></div></section> : <>
            {error && <ErrorBox error={error} retry={() => setEpoch(e => e + 1)} />}
            {loading && !profile ? <Spinner /> : !profile ? !error && <Empty title="Профиль пока не загружен">Нажмите обновить или откройте другого игрока.</Empty> : <>
              <ProfileHeader profile={profile} mode={mode} refreshing={refreshing} onRefresh={refresh} onCopy={async () => { try { await navigator.clipboard.writeText(profile.player.uid); setToast('Игровой ID скопирован'); } catch { setToast(`Игровой ID: ${profile.player.uid}`); } }} />
              {profile.source_error && <ErrorBox error={profile.source_error} retry={refresh} />}
              {view.tab === 'profile' ? <><SummaryCard profile={profile} /><section className="section"><div className="section-heading"><h2><TrendingUp size={18} /> Динамика формы</h2><span className="micro">{mode === 'competitive' ? '5 × 5' : '2 × 2'}</span></div><div className="scope-switch"><button className={scope === 'last20' ? 'active' : ''} onClick={() => setScope('last20')}>Последние 20</button><button className={scope === 'archive' ? 'active' : ''} onClick={() => setScope('archive')}><History size={14} /> Сохранённая история</button></div>{archiveError && scope === 'archive' ? <ErrorBox error={archiveError} retry={() => setEpoch(e => e + 1)} /> : scope === 'archive' && !archive ? <Spinner text="Открываем сохранённую историю…" /> : <HistoryChart profile={scope === 'archive' ? archive! : profile} onMatch={id => navigate({ match: id })} />}</section><section className="section"><div className="section-heading"><h2>Последние матчи</h2><button className="text-button" onClick={() => navigate({ tab: 'matches' })}>Все <ArrowRight size={14} /></button></div><MatchList matches={profile.matches.slice(0, 3)} onMatch={id => navigate({ match: id })} /></section><FormulaInfo /></> : <section className="section"><div className="section-heading"><h1>Последние матчи</h1><span className="count-badge">{profile.matches.length} / 20</span></div><p className="section-description">Завершённые матчи · {modeLabel[mode].toLowerCase()}. От новых к старым.</p><MatchList matches={profile.matches} onMatch={id => navigate({ match: id })} /></section>}
            </>}
          </>}
        </>}
        <footer className="app-footer">SO2 CHECK <span>·</span> С начала наблюдения <span>·</span> Независимый рейтинг</footer>
      </main>
      <nav className="bottom-nav" aria-label="Основная навигация">{tabs.map(t => <button key={t.id} aria-current={view.tab === t.id ? 'page' : undefined} className={view.tab === t.id ? 'active' : ''} onClick={() => navigate({ tab: t.id, settings: false, search: false, match: null })}><t.icon size={21} strokeWidth={1.8} /><span>{t.title}</span></button>)}</nav>
    </>}{toast && <div className="toast" role="status"><Check size={17} />{toast}</div>}</div>;
}

function ProfileHeader({ profile, mode, refreshing, onRefresh, onCopy }: { profile: Profile; mode: Mode; refreshing: boolean; onRefresh: () => void; onCopy: () => void }) {
  const [imageFailed, setImageFailed] = useState(false);
  useEffect(() => setImageFailed(false), [profile.player.uid]);
  return <section className="profile-heading"><div className="avatar">{profile.player.avatar_url && !imageFailed ? <img src={profile.player.avatar_url} alt="Аватар игрока" referrerPolicy="no-referrer" onError={() => setImageFailed(true)} /> : <UserRound size={29} strokeWidth={1.6} />}</div><div className="profile-identity"><h1>{profile.player.nickname ?? 'Игрок'}</h1><button className="copy-id" onClick={onCopy}>ID {profile.player.uid} <Copy size={12} /></button>{profile.player.current_mmr?.[mode] != null && <span className="mmr-label">Текущий MMR <b>{stat(profile.player.current_mmr[mode])}</b></span>}</div><button className="icon-button refresh-button" aria-label="Обновить статистику" onClick={onRefresh} disabled={refreshing || ['queued', 'running'].includes(profile.refresh_state ?? '')}><RefreshCw size={19} className={refreshing || ['queued', 'running'].includes(profile.refresh_state ?? '') ? 'spin' : ''} /></button><p className="profile-updated">{refreshing || ['queued', 'running'].includes(profile.refresh_state ?? '') ? 'Обновляем · показываем сохранённые данные' : profile.updated_at ? `Обновлено ${date(profile.updated_at)}` : 'Пока не обновлялось'}</p></section>;
}
function SummaryCard({ profile }: { profile: Profile }) {
  const s = profile.summary;
  return <><section className="panel rating-panel"><div className="rating-main"><div><span className="eyebrow">СВЕЖАЯ ФОРМА</span><div className="rating-line"><Rating value={s.rating} display={s.rating_display} large /><span className="rating-unit">РЕЙТИНГ<br /><b>V1</b></span></div><p>Средний рейтинг последних 20 матчей</p></div><div className="form-symbol" aria-hidden="true"><Crosshair size={65} strokeWidth={.8} /><span>20</span></div></div><div className="sample-line"><span><i />Учтено <b>{s.count} из 20</b> матчей</span>{s.rated_count !== s.count && <span>Оценено {s.rated_count} из {s.count}</span>}<span className="preliminary-badge">Предварительный</span></div><div className="stats-grid"><Metric label="K/D" value={s.kd_infinite ? '∞' : number(s.kd)} hint="Убийства / смерти" /><Metric label="AVG" value={number(s.avg, 1)} hint="Убийств за матч" /><Metric label="Ассисты" value={number(s.assists, 1)} hint="В среднем за матч" /><Metric label="HS%" value={s.hs_percent == null ? 'Нет данных' : `${number(s.hs_percent, 1)}%`} hint="От всех убийств" /></div>{s.tier === 'match' || s.tier === 'per_match' || s.tier === 'fallback' ? <p className="tier-note">Упрощённая оценка за матч: личные раунды недоступны. Сравнение только в этой методике.</p> : s.tier === 'mixed' ? <p className="tier-note">В выборке разные методики. Общий рейтинг не рассчитан.</p> : null}{s.count === 0 && <p className="tier-note">Завершённые матчи выбранного режима пока не получены.</p>}</section>{s.rated_count > 0 && <section className="stability-row"><Metric label="Выше 1.00" value={s.above_100 == null ? 'Нет данных' : `${number(s.above_100, 0)}%`} hint="Стабильность формы" /><Metric label="Медиана" value={number(s.median)} hint="Середина результатов" /><Metric label="Разброс" value={number(s.stddev)} hint="Меньше — ровнее" /></section>}</>;
}
function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) { return <div className="metric"><span className="metric-label">{label}</span><strong className={value === 'Нет данных' ? 'no-value' : ''}>{value}</strong>{hint && <small>{hint}</small>}</div>; }
function MatchList({ matches, onMatch }: { matches: Match[]; onMatch: (id: string) => void }) {
  if (!matches.length) return <Empty icon={<Swords size={28} />} title="Пока нет матчей">Здесь появятся завершённые игры этого режима, когда источник передаст данные.</Empty>;
  return <div className="match-list">{matches.map(m => <button key={m.id} className="match-card" onClick={() => onMatch(m.id)}><span className={`match-result ${m.result ?? 'unknown'}`}>{m.result === 'win' ? 'П' : m.result === 'loss' ? 'ПР' : m.result === 'draw' ? 'Н' : '—'}</span><span className="match-main"><strong>{m.map ?? 'Карта неизвестна'}</strong><small>{date(m.ended_at)}{m.score && <span> · <b>{m.score.join(' : ')}</b></span>}</small></span><span className="match-numbers"><span className="kda">{stat(m.kills)} / {stat(m.deaths)} / {stat(m.assists)}</span><small>У / С / А</small></span><Rating value={m.rating} display={m.rating_display} /><ChevronRight size={15} className="match-chevron" /></button>)}</div>;
}
function FormulaInfo() {
  return <details className="formula-info"><summary><Info size={16} /><span>Как считается рейтинг V1</span><ChevronRight size={16} /></summary><div><p>Собственная предварительная оценка эффективности. 1.00 — ориентир среднего результата, пока не откалиброванный на большой выборке.</p><p>Учитываются убийства, смерти и ассисты. При подтверждённом числе личных раундов показатели нормируются на раунд. Упрощённая оценка за матч помечается отдельно.</p><p>Средний рейтинг — простое среднее последних 20 доступных матчей, каждый имеет одинаковый вес. Ассисты повышают оценку; HS% и место в таблице не дают скрытых бонусов. Текущий MMR не подменяет историческую силу соперников.</p><p>Нет данных — нет оценки. Только завершённые игры, единые правила исключения, отдельно 5×5 и 2×2.</p><div className="rating-legend"><span className="red">&lt; 0.90</span><span className="white">0.90–1.10</span><span className="green">1.11–1.80</span><span className="gold">&gt; 1.80</span></div></div></details>;
}

function MatchDetail({ id, uid, onBack, openPlayer }: { id: string; uid: string; onBack: () => void; openPlayer: (uid: string) => void }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState('');
  const [epoch, setEpoch] = useState(0);
  useEffect(() => { let active = true; setDetail(null); setError(''); api<Detail>(`/matches/${encodeURIComponent(id)}?uid=${encodeURIComponent(uid)}`).then(d => { if (active) setDetail(d); }).catch(e => { if (active) setError(errorText(e)); }); return () => { active = false; }; }, [id, uid, epoch]);
  const groups = detail ? [...new Set(detail.participants.map(p => p.team))] : [];
  return <section className="section"><button className="back-link" onClick={onBack}><ArrowLeft size={16} /> К матчам</button>{error ? <ErrorBox error={error} retry={() => setEpoch(e => e + 1)} /> : !detail ? <Spinner /> : <><div className="panel detail-hero"><span className="eyebrow">{modeLabel[detail.match.mode]} · {date(detail.match.ended_at)}</span><h1>{detail.match.map ?? 'Карта неизвестна'}</h1><div className="detail-score"><strong>{detail.match.score?.join(' : ') ?? 'Счёт неизвестен'}</strong><Rating value={detail.match.rating} display={detail.match.rating_display} /></div><div className="stats-grid three"><Metric label="Убийства" value={stat(detail.match.kills)} /><Metric label="Смерти" value={stat(detail.match.deaths)} /><Metric label="Ассисты" value={stat(detail.match.assists)} /></div>{detail.match.mmr_before != null && detail.match.mmr_after != null && <p className="micro">MMR матча: {detail.match.mmr_before} → {detail.match.mmr_after}</p>}</div><div className="section-heading"><h2>Участники матча</h2><span className="micro">{detail.participants.length || 'Нет данных'}</span></div>{!detail.participants.length ? <Empty title="Состав недоступен">Источник не передал данные участников этой игры.</Empty> : groups.map((team, index) => <section className="panel team-panel" key={String(team)}><h3>{team == null ? 'Команда не определена' : `Команда ${index + 1}`}</h3><div className="participant-header"><span>Игрок</span><span>У / С / А</span><span>Рейтинг</span></div>{detail.participants.filter(p => p.team === team).map((p, i) => <button className={`participant-row ${p.uid === uid ? 'is-player' : ''}`} key={p.uid ?? i} disabled={!p.uid} onClick={() => p.uid && openPlayer(p.uid)}><span><strong>{p.nickname ?? 'Игрок'}</strong><small>{p.uid ? `ID ${p.uid}` : 'ID недоступен'}{p.rank != null ? ` · Место ${p.rank}` : ''}</small></span><span>{stat(p.kills)} / {stat(p.deaths)} / {stat(p.assists)}</span><Rating value={p.rating} /></button>)}</section>)}<section className="panel explanation"><h2><Info size={17} /> Почему такая оценка</h2><p>Методика {detail.explanation.version}. {detail.explanation.tier === 'rounds' || detail.explanation.tier === 'per_round' ? 'Показатели нормированы на личные сыгранные раунды.' : 'Упрощённая оценка за матч: личные сыгранные раунды не подтверждены.'}</p><p>Убийства и ассисты повышают результат, смерти снижают. Каждый матч имеет одинаковый вес в среднем рейтинге.</p><div className="explanation-factors">{Object.entries(detail.explanation.factors ?? {}).filter(([key]) => ['kills', 'deaths', 'assists', 'base', 'opponent'].includes(key)).map(([key, value]) => <div key={key}><span>{{ kills: 'Вклад убийств', deaths: 'Вклад смертей', assists: 'Вклад ассистов', base: 'Базовая оценка', opponent: 'Сила соперников' }[key]}</span><b>{typeof value === 'number' ? number(value, 3) : 'Нет данных'}</b></div>)}</div>{detail.match.rounds != null && <p className="micro">Личных раундов: {detail.match.rounds}</p>}</section><FormulaInfo /></>}</section>;
}

function Comparison({ primaryUid, primary, mode, openPlayer }: { primaryUid: string | null; primary: Profile | null; mode: Mode; openPlayer: (uid: string) => void }) {
  const [left, setLeft] = useState<Profile | null>(primary);
  const [right, setRight] = useState<Profile | null>(null);
  const [otherUid, setOtherUid] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [epoch, setEpoch] = useState(0);
  useEffect(() => {
    let active = true; setLeft(primary?.mode === mode ? primary : null); setRight(null); setError('');
    if (!primaryUid) return;
    setLoading(true);
    Promise.all([primary?.mode === mode ? Promise.resolve(primary) : api<Profile>(`/players/${primaryUid}?mode=${mode}&scope=last20`), otherUid ? api<Profile>(`/players/${otherUid}?mode=${mode}&scope=last20`) : Promise.resolve(null)]).then(([a, b]) => { if (active) { setLeft(a); setRight(b); } }).catch(e => { if (active) setError(errorText(e)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [primaryUid, mode, otherUid, epoch, primary]);
  const rows: { title: string; get: (p: Profile) => string }[] = [
    { title: 'Учтено матчей', get: p => `${p.summary.count} / 20` }, { title: 'K/D', get: p => p.summary.kd_infinite ? '∞' : number(p.summary.kd) },
    { title: 'AVG · убийств за матч', get: p => number(p.summary.avg, 1) }, { title: 'Ассистов за матч', get: p => number(p.summary.assists, 1) },
    { title: 'HS%', get: p => p.summary.hs_percent == null ? 'Нет данных' : `${number(p.summary.hs_percent, 1)}%` },
    { title: 'Матчей выше 1.00', get: p => p.summary.above_100 == null ? 'Нет данных' : `${number(p.summary.above_100, 0)}%` },
    { title: 'Матчей выше 1.10', get: p => p.summary.above_110 == null ? 'Нет данных' : `${number(p.summary.above_110, 0)}%` },
    { title: 'Медианный рейтинг', get: p => number(p.summary.median) }, { title: 'Разброс', get: p => number(p.summary.stddev) },
  ];
  return <section className="section"><h1>Лицом к лицу</h1><p className="section-description">Последние 20 доступных матчей каждого игрока. Один режим — общие правила.</p>{!primaryUid ? <Empty icon={<UsersRound size={29} />} title="Сначала выберите свой профиль">Закрепите основной игровой ID на вкладке «Профиль», чтобы сравнивать свою форму с другими.</Empty> : <><section className="panel compare-search"><UidForm label="ID второго игрока" button="Сравнить" loading={loading} value={otherUid} onSubmit={setOtherUid} /></section>{error && <ErrorBox error={error} retry={() => setEpoch(e => e + 1)} />}{loading && <Spinner text="Сопоставляем выборки…" />}{left && right ? <><div className="comparison-players"><button className="panel" onClick={() => openPlayer(left.player.uid)}><span className="eyebrow">ОСНОВНОЙ</span><strong>{left.player.nickname ?? 'Игрок'}</strong><small>ID {left.player.uid}</small><Rating value={left.summary.rating} display={left.summary.rating_display} /></button><span className="versus">×</span><button className="panel" onClick={() => openPlayer(right.player.uid)}><span className="eyebrow">СОПЕРНИК</span><strong>{right.player.nickname ?? 'Игрок'}</strong><small>ID {right.player.uid}</small><Rating value={right.summary.rating} display={right.summary.rating_display} /></button></div>{left.summary.tier !== right.summary.tier && <div className="info-note"><Info size={18} /><span>Методики оценки различаются. Рейтинги этих выборок напрямую несопоставимы.</span></div>}<div className="panel comparison-table">{rows.map(row => <div className="comparison-row" key={row.title}><span>{row.get(left)}</span><strong>{row.title}</strong><span>{row.get(right)}</span></div>)}</div><p className="micro">Разный размер выборки указан открыто. Сила соперников не оценивается без подтверждённого исторического MMR.</p></> : !loading && !error && <Empty icon={<ArrowDownUp size={29} />} title="С кем сравним форму?">Введите ID второго игрока. Поиск не изменит ваш основной профиль.</Empty>}</>}</section>;
}

function Leaderboard({ mode, onPlayer }: { mode: Mode; onPlayer: (uid: string) => void }) {
  const [top, setTop] = useState<Top | null>(null);
  const [error, setError] = useState('');
  const [epoch, setEpoch] = useState(0);
  useEffect(() => { let active = true; setTop(null); setError(''); api<Top>(`/top?mode=${mode}`).then(data => { if (active) setTop(data); }).catch(e => { if (active) setError(errorText(e)); }); return () => { active = false; }; }, [mode, epoch]);
  const list = (players: TopPlayer[], provisional = false) => <div className="leaderboard-list">{players.map((p, i) => <button className="leaderboard-row panel" key={p.uid} onClick={() => onPlayer(p.uid)}><span className={`rank-position ${i < 3 && !provisional ? 'podium' : ''}`}>{provisional ? '·' : String(i + 1).padStart(2, '0')}</span><span className="leaderboard-player"><strong>{p.nickname ?? 'Игрок'}</strong><small>ID {p.uid} · {p.count} из 20 матчей</small><small className="method-label">{p.tier === 'rounds' ? 'По личным раундам' : p.tier === 'match' ? 'Упрощённая за матч' : 'Разные методики'}</small></span><Rating value={p.rating} display={p.rating_display} /><ChevronRight size={15} /></button>)}</div>;
  return <section className="section"><div className="section-heading"><h1>Топ наблюдаемых</h1><Medal size={23} className="orange-text" /></div><p className="section-description">Среди игроков, которых отслеживает приложение. Свежая форма · {modeLabel[mode].toLowerCase()}.</p><div className="info-note"><ShieldCheck size={18} /><span>Основной топ — полная выборка из 20 матчей. Рейтинг предварительный; методики расчёта разделены.</span></div>{error ? <ErrorBox error={error} retry={() => setEpoch(e => e + 1)} /> : !top ? <Spinner /> : <>{top.players.length ? list(top.players) : <Empty icon={<Medal size={30} />} title="Топ пока формируется">Здесь появятся отслеживаемые игроки с полной выборкой из 20 матчей этого режима.</Empty>}{top.provisional.length > 0 && <><div className="section-heading"><h2>Неполная выборка</h2><span className="micro">ПРЕДВАРИТЕЛЬНО</span></div>{list(top.provisional, true)}</>}</>}<p className="micro top-footnote">Это не мировой рейтинг Standoff 2. Одинаковый средний рейтинг не меняется из-за показателей стабильности.</p></section>;
}



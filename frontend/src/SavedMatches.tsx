import { useCallback, useEffect, useState } from 'react';
import { ArrowLeft, Camera, ChevronRight, FileImage, RefreshCw, Trophy } from 'lucide-react';
import { api, errorText } from './api';
import { date, modeLabel, ratingColor, ratingText } from './format';
import type { Mode, PersonalMatchPlayer, PersonalMatchSnapshot } from './types';

export function usePersonalMatches(enabled: boolean, mode: Mode, epoch: number, primaryUid: string | null) {
  const [best, setBest] = useState<PersonalMatchSnapshot | null>(null);
  const [matches, setMatches] = useState<PersonalMatchSnapshot[]>([]);
  const [loading, setLoading] = useState(false);
  const [bestError, setBestError] = useState('');
  const [matchesError, setMatchesError] = useState('');
  const [uploadMeta, setUploadMeta] = useState({ total: 0, truncated: false });
  const [revision, setRevision] = useState(0);
  const [dataMode, setDataMode] = useState<Mode | null>(null);
  const [dataPrimary, setDataPrimary] = useState<string | null | undefined>(undefined);
  const refresh = useCallback(() => setRevision(value => value + 1), []);

  useEffect(() => {
    setBest(null); setMatches([]); setBestError(''); setMatchesError(''); setUploadMeta({ total: 0, truncated: false });
    setDataMode(enabled ? mode : null);
    setDataPrimary(enabled ? primaryUid : undefined);
    if (!enabled) { setLoading(false); return; }
    let active = true;
    setLoading(true);
    Promise.allSettled([
      api<{ best: PersonalMatchSnapshot | null }>(`/me/best?mode=${mode}`),
      api<{ matches: PersonalMatchSnapshot[]; total?: number; truncated?: boolean }>(`/me/screenshot-matches?mode=${mode}`),
    ]).then(([record, uploads]) => {
      if (!active) return;
      if (record.status === 'fulfilled') setBest(record.value.best?.mode === mode ? record.value.best : null);
      else setBestError(errorText(record.reason));
      if (uploads.status === 'fulfilled') {
        const rows = uploads.value.matches.filter(match => match.mode === mode);
        setMatches(rows);
        setUploadMeta({ total: uploads.value.total ?? rows.length, truncated: uploads.value.truncated ?? false });
      }
      else setMatchesError(errorText(uploads.reason));
      setLoading(false);
    });
    return () => { active = false; };
  }, [enabled, mode, epoch, revision, primaryUid]);

  useEffect(() => {
    if (!enabled) return;
    let pending: number | undefined;
    const onVisible = () => {
      if (document.visibilityState !== 'visible') return;
      window.clearTimeout(pending);
      pending = window.setTimeout(refresh, 200);
    };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onVisible);
    window.Telegram?.WebApp?.onEvent('activated', onVisible);
    return () => {
      window.clearTimeout(pending);
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onVisible);
      window.Telegram?.WebApp?.offEvent('activated', onVisible);
    };
  }, [enabled, refresh]);
  const current = enabled && dataMode === mode && dataPrimary === primaryUid;
  return { best: current ? best : null, matches: current ? matches : [], loading: enabled && (!current || loading),
    bestError: current ? bestError : '', matchesError: current ? matchesError : '', refresh,
    uploadMeta: current ? uploadMeta : { total: 0, truncated: false } };
}

function SnapshotRating({ value, display }: { value: number | null; display?: string | null }) {
  const missing = value == null && display == null;
  return <span className={`rating saved-rating ${ratingColor(display ? Number(display) : value) ?? ''}`} title={missing ? 'Нет данных' : undefined} aria-label={missing ? 'Нет данных' : undefined}>{display ?? ratingText(value)}</span>;
}

function displayNickname(snapshot: PersonalMatchSnapshot) { return snapshot.requested_nickname ?? snapshot.nickname; }
function OcrNicknameNote({ snapshot }: { snapshot: PersonalMatchSnapshot }) {
  return snapshot.source === 'screenshot' && displayNickname(snapshot) !== snapshot.nickname
    ? <small className="saved-ocr-nickname">На скриншоте распознано: {snapshot.nickname}</small> : null;
}

function SourceBadge({ snapshot }: { snapshot: PersonalMatchSnapshot }) {
  return <span className={`saved-source ${snapshot.source}`}>
    {snapshot.source === 'screenshot' && <FileImage size={12} />}
    {snapshot.source === 'screenshot' ? 'Со скриншота' : 'Из игровой истории'}
  </span>;
}

function BotUploadLink({ bot }: { bot: string }) {
  const url = `https://t.me/${bot.replace('@', '')}?start=match`;
  return <a className="button orange saved-upload-link" href={url} onClick={event => {
    const open = window.Telegram?.WebApp?.openTelegramLink;
    if (open) { event.preventDefault(); try { open.call(window.Telegram!.WebApp, url); } catch { window.location.href = url; } }
  }}><Camera size={17} />Разобрать скриншот в боте</a>;
}

function SavedError({ text, onRetry }: { text: string; onRetry: () => void }) {
  return <div className="error-box saved-error" role="alert"><div><p>{text}</p><button className="text-button" onClick={onRetry}>Повторить загрузку матчей</button></div></div>;
}

function SavedLoading() {
  return <p className="saved-loading micro" role="status"><RefreshCw size={15} className="spin" />Загружаем сохранённые матчи…</p>;
}

function SnapshotMeta({ snapshot, omitMap = false }: { snapshot: PersonalMatchSnapshot; omitMap?: boolean }) {
  return <p className="saved-meta">
    {!omitMap && <span>{snapshot.map ?? 'Карта не указана'}</span>}
    <span>{snapshot.score ? `${snapshot.score[0]} : ${snapshot.score[1]}` : 'Счёт: нет данных'}</span>
    <span>{snapshot.ended_at ? date(snapshot.ended_at) : snapshot.date_label ? `На скриншоте: ${snapshot.date_label}` : 'Дата игры неизвестна'}</span>
  </p>;
}

type SectionProps = {
  mode: Mode; bot: string; loading: boolean; error: string; refresh: () => void;
  onOpen: (snapshot: PersonalMatchSnapshot) => void;
};

export function BestMatchCard({ best, mode, bot, loading, error, refresh, onOpen }: SectionProps & { best: PersonalMatchSnapshot | null }) {
  return <section className="section personal-best" aria-label="Лучший матч">
    <div className="section-heading"><h2><Trophy size={18} />Лучший матч</h2><button className="icon-button" aria-label="Обновить лучший матч" onClick={refresh} disabled={loading}><RefreshCw size={17} className={loading ? 'spin' : ''} /></button></div>
    {error ? <SavedError text={error} onRetry={refresh} /> : loading && !best ? <SavedLoading /> : best ? <>
      <button className="panel best-match-card" onClick={() => onOpen(best)} aria-label={`Открыть лучший матч ${best.map ?? ''}`}>
        <div className="saved-card-top"><span className="eyebrow">ЛИЧНЫЙ РЕКОРД · {modeLabel[mode]}</span><SourceBadge snapshot={best} /></div>
        <div className="saved-card-main"><div><strong className="saved-nickname">{displayNickname(best)}</strong><OcrNicknameNote snapshot={best} /><SnapshotMeta snapshot={best} /><span className="saved-kda">{best.kills} / {best.deaths} / {best.assists}<small>Убийства / смерти / ассисты</small></span></div><div className="best-rating"><SnapshotRating value={best.rating} display={best.rating_display} /><ChevronRight size={18} /></div></div>
        <p className="saved-method">{best.method_note}<span>Загружен {date(best.loaded_at)}</span></p>
      </button>
      <p className="micro saved-footnote">Лучший отдельный матч среди сохранённых. Средний рейтинг последних 20 считается отдельно.</p>
    </> : <div className="panel saved-empty"><Trophy size={24} /><h3>Лучший матч ещё не сохранён</h3><p>Отправьте скриншот боту, укажите ник и подтвердите показатели. Здесь появится ваш рекорд в режиме «{modeLabel[mode]}».</p><BotUploadLink bot={bot} /></div>}
  </section>;
}

export function UploadedMatches({ matches, mode, bot, loading, error, refresh, onOpen, total, truncated }: SectionProps & { matches: PersonalMatchSnapshot[]; total: number; truncated: boolean }) {
  return <section className="section uploaded-matches" aria-label="Загруженные матчи">
    <div className="section-heading"><h2><FileImage size={18} />Загруженные матчи</h2><button className="icon-button" aria-label="Обновить загруженные матчи" onClick={refresh} disabled={loading}><RefreshCw size={17} className={loading ? 'spin' : ''} /></button></div>
    <p className="micro saved-footnote">Со скриншотов · {modeLabel[mode]}. Эта история не входит автоматически в последние 20 матчей.</p>
    {truncated && <p className="micro saved-upload-limit">Показаны последние {matches.length} из {total} загруженных матчей.</p>}
    {error ? <SavedError text={error} onRetry={refresh} /> : loading && !matches.length ? <SavedLoading /> : matches.length ? <div className="uploaded-match-list">{matches.map(match => <button className="panel uploaded-match-card" key={match.id} onClick={() => onOpen(match)}>
      <div><SourceBadge snapshot={match} /><strong>{match.map ?? 'Карта не указана'}</strong><span className="saved-list-kda">{match.kills} / {match.deaths} / {match.assists} · {match.score ? `${match.score[0]} : ${match.score[1]}` : 'Счёт: нет данных'}</span><small>Загружен {date(match.loaded_at)}</small></div><SnapshotRating value={match.rating} display={match.rating_display} /><ChevronRight size={16} />
    </button>)}</div> : <div className="panel saved-empty"><h3>Загруженных матчей пока нет</h3><p>Отправьте скриншот боту, укажите ник и подтвердите строку.</p><BotUploadLink bot={bot} /></div>}
  </section>;
}

function teamTitle(team: string) {
  if (team.toLowerCase() === 'ct') return 'Оборона (CT)';
  if (team.toLowerCase() === 't') return 'Атака (T)';
  return team === 'unknown' ? 'Участники' : `Команда ${team}`;
}

function playerValue(value: number | null) { return value == null ? <span className="saved-missing" title="Нет данных" aria-label="Нет данных">—</span> : value; }

export function PersonalMatchDetail({ snapshot, onBack }: { snapshot: PersonalMatchSnapshot; onBack: () => void }) {
  const groups = new Map<string, PersonalMatchPlayer[]>();
  for (const player of snapshot.players) {
    const key = player.team ?? 'unknown';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(player);
  }
  return <section className="personal-match-detail" aria-label="Подробности сохранённого матча">
    <button className="back-link" onClick={onBack}><ArrowLeft size={16} />Назад</button>
    <div className="panel saved-detail-hero"><div className="saved-card-top"><span className="eyebrow">{modeLabel[snapshot.mode]}</span><SourceBadge snapshot={snapshot} /></div><h1>{snapshot.map ?? 'Сохранённый матч'}</h1><SnapshotMeta snapshot={snapshot} omitMap /><div className="saved-detail-score"><div className="saved-detail-identity"><strong>{displayNickname(snapshot)}</strong><OcrNicknameNote snapshot={snapshot} /></div><SnapshotRating value={snapshot.rating} display={snapshot.rating_display} /></div><p className="saved-detail-kda">{snapshot.kills} / {snapshot.deaths} / {snapshot.assists}<span>Убийства / смерти / ассисты</span></p><p className="saved-method">{snapshot.method_note}<span>Предварительная оценка одного матча</span></p></div>
    <div className="info-note saved-provenance"><FileImage size={18} /><span>{snapshot.source === 'screenshot' ? 'Подтверждена ваша строка; остальные показатели распознаны со скриншота и могут содержать ошибки.' : 'Матч сохранён из игровой истории. Расчёт зависит от режима. В 5×5 при наличии подтверждённых личных раундов рекорд выбирается среди таких матчей; упрощённые оценки остаются в архиве.'}</span></div>
    <div className="section-heading"><h2>Участники матча</h2><span className="micro">У · С · П</span></div><p className="micro saved-footnote">У — убийства, С — смерти, П — ассисты.</p>
    {groups.size ? [...groups].map(([team, players]) => <section className="panel saved-team" key={team}><h3>{teamTitle(team)}</h3><div className="saved-scoreboard-head"><span>Игрок</span><span>У</span><span>С</span><span>П</span><span>Рейтинг</span></div>{players.map((player, index) => <div className={`saved-player-row ${player.selected ? 'is-selected' : ''}`} key={`${team}-${index}`}><div className="saved-player-name"><strong>{player.rank != null && <span className="saved-player-rank">#{player.rank} </span>}{player.nickname}</strong><small>Очки: {player.game_score == null ? 'Нет данных' : player.game_score}</small>{player.selected && <small className="saved-selected-label">Ваша строка{player.user_confirmed ? ' · подтверждена' : ''}</small>}{!player.selected && snapshot.source === 'screenshot' && <small>Распознано</small>}{player.uid && <small>ID {player.uid}</small>}</div><span>{playerValue(player.kills)}</span><span>{playerValue(player.deaths)}</span><span>{playerValue(player.assists)}</span><SnapshotRating value={player.rating} display={player.rating_display} /></div>)}</section>) : <div className="panel saved-empty"><p>Подробная таблица участников не сохранена.</p></div>}
    <p className="micro saved-data-legend">— нет данных</p>
    <p className="micro saved-detail-note">{snapshot.mode === 'allies' ? 'Напарники v2: оценка по соотношению убийств и смертей с вкладом ассистов. Раунды не используются.' : snapshot.rating_tier === 'rounds' ? `Учтено личных раундов: ${snapshot.personal_rounds}. Для скриншота полное участие подтверждено вами. У других участников личные раунды не подтверждены: их оценки упрощённые.` : 'Упрощённая оценка K/D/A: личные раунды неизвестны. Подтвердите полное участие при повторной загрузке скриншота для расчёта на раунд.'} Урон, HS%, звание и сила соперников не дают скрытых бонусов.</p>
    <p className="micro saved-detail-note">Загружен {date(snapshot.loaded_at)}{snapshot.match_id ? ` · Матч №${snapshot.match_id}` : ''}</p>
  </section>;
}

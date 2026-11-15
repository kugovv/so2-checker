import { useMemo, useState } from 'react';
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Info, TrendingUp } from 'lucide-react';
import type { Match, Profile } from './types';
import { date, number, ratingText, stat } from './format';
import { Empty } from './App';

export function HistoryChart({ profile, onMatch }: { profile: Profile; onMatch: (id: string) => void }) {
  const [metric, setMetric] = useState<'rating' | 'mmr'>('rating');
  const [selected, setSelected] = useState<Match | null>(null);
  const points = useMemo(() => {
    const matches = [...profile.matches].reverse();
    return matches.map((m, index) => {
      const recent = matches.slice(Math.max(0, index - 19), index + 1);
      const rated = recent.filter(x => x.rating != null);
      const comparable = new Set(rated.map(x => `${x.tier}:${x.formula_version ?? profile.summary.formula_version}`)).size <= 1;
      return { ...m, index: index + 1, shortDate: date(m.ended_at, true), moving: comparable && rated.length ? rated.reduce((sum, x) => sum + x.rating!, 0) / rated.length : null };
    });
  }, [profile]);
  const hasMmr = points.some(p => p.mmr_after != null);
  const hasGaps = profile.gaps.length > 0;
  const tiers = new Set(profile.matches.filter(m => m.rating != null).map(m => m.tier));
  const versions = new Set(profile.matches.filter(m => m.rating != null).map(m => m.formula_version ?? profile.summary.formula_version));
  const disconnected = hasGaps || tiers.size > 1 || versions.size > 1;
  if (!points.length) return <Empty icon={<TrendingUp size={28} />} title="График начнётся с первого матча">Покажем только подтверждённые игры. В сохранённой истории останутся доступные матчи с начала наблюдения.</Empty>;
  return <div className="panel chart-panel"><div className="chart-controls"><button className={metric === 'rating' ? 'active' : ''} onClick={() => setMetric('rating')}>Рейтинг</button>{hasMmr && <button className={metric === 'mmr' ? 'active' : ''} onClick={() => setMetric('mmr')}>MMR матчей</button>}<span>{points.length} матчей</span></div>{hasGaps && <div className="chart-warning"><Info size={15} /><span>В сохранённой истории есть пропуски. Точки не соединены; среднее рассчитано по доступным матчам.</span></div>}{(tiers.size > 1 || versions.size > 1) && <div className="chart-warning"><Info size={15} /><span>В выборке разные методики. Средняя линия на их границе прерывается.</span></div>}<div className="chart-container"><ResponsiveContainer width="100%" height="100%"><LineChart data={points} margin={{ top: 15, right: 14, bottom: 4, left: -24 }} accessibilityLayer onClick={(state: unknown) => { const s = state as { activeTooltipIndex?: string | number | null }; if (s.activeTooltipIndex != null) setSelected(points[Number(s.activeTooltipIndex)] ?? null); }}><CartesianGrid vertical={false} stroke="#414A52" strokeDasharray="3 5" opacity={.55} /><XAxis dataKey="shortDate" tick={{ fill: '#A4ADB5', fontSize: 10 }} tickLine={false} axisLine={false} minTickGap={35} /><YAxis tick={{ fill: '#A4ADB5', fontSize: 10 }} tickLine={false} axisLine={false} domain={metric === 'rating' ? [0, 'auto'] : ['auto', 'auto']} width={50} />{metric === 'rating' && <ReferenceLine y={1} stroke="#A4ADB5" strokeDasharray="4 5" />}<Tooltip trigger="click" content={({ active, payload }) => { const p = payload?.[0]?.payload as Match | undefined; if (!active || !p) return null; return <div className="chart-tooltip"><strong>{p.map ?? 'Карта неизвестна'}</strong><small>{date(p.ended_at)} · {p.score?.join(' : ') ?? 'Счёт неизвестен'}</small><span>У / С / А: {stat(p.kills)} / {stat(p.deaths)} / {stat(p.assists)}</span><b>{metric === 'rating' ? `Рейтинг ${ratingText(p.rating)}` : `MMR ${stat(p.mmr_after)}`}</b></div>; }} />{metric === 'rating' ? <><Line name="Рейтинг матча" type="linear" dataKey="rating" stroke={disconnected ? 'transparent' : '#FF7A24'} strokeWidth={2} connectNulls={false} dot={{ r: 3.5, fill: '#FF7A24', stroke: '#171A1D', strokeWidth: 2 }} activeDot={{ r: 6 }} isAnimationActive={false} /><Line name="Среднее 20" type="linear" dataKey="moving" stroke={disconnected ? 'transparent' : '#A4ADB5'} strokeWidth={1.5} strokeDasharray="5 4" connectNulls={false} dot={disconnected ? { r: 2, fill: '#A4ADB5' } : false} activeDot={false} isAnimationActive={false} /></> : <Line type="linear" dataKey="mmr_after" stroke={disconnected ? 'transparent' : '#FF7A24'} strokeWidth={2} connectNulls={false} dot={{ r: 3.5, fill: '#FF7A24' }} isAnimationActive={false} />}</LineChart></ResponsiveContainer></div>{metric === 'rating' && <div className="chart-legend"><span><i />Рейтинг матча</span><span><i />Среднее последних 20</span></div>}<p className="micro chart-hint">Нажмите на точку, чтобы увидеть показатели матча.</p>{selected && <button className="selected-match" onClick={() => onMatch(selected.id)}><span>{selected.map ?? 'Матч'} · {date(selected.ended_at, true)}</span><b>{metric === 'rating' ? ratingText(selected.rating) : number(selected.mmr_after, 0)} →</b></button>}{profile.scope === 'archive' && <p className="micro archive-note">Сохранённая история с начала наблюдения. Полнота истории аккаунта {profile.history_complete ? 'подтверждена источником' : 'не подтверждена'}.</p>}</div>;
}


import type { CollectorStatus } from './types';

export function NightStatus({ info }: { info?: CollectorStatus }) {
  return <section className="panel night-status" aria-label="Ночное обновление">
    <h2>Обновление раз в сутки</h2>
    <p>Ночной сбор начинается в 00:00 МСК. Статистика появится после обработки вашего профиля.</p>
    <dl><div><dt>Последнее успешное обновление</dt><dd>{info?.last_success_msk ?? 'Ещё не выполнялось'}</dd></div>
      <div><dt>Ближайший ночной сбор</dt><dd>{info?.next_run_msk ?? 'Получаем дату с сервера…'}</dd></div></dl>
    <strong className="night-state">{info?.label ?? 'Ожидает первого обновления'}</strong>
    {info && !info.schedule_enabled && <p className="micro">Ночной сбор пока не включён: выполняется проверка телефона и игровых экранов.</p>}
    {info && ['partial', 'failed'].includes(info.status) && <p className="night-incomplete">Ночное обновление не завершено. {info.last_success_msk ? `Сейчас отображаются данные за ${info.last_success_msk}.` : 'Первой успешной проверки ещё не было.'}</p>}
  </section>;
}

export function PendingProfile({ uid, own, onRefresh }: { uid: string; own: boolean; onRefresh?: () => void }) {
  return <section className="panel pending-profile" aria-label="Профиль ожидает проверки">
    <span className="eyebrow">ID {uid}</span>
    <h2>{own ? 'ID сохранён. Ожидает первого обновления' : 'Профиль ожидает первого обновления'}</h2>
    <p>Существование профиля, ник и матчи будут подтверждены реальным сбором. До этого рейтинг не рассчитан.</p>
    <p className="micro">Сохранение ID выбирает профиль для просмотра и не подтверждает владение игровым аккаунтом.</p>
    {onRefresh && <button className="button orange" onClick={onRefresh}>Обновить данные и статус</button>}
  </section>;
}

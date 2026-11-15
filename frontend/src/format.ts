import type { RatingColor } from './types';
export const modeLabel = { competitive: 'Соревновательный', allies: 'Напарники' } as const;
export function number(value: number | null | undefined, digits = 2): string {
  return value == null || !Number.isFinite(value) ? 'Нет данных' : value.toLocaleString('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
export const stat = (value: number | null | undefined) => value == null ? '—' : value.toLocaleString('ru-RU');
export function date(value: string | null | undefined, short = false) {
  if (!value || Number.isNaN(new Date(value).getTime())) return 'Дата неизвестна';
  return new Date(value).toLocaleString('ru-RU', short ? { day: 'numeric', month: 'short' } : { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}
export function ratingColor(value: number | null): RatingColor | undefined {
  if (value == null) return undefined;
  const rounded = Math.round((value + Number.EPSILON) * 100) / 100;
  return rounded < .9 ? 'red' : rounded <= 1.1 ? 'white' : rounded <= 1.8 ? 'green' : 'gold';
}
export const ratingText = (value: number | null | undefined) => value == null ? '—' : (Math.round((value + Number.EPSILON) * 100) / 100).toFixed(2);

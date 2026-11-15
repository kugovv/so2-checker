export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}
let sessionToken: string | null = null;
export function setSessionToken(token: string | null) { sessionToken = token; }
const configuredUrl = import.meta.env.VITE_API_URL?.trim().replace(/\/$/, '') ?? '';
export const apiConfigured = !!configuredUrl || import.meta.env.DEV;
function apiOrigin(): string {
  if (configuredUrl) {
    const url = new URL(configuredUrl);
    if (url.protocol !== 'https:' && !import.meta.env.DEV) throw new Error('Адрес сервера приложения должен использовать HTTPS.');
    return url.origin;
  }
  if (import.meta.env.DEV) return '';
  throw new Error('Сервер приложения пока не подключён. Веб-интерфейс опубликован; вход и игровая статистика станут доступны после подключения сервера.');
}
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const origin = apiOrigin();
  const ctrl = new AbortController();
  const timeout = window.setTimeout(() => ctrl.abort(), 20000);
  try {
    const response = await fetch(`${origin}/api${path}`, {
      credentials: 'omit', ...options,
      headers: { 'Content-Type': 'application/json', ...(sessionToken ? { Authorization: `Bearer ${sessionToken}` } : {}), ...options.headers }, signal: options.signal ?? ctrl.signal,
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = body.detail;
      throw new ApiError(response.status,
        typeof detail === 'string' ? detail : typeof detail?.message === 'string' ? detail.message :
        response.status === 401 ? 'Сессия завершилась. Откройте приложение заново через бота.' :
        response.status === 429 ? 'Слишком много запросов. Повторите чуть позже.' : 'Не удалось выполнить запрос. Попробуйте ещё раз.');
    }
    return body as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new Error(error instanceof DOMException && error.name === 'AbortError' ? 'Сервер не ответил вовремя. Повторите запрос.' : 'Нет связи с сервером. Проверьте подключение.');
  } finally { window.clearTimeout(timeout); }
}
export const errorText = (error: unknown) => error instanceof Error ? error.message : 'Не удалось загрузить данные.';

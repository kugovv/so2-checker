export type Mode = 'competitive' | 'allies';
export type Scope = 'last20' | 'archive';
export type Tab = 'profile' | 'matches' | 'compare' | 'top';
export type RatingColor = 'red' | 'white' | 'green' | 'gold';
export type Me = { primary_uid: string | null; mode: Mode };
export type Status = { source_available: boolean; source_message: string; bot_username: string; formula_version: string };
export type Summary = {
  count: number; rated_count: number; rating: number | null; rating_display: string | null; rating_color: RatingColor | null;
  kd: number | null; kd_infinite: boolean; avg: number | null; assists: number | null; hs_percent: number | null;
  above_100: number | null; above_110: number | null; median: number | null; stddev: number | null; tier: string | null; formula_version: string;
};
export type Match = {
  id: string; map: string | null; ended_at: string | null; mode: Mode; score: [number, number] | null;
  result: 'win' | 'loss' | 'draw' | null; kills: number | null; deaths: number | null; assists: number | null;
  headshots: number | null; rounds: number | null; rating: number | null; rating_display: string | null;
  rating_color: RatingColor | null; tier: string | null; formula_version?: string; mmr_before: number | null; mmr_after: number | null;
};
export type Player = { uid: string; nickname: string | null; avatar_url: string | null; current_mmr: Partial<Record<Mode, number | null>> };
export type Profile = {
  player: Player; mode: Mode; scope: Scope; summary: Summary; matches: Match[]; updated_at: string | null;
  refresh_state: string | null; source_error: string | null; gaps: { detected_at: string; reason: string }[]; history_complete: boolean;
};
export type TopPlayer = Summary & { uid: string; nickname: string | null };
export type Top = { players: TopPlayer[]; provisional: TopPlayer[] };
export type Participant = { uid: string | null; nickname: string | null; team: string | number | null; kills: number | null; deaths: number | null; assists: number | null; rating: number | null; rank: number | null; mmr_before: number | null; mmr_after: number | null };
export type Detail = { match: Match; participants: Participant[]; explanation: { version: string; tier: string | null; inputs: Record<string, unknown>; parameters: Record<string, unknown>; factors: Record<string, unknown> | string[] }; gaps: unknown[] };

type TelegramApp = {
  initData: string; viewportHeight: number; viewportStableHeight: number;
  safeAreaInset?: { top: number; bottom: number }; contentSafeAreaInset?: { top: number; bottom: number };
  ready(): void; expand(): void; setHeaderColor(color: string): void; setBackgroundColor(color: string): void;
  onEvent(event: string, callback: () => void): void; offEvent(event: string, callback: () => void): void;
  BackButton: { show(): void; hide(): void; onClick(callback: () => void): void; offClick(callback: () => void): void };
  HapticFeedback?: { selectionChanged(): void }; openTelegramLink?(url: string): void;
};
declare global { interface Window { Telegram?: { WebApp?: TelegramApp } } }
interface ImportMetaEnv { readonly DEV: boolean; readonly VITE_API_URL?: string }
interface ImportMeta { readonly env: ImportMetaEnv }

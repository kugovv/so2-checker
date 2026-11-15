export type Mode = 'competitive' | 'allies';
export type Scope = 'last20' | 'archive';
export type Tab = 'profile' | 'matches' | 'compare';
export type RatingColor = 'red' | 'white' | 'green' | 'gold';
export type Me = { primary_uid: string | null; mode: Mode; id_status?: 'pending' | 'known' };
export type Status = { source_available: boolean; source_message: string; bot_username: string; formula_version: string; collector_enabled?: boolean; manual_import_enabled?: boolean };
export type CollectorStatus = { status: string; label: string; last_success_at: string | null; next_run_at: string; timezone: string; schedule_enabled: boolean; navigation_calibrated: boolean; last_success_msk: string | null; next_run_msk: string };
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
  data_status?: 'pending' | 'available' | 'empty_verified'; collector_status?: CollectorStatus;
  player: Player; mode: Mode; scope: Scope; summary: Summary; matches: Match[]; updated_at: string | null;
  refresh_state: string | null; source_error: string | null; gaps: { detected_at: string; reason: string }[]; history_complete: boolean;
};
export type Participant = { uid: string | null; nickname: string | null; team: string | number | null; kills: number | null; deaths: number | null; assists: number | null; rating: number | null; rank: number | null; mmr_before: number | null; mmr_after: number | null };
export type Detail = { match: Match; participants: Participant[]; explanation: { version: string; tier: string | null; inputs: Record<string, unknown>; parameters: Record<string, unknown>; factors: Record<string, unknown> | string[] }; gaps: unknown[] };
export type PersonalMatchPlayer = {
  nickname: string; aliases: string[]; uid: string | null; team: string | null;
  kills: number | null; deaths: number | null; assists: number | null;
  game_score: number | null; rank: number | null;
  rating: number | null; rating_display: string | null; rating_color: RatingColor | null;
  selected: boolean; user_confirmed: boolean;
};
export type PersonalMatchSnapshot = {
  id: string; screenshot_id: number | null; source: 'screenshot' | 'automatic'; source_label: string;
  mode: Mode; match_id: string | null; map: string | null; score: [number, number] | null;
  ended_at: string | null; date_label: string | null; loaded_at: string;
  nickname: string; requested_nickname: string | null; player_uid: string | null;
  kills: number; deaths: number; assists: number; rating: number; rating_display: string; rating_color: RatingColor;
  formula_version: string; rating_tier: 'match' | 'rounds' | 'match_rounds' | 'duels'; personal_rounds?: number | null; match_rounds?: number | null; rounds_basis?: string | null; method_note: string; selected_index: number;
  players: PersonalMatchPlayer[]; original_rating: number | null; original_rating_tier: string | null;
};

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

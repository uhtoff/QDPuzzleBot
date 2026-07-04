export interface Guild {
  id: number;
  guild_snowflake: string | null;
  guild_name: string;
  is_admin: boolean;
}

export interface User {
  id: number;
  username: string | null;
  global_name: string | null;
  avatar: string | null;
  avatar_url: string | null;
}

export interface HuntSummary {
  uid: string;
  name: string;
  url: string;
  archived: boolean;
  guild_snowflake: number | null;
  guild_name: string | null;
  start_time: string | null;
  solve_time: string | null;
}

export interface HuntList {
  active: HuntSummary[];
  archived: HuntSummary[];
}

export interface PuzzleTagGroup {
  id: number;
  name: string;
  group_code: string | null;
  is_target_of: boolean;
  edges: string[];
  group_type: string | null;
}

export interface Activity {
  at: string | null;
  author: string | null;
}

export interface AdditionalSheet {
  id: number;
  name: string;
  page_id: string | null;
}

export interface MiniPuzzle {
  id: number;
  name: string;
  solved: boolean;
  solution: string | null;
  channel_id: string | null;
}

export interface Puzzle {
  id: number;
  name: string;
  url: string | null;
  channel_id: string | null;
  discord_url: string | null;
  sheet_page: string | null;
  is_meta: boolean;
  meta_kind: "runaround" | "metameta" | "meta" | "capstone" | null;
  is_minimeta: boolean;
  status: string | null;
  solved: boolean;
  on_solved_sheet: boolean;
  solution: string | null;
  priority: string | null;
  puzzle_type: string | null;
  notes: string[];
  start_time: string | null;
  solve_time: string | null;
  last_activity: Activity | null;
  rounds: PuzzleTagGroup[];
  sheets: AdditionalSheet[];
  mini_puzzles: MiniPuzzle[];
  rollup_stale?: boolean;
  rollup_error?: string | null;
}

export interface BoardGroup {
  tag_group_id: number;
  name: string;
  group_code: string | null;
  puzzles: Puzzle[];
  target_solution: string | null;
  target_solved: boolean;
  num_feeders: number;
  num_solved_feeders: number;
  rollup_stale: boolean;
  rollup_error: string | null;
  source_mode: string;  // "round" | "subtree" | "explicit" (M7)
  group_type: string | null;
  target_puzzle_id: number | null;
}

export interface HuntDetail {
  hunt: {
    uid: string;
    name: string;
    url: string;
    archived: boolean;
    is_gated: boolean;
    role_name: string | null;
    guild_snowflake: string | null;
    sheet_base_url: string | null;
    solved_sheet_base_url: string | null;
    can_manage: boolean;
    is_admin: boolean;
    start_timestamp: number | null;
    username: string;
    password: string;
    url_sep: string;
    puzzle_prefix: string;
  };
  summary: { total: number; solved: number };
  board: BoardGroup[];
  // Ordered create-meta type labels (CREATE_META_TYPES in puzzle_service.py).
  meta_types: string[];
}

export interface ActivityEvent {
  type: string;
  channel_id?: string;
  guild_id?: number;
  uid?: string;
  at?: string;
  author?: string;
}

export interface RollupAnswer {
  name: string;
  solution: string | null;
}

export interface RollupAnswers {
  mode: string;
  answers: RollupAnswer[];
}

export interface EvidenceItem {
  id: number;
  source_puzzle_id: number;
  source_puzzle_name: string;
  target_puzzle_id: number | null;
  target_puzzle_name: string | null;
  note: string;
  created_at: string | null;
}

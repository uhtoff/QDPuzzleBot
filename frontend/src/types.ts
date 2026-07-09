export interface Guild {
  id: number;
  guild_snowflake: string | null;
  guild_name: string;
  can_administer: boolean;
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
  discord_deep_url: string | null;
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
    can_administer: boolean;
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

export interface ChatAttachment {
  id: string;
  filename: string;
  content_type: string | null;
  size: number;
  url: string | null; // /api/attachments/<id>, or null if not downloaded
}

export interface ChatEmbedField {
  name: string;
  value: string;
  inline: boolean;
}

export interface ChatEmbed {
  title?: string;
  description?: string;
  url?: string;
  color?: number; // Discord embed accent, 0xRRGGBB
  fields?: ChatEmbedField[];
  footer?: string;
}

export interface ChatMessage {
  id: string;
  author_id: string;
  author_name: string;
  author_avatar: string | null;
  kind: number; // 0 human, 1 this-bot, 2 other-bot, 3 command
  content: string;
  embeds: ChatEmbed[];
  reference_id: string | null;
  created_at: string;
  edited_at: string | null;
  attachments: ChatAttachment[];
}

export interface ActivityEvent {
  type: string;
  channel_id?: string;
  guild_id?: number;
  uid?: string;
  at?: string;
  author?: string;
  message?: ChatMessage; // for type === "message" | "message_edit"
  message_id?: string; // for type === "message_delete"
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

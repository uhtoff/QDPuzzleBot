export interface Guild {
  id: number;
  guild_snowflake: string | null;
  guild_name: string;
  can_administer: boolean;
  /** False while the guild still matches the ADMIN tier by role name — the one
   *  state worth a banner. The organiser tier's state deliberately isn't here:
   *  leaving it unpinned is legitimate, and nagging about it would train people
   *  to ignore the banner. */
  admin_tier_pinned: boolean;
}

/** One Discord role, as offered by the guild settings API. `id` is a STRING:
 *  Discord snowflakes are 64-bit and exceed JS's safe integer range. */
export interface GuildRole {
  id: string;
  name: string;
}

/** One authorisation tier's state. `pinned` false means the tier is still
 *  matched by role NAME, so anyone who can create a role can grant it to
 *  themselves — the page warns about that. */
export interface GuildTier {
  pinned: boolean;
  role_ids: string[];
}

export interface GuildSettingsValues {
  discord_bot_channel: string | null;
  drive_parent_id: string | null;
  drive_resources_id: string | null;
  discord_use_voice_channels: boolean;
  website_url: string | null;
  template_link: string | null;
  admin_role_ids: string[];
  organiser_role_ids: string[];
}

export interface GuildSettingsResponse {
  guild: { id: number; guild_snowflake: string; guild_name: string };
  settings: GuildSettingsValues;
  tiers: { admin: GuildTier; organiser: GuildTier };
  roles: GuildRole[];
  your_role_ids: string[];
}

export type GuildSettingsPatch = Partial<GuildSettingsValues>;

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

/** A working sheet belonging to the hunt rather than to any one puzzle. */
export interface HuntSheet {
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
    is_team_hunt: boolean;
    role_name: string | null;
    guild_snowflake: string | null;
    sheet_base_url: string | null;
    solved_sheet_base_url: string | null;
    can_manage: boolean;
    can_administer: boolean;
    organiser_gate_active: boolean;
    start_timestamp: number | null;
    username: string;
    password: string;
    url_sep: string;
    puzzle_prefix: string;
  };
  summary: { total: number; solved: number };
  board: BoardGroup[];
  // The hunt's own working sheets — those belonging to no puzzle.
  hunt_sheets: HuntSheet[];
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

export type RegionRole = "locates" | "informs";

export interface RegionShape {
  type: "rect";
  /** Normalised 0-1, top-left corner first: [[x1,y1],[x2,y2]].
   *  Normalising is what lets the SVG overlay use these values directly at any
   *  zoom level, and what lets a region survive the image being re-tiled. */
  points: [[number, number], [number, number]];
}

export interface CanvasRegion {
  id: number;
  shape: RegionShape;
  label: string;
  note: string | null;
  /** role is non-null exactly when puzzle_id is; the API rejects a half-set link. */
  puzzle_id: number | null;
  role: RegionRole | null;
}

export type MarkKind = "pen" | "line" | "text";

export interface MarkStyle {
  colour: string;
  width: number;
  opacity: number;
  arrow: boolean;
}

/** One purely-visual mark on an image. Coordinates are normalised 0-1, so the
 *  SVG overlay uses them directly at any zoom. */
export interface MediaMark {
  id: number;
  media_id: number;
  kind: MarkKind;
  shape: { points: [number, number][]; size?: number };
  style: MarkStyle;
  text: string | null;
}

export interface PuzzleImage {
  id: number;
  media_id: number;
  media_url: string;
  source_region_id: number | null;
  position: number;
}

export interface HuntCanvas {
  id: number;
  name: string;
  note: string | null;
  media_id: number;
  regions: CanvasRegion[];
  /** From the media row, so the viewer can size itself and choose a zoom level
   *  before fetching a single tile. Optional only for the vanished-media case. */
  width?: number;
  height?: number;
  tile_status?: "none" | "pending" | "ready" | "failed";
  tile_levels?: number;
}

export interface EvidenceItem {
  id: number;
  source_puzzle_id: number | null;
  source_puzzle_name: string | null;
  // Set instead of the puzzle source when the evidence came from a hunt-level
  // working sheet. At most one of the two is ever non-null.
  source_sheet_id: number | null;
  source_sheet_name: string | null;
  source_region_id: number | null;
  /** Region label, else its canvas's name, else "Canvas (filename)". */
  source_region_name: string | null;
  target_puzzle_id: number | null;
  target_puzzle_name: string | null;
  note: string;
  media_id: number | null;
  media_url: string | null;
  created_at: string | null;
}

export interface TeamHuntSummary {
  uid: string;
  name: string;
  team: string | null;
  archived: boolean;
  team_server: string | null;
  on_server_count: number | null;
  invited_waiting_count: number;
}

export interface TeamHuntMember {
  user_id: string;
  name: string;
  team_role: boolean;
  mod: boolean;
}

export interface TeamHuntInvited {
  user_id: string;
  name: string;
  invited_at: string;
}

export interface TeamHuntHistoryRow {
  at: string;
  user_id: string;
  name: string;
  action: string;
  via: string;
  actor_id: string | null;
  actor_name: string | null;
}

export interface TeamHuntDetail {
  hunt: TeamHuntSummary;
  bot_on_server: boolean;
  team_role_missing: boolean;
  on_server: TeamHuntMember[] | null;
  invited_waiting: TeamHuntInvited[];
  history: TeamHuntHistoryRow[];
}

export interface SetupStatus {
  template_problems: string[];
  already_set_up: string | null;
  existing: {
    hub_id: string;
    roster_role_id: string;
    hunt_name: string;
    hunt_url: string;
  } | null;
  hubs: { id: string; name: string; roles: { id: string; name: string }[] }[];
}

export interface SetupResult {
  ok: boolean;
  lines: string[];
  error: string | null;
  hunt_uid: string | null;
  invites: string | null;
}

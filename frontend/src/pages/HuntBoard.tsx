import { memo, useCallback, useEffect, useMemo, useState, useTransition } from "react";
import {
  Accordion,
  Anchor,
  Badge,
  Box,
  Button,
  Center,
  Group,
  Loader,
  Progress,
  SegmentedControl,
  Select,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { useMediaQuery } from "@mantine/hooks";
import { Link, useParams } from "react-router-dom";
import { IconArrowLeft, IconBrowserX } from "@tabler/icons-react";
import { useHunt } from "../api";
import { closeSolved, extensionSupports } from "../lib/extension";
import { boardViews, type BoardView } from "../lib/boardViews";
import { boardGroupRows, filterActive, type PuzzleFilter } from "../lib/puzzleFilter";
import { PuzzleFilterBar } from "../components/PuzzleFilterBar";
import { loadFilter, saveFilter } from "../lib/filterStorage";
import { useQueryClient } from "@tanstack/react-query";
import { BoardGroup } from "../components/BoardGroup";
import { PuzzleTable } from "../components/PuzzleTable";
import { DragBoard } from "../components/DragBoard";
import { CreateControls } from "../components/CreateControls";
import { PuzzleDrawer } from "../components/PuzzleDrawer";
import { ChatViewer } from "../components/ChatViewer";
import { SolveModal } from "../components/PuzzleEditCells";
import { EvidencePanel } from "../components/EvidencePanel";
import { CanvasPanel } from "../components/CanvasPanel";
import { CanvasTogglePanel } from "../components/CanvasTogglePanel";
import { ReconcilePanel } from "../components/ReconcilePanel";
import { MediaSweepPanel } from "../components/MediaSweepPanel";
import { HuntInfoPanel } from "../components/HuntInfoPanel";
import { AdminSettingsPanel } from "../components/AdminSettingsPanel";
import { VoiceBadge } from "../components/VoiceBadge";
import { GateHuntPanel } from "../components/GateHuntPanel";
import { ManageSection } from "../components/ManageSection";
import { useActivityFeed, type LiveActivity } from "../hooks/useActivityFeed";
import { useVoiceFeed } from "../hooks/useVoiceFeed";
import { useLingering } from "../hooks/useLingering";
import {
  applyOverrides,
  deriveOverrides,
  loadCollapse,
  saveCollapse,
  type CollapseOverrides,
} from "../lib/groupCollapse";
import { huntVoiceCount, type LiveVoice } from "../lib/voice";
import { useViewAs, applyViewAs } from "../context/ViewAsContext";
import type { HuntDetail, Puzzle } from "../types";
import { ChannelNamesContext } from "../components/DiscordMarkdown";
import { channelNamesFromBoard } from "../lib/discordLinks";
import "./HuntBoard.css";

interface BoardByGroupViewProps {
  data: HuntDetail;
  allPuzzles: Puzzle[];
  filter: PuzzleFilter;
  onChangeFilter: (next: PuzzleFilter) => void;
  /** Remembered open/collapse choices (see lib/groupCollapse). */
  collapse: CollapseOverrides;
  onCollapseChange: (next: CollapseOverrides) => void;
  /** Open groups while a search is typed: not remembered, null = all matches open. */
  searchOpen: string[] | null;
  onSearchOpenChange: (next: string[]) => void;
  live: Record<string, LiveActivity>;
  voice: LiveVoice;
  isNarrow: boolean;
  onSolve: (puzzle: Puzzle) => void;
  onOpenNotes: (puzzle: Puzzle) => void;
  onOpenChat: (puzzle: Puzzle) => void;
}

const BoardByGroupView = memo(function BoardByGroupView({
  data,
  allPuzzles,
  filter,
  onChangeFilter,
  collapse,
  onCollapseChange,
  searchOpen,
  onSearchOpenChange,
  live,
  voice,
  isNarrow,
  onSolve,
  onOpenNotes,
  onOpenChat,
}: BoardByGroupViewProps) {
  // Bare groups (no puzzles) are drop targets for the Arrange view only —
  // hide them from the board summary so it isn't cluttered with empties.
  // Arrange keeps Unassigned Feeders far left; the By-group summary instead
  // places it below unsolved targets and plain groups but above solved
  // targets (which sink to the bottom).
  const { filtering, rowsByGroup, shownCount, boardGroups, defaultOpen } = useMemo(() => {
    const isFiltering = filterActive(filter);
    // While filtering, each group's own rows (see boardGroupRows): a group matching by
    // name shows everything; otherwise puzzles match on their own name or answer.
    const rowsMap = new Map(
      data.board.map((g) => [g.tag_group_id, boardGroupRows(g.name, g.puzzles, filter)]),
    );
    const withPuzzles = data.board.filter((g) => (rowsMap.get(g.tag_group_id) ?? []).length > 0);
    const count = new Set(
      withPuzzles.flatMap((g) => (rowsMap.get(g.tag_group_id) ?? []).map((p) => p.id)),
    ).size;
    const unassigned = withPuzzles.filter((g) => g.tag_group_id === 0);
    const rest = withPuzzles.filter((g) => g.tag_group_id !== 0);
    const firstSolved = rest.findIndex((g) => g.target_solved);
    const at = firstSolved === -1 ? rest.length : firstSolved;
    const ordered = [...rest.slice(0, at), ...unassigned, ...rest.slice(at)];
    // Filtering opens every group with a match, solved ones included.
    const defOpen = ordered
      .filter((g) => isFiltering || !g.target_solved)
      .map((g) => String(g.tag_group_id));
    return {
      filtering: isFiltering,
      rowsByGroup: rowsMap,
      shownCount: count,
      boardGroups: ordered,
      defaultOpen: defOpen,
    };
  }, [data.board, filter]);

  // A typed search is a temporary "find it" mode: every match opens, and
  // opening or collapsing there is not remembered. Otherwise the person's
  // remembered choices adjust the default.
  const searching = filter.search.trim() !== "";
  const visibleIds = useMemo(() => boardGroups.map((g) => String(g.tag_group_id)), [boardGroups]);
  const effectiveOpen = useMemo(
    () => (searching ? searchOpen ?? defaultOpen : applyOverrides(defaultOpen, visibleIds, collapse)),
    [searching, searchOpen, defaultOpen, visibleIds, collapse],
  );
  const openSet = useMemo(() => new Set(effectiveOpen), [effectiveOpen]);
  const onOpenGroupsChange = useCallback(
    (next: string[]) => {
      if (searching) onSearchOpenChange(next);
      else onCollapseChange(deriveOverrides(collapse, defaultOpen, visibleIds, next));
    },
    [searching, onSearchOpenChange, onCollapseChange, collapse, defaultOpen, visibleIds],
  );

  return (
    <Stack gap="xs">
      <PuzzleFilterBar
        puzzles={allPuzzles}
        filter={filter}
        onChange={onChangeFilter}
        shown={filtering ? shownCount : allPuzzles.length}
        total={allPuzzles.length}
      />
      {filtering && boardGroups.length === 0 && (
        <Text c="dimmed" size="sm">No puzzles match.</Text>
      )}
      <Group justify="flex-end">
        <Anchor component="button" type="button" size="sm" onClick={() => onOpenGroupsChange([])}>
          Collapse all
        </Anchor>
      </Group>
      <Accordion
        variant="separated"
        multiple
        value={effectiveOpen}
        onChange={onOpenGroupsChange}
      >
        {boardGroups.map((g) => {
          const idStr = String(g.tag_group_id);
          return (
            <BoardGroup
              key={g.tag_group_id}
              group={g}
              detail={data}
              live={live}
              voice={voice}
              isOpen={openSet.has(idStr)}
              isNarrow={isNarrow}
              onSolve={onSolve}
              onOpenNotes={onOpenNotes}
              onOpenChat={onOpenChat}
              rows={filtering ? rowsByGroup.get(g.tag_group_id) : undefined}
            />
          );
        })}
      </Accordion>
    </Stack>
  );
});

export default function HuntBoard() {
  const { uid } = useParams();
  const { data: rawData, isLoading, error } = useHunt(uid);
  const { viewAs } = useViewAs();
  const qc = useQueryClient();
  const live = useActivityFeed((u) => {
    if (u === uid) {
      qc.invalidateQueries({ queryKey: ["hunt", uid] });
      qc.invalidateQueries({ queryKey: ["evidence", uid] });
      qc.invalidateQueries({ queryKey: ["canvases", uid] });
      // Markup and puzzle images are keyed by media/puzzle id, not by hunt, so
      // they need a PREFIX invalidation — react-query matches partial keys.
      // Without these two the server's push arrives and every client ignores
      // it: two people marking up the same image would not see each other's
      // ink until a reload, even though the mutation pushed correctly.
      // At most a couple of these queries are ever mounted (one open modal,
      // one open drawer), so invalidating the whole prefix is cheap.
      qc.invalidateQueries({ queryKey: ["markup"] });
      qc.invalidateQueries({ queryKey: ["puzzle-images"] });
    }
  });
  const voice = useVoiceFeed();
  // Separate immediate selection (so the SegmentedControl responds on frame 1)
  // from the transition-deferred panel switch, and keep visited puzzle-board
  // tabs mounted so switching back is an instant CSS toggle.
  const [selectedView, setSelectedView] = useState<BoardView>("board");
  const [renderedView, setRenderedView] = useState<BoardView>("board");
  const [visitedViews, setVisitedViews] = useState<Set<BoardView>>(() => new Set(["board"]));
  const [isPending, startTransition] = useTransition();

  const [notesPuzzleId, setNotesPuzzleId] = useState<number | null>(null);
  const [chatPuzzleId, setChatPuzzleId] = useState<number | null>(null);
  const [solvePuzzleId, setSolvePuzzleId] = useState<number | null>(null);
  // By-group open/collapse choices, remembered per hunt in this browser.
  const [collapse, setCollapse] = useState<CollapseOverrides>(() => loadCollapse(uid));
  const [searchOpen, setSearchOpen] = useState<string[] | null>(null);
  const isNarrow = useMediaQuery("(max-width: 768px)") ?? false;

  // One filter for both "By group" and "All puzzles", so it survives switching views.
  // Its settings (not the search) are remembered per hunt in this browser.
  const [filter, setFilter] = useState<PuzzleFilter>(() => loadFilter(uid));
  // The route param can change without remounting (hunt to hunt), so reload then.
  useEffect(() => {
    setFilter(loadFilter(uid));
    setCollapse(loadCollapse(uid));
    setSearchOpen(null);
    setVisitedViews(new Set(["board"]));
    setSelectedView("board");
    setRenderedView("board");
  }, [uid]);

  const switchView = useCallback((next: BoardView) => {
    setSelectedView(next);
    startTransition(() => {
      setRenderedView(next);
      setVisitedViews((prev) => (prev.has(next) ? prev : new Set(prev).add(next)));
    });
  }, []);

  // Saved here, in the setter, not in an effect on `filter`: an effect would
  // write the previous hunt's filter under the new hunt's key on a switch.
  const updateFilter = useCallback(
    (next: PuzzleFilter) => {
      setFilter(next);
      saveFilter(uid, next);
    },
    [uid],
  );
  // A changed search re-opens every group that still has matches.
  const changeFilter = useCallback(
    (next: PuzzleFilter) => {
      updateFilter(next);
      setSearchOpen(null);
    },
    [updateFilter],
  );
  // Saved in the setter, like the filter (see updateFilter).
  const updateCollapse = useCallback(
    (next: CollapseOverrides) => {
      setCollapse(next);
      saveCollapse(uid, next);
    },
    [uid],
  );

  // A flat, de-duplicated puzzle list for the "All puzzles" table.
  const allPuzzles = useMemo(() => {
    if (!rawData) return [];
    const seen = new Map<number, (typeof rawData.board)[number]["puzzles"][number]>();
    rawData.board.forEach((g) => g.puzzles.forEach((p) => seen.set(p.id, p)));
    return Array.from(seen.values());
  }, [rawData]);

  // Apply the Admin "view as" ceiling: overriding can_manage/can_administer here
  // cascades to every gated child, since they all read detail.hunt.{can_manage,can_administer}.
  const data = useMemo(
    () => (rawData ? applyViewAs(rawData, viewAs) : null),
    [rawData, viewAs],
  );

  // The chat dialog is mounted only while in use; useLingering keeps its fade.
  const chat = useLingering(
    chatPuzzleId != null ? allPuzzles.find((p) => p.id === chatPuzzleId) ?? null : null,
  );

  const openNotes = useCallback((p: Puzzle) => setNotesPuzzleId(p.id), []);
  const openChat = useCallback((p: Puzzle) => setChatPuzzleId(p.id), []);
  const openSolve = useCallback((p: Puzzle) => setSolvePuzzleId(p.id), []);
  // Names Discord links in notes (puzzles, minis, the general channel).
  const channelNames = useMemo(
    () => (data ? channelNamesFromBoard(data.board, data.hunt) : null),
    [data],
  );

  if (isLoading) {
    return (
      <Center h={200}>
        <Loader />
      </Center>
    );
  }
  if (error || !data) {
    return <Text c="red">Could not load this hunt (you may not have access).</Text>;
  }

  const views = boardViews({
    canManage: data.hunt.can_manage,
    canAdminister: data.hunt.can_administer,
    canvas: data.hunt.canvas_enabled,
  });
  // A view that is no longer offered (Canvas just switched off, say) falls back to By group.
  const activeControlView: BoardView = views.some((v) => v.value === selectedView) ? selectedView : "board";
  const shown: BoardView = views.some((v) => v.value === renderedView) ? renderedView : "board";
  const notesPuzzle = notesPuzzleId != null ? allPuzzles.find((p) => p.id === notesPuzzleId) ?? null : null;
  const solvePuzzle = solvePuzzleId != null ? allPuzzles.find((p) => p.id === solvePuzzleId) ?? null : null;
  const pct = data.summary.total ? Math.round((data.summary.solved / data.summary.total) * 100) : 0;

  return (
    <ChannelNamesContext.Provider value={channelNames}>
    <Stack gap="md">
      <Group justify="space-between" align="flex-start">
        <div>
          <Anchor component={Link} to="/" size="sm">
            <Group gap={4}>
              <IconArrowLeft size={14} /> All hunts
            </Group>
          </Anchor>
          <Group gap="sm" mt={4}>
            <Title order={3}>
              <Anchor href={data.hunt.url} target="_blank" inherit>
                {data.hunt.name}
              </Anchor>
            </Title>
            {data.hunt.archived && <Badge color="gray">Archived</Badge>}
            {data.hunt.voice_url && huntVoiceCount(data.hunt.uid, data.hunt.voice_count, voice) > 0 && (
              <Anchor href={data.hunt.voice_url} target="_blank" rel="noreferrer" aria-label="Join hunt voice">
                <VoiceBadge count={huntVoiceCount(data.hunt.uid, data.hunt.voice_count, voice)} />
              </Anchor>
            )}
          </Group>
        </div>
        <Stack gap={4} w={220}>
          <Text size="sm" ta="right">
            {data.summary.solved}/{data.summary.total} solved
          </Text>
          <Progress value={pct} color="green" />
        </Stack>
      </Group>

      {/* Hunt info — visible to all users who can see the hunt */}
      <HuntInfoPanel
        uid={uid!}
        editable={!data.hunt.archived}
        url={data.hunt.url}
        start_timestamp={data.hunt.start_timestamp}
        end_timestamp={data.hunt.end_timestamp}
        sheet_url={data.hunt.sheet_url}
        solved_sheet_url={data.hunt.solved_sheet_url}
        drive_folder_url={data.hunt.drive_folder_url}
        hunt_sheets={data.hunt_sheets}
        username={data.hunt.username}
        password={data.hunt.password}
      />

      <Group justify="space-between" align="center">
        {/* Wide screens: the tab bar. Narrow ones get a dropdown, since the bar
            needs up to 660px and cannot scroll. */}
        <SegmentedControl
          className="hunt-view-tabs"
          data-pending={isPending ? "true" : undefined}
          visibleFrom="md"
          value={activeControlView}
          onChange={(v) => switchView(v as BoardView)}
          data={views.map(({ label, value }) => ({ label, value }))}
          w={views.reduce((sum, v) => sum + v.width, 0)}
        />
        <Select
          hiddenFrom="md"
          aria-label="View"
          value={activeControlView}
          onChange={(v) => v && switchView(v as BoardView)}
          data={views.map(({ label, value }) => ({ label, value }))}
          allowDeselect={false}
          w={180}
        />
        <Group gap="sm">
          {extensionSupports("closeSolved") && data.hunt.sheet_mode === "files" && (
            <Button variant="light" color="gray" leftSection={<IconBrowserX size={16} />} onClick={() => closeSolved()}>
              Close solved tabs
            </Button>
          )}
          {!data.hunt.archived && <CreateControls detail={data} uid={uid!} />}
        </Group>
      </Group>

      <Box style={{ opacity: isPending ? 0.65 : 1, transition: "opacity 120ms ease" }}>
        {(visitedViews.has("board") || shown === "board") && (
          <Box style={{ display: shown === "board" ? undefined : "none" }}>
            <BoardByGroupView
              data={data}
              allPuzzles={allPuzzles}
              filter={filter}
              onChangeFilter={changeFilter}
              collapse={collapse}
              onCollapseChange={updateCollapse}
              searchOpen={searchOpen}
              onSearchOpenChange={setSearchOpen}
              live={live}
              voice={voice}
              isNarrow={isNarrow}
              onSolve={openSolve}
              onOpenNotes={openNotes}
              onOpenChat={openChat}
            />
          </Box>
        )}
        {(visitedViews.has("all") || shown === "all") && (
          <Box style={{ display: shown === "all" ? undefined : "none" }}>
            <PuzzleTable
              detail={data}
              puzzles={allPuzzles}
              live={live}
              voice={voice}
              onSolve={openSolve}
              onOpenNotes={openNotes}
              onOpenChat={openChat}
              filter={filter}
              onFilterChange={updateFilter}
            />
          </Box>
        )}
        {data.hunt.can_manage && (visitedViews.has("arrange") || shown === "arrange") && (
          <Box style={{ display: shown === "arrange" ? undefined : "none" }}>
            <DragBoard detail={data} uid={uid!} />
          </Box>
        )}
        {shown === "evidence" && <EvidencePanel uid={uid!} detail={data} />}
        {shown === "canvas" && <CanvasPanel uid={uid!} detail={data} />}
        {shown === "manage" && (data.hunt.can_manage || data.hunt.can_administer) && (() => {
          // Organiser-level pieces (hidden on archived hunts, where can_manage is false).
          const canvasControl = data.hunt.can_manage && (
            <CanvasTogglePanel uid={uid!} enabled={data.hunt.canvas_enabled} />
          );
          const maintenance = data.hunt.can_manage && (
            <>
              <ReconcilePanel uid={uid!} />
              {/* The media sweep is a bulk delete, so organiser-gated too. */}
              <MediaSweepPanel uid={uid!} />
            </>
          );
          return (
            <Stack gap="md" maw={760}>
              {data.hunt.can_administer ? (
                // Admin: every section; reachable even when archived (to unarchive).
                <AdminSettingsPanel
                  uid={uid!}
                  huntName={data.hunt.name}
                  url={data.hunt.url}
                  start_timestamp={data.hunt.start_timestamp}
                  end_timestamp={data.hunt.end_timestamp}
                  username={data.hunt.username}
                  password={data.hunt.password}
                  url_sep={data.hunt.url_sep}
                  puzzle_prefix={data.hunt.puzzle_prefix}
                  archived={data.hunt.archived}
                  isTeamHunt={data.hunt.is_team_hunt}
                  multiTeam={data.hunt.multi_team}
                  hasSolvedSheet={!!data.hunt.solved_sheet_url}
                  sheetMode={data.hunt.sheet_mode}
                  puzzleCount={data.summary.total}
                  voicePerPuzzle={data.hunt.voice_per_puzzle}
                  headerStyle={data.hunt.header_style}
                  canvasControl={canvasControl}
                  maintenance={maintenance}
                  teamRole={
                    // Hidden on archived (read-only) hunts.
                    !data.hunt.archived && (
                      <GateHuntPanel uid={uid!} isGated={data.hunt.is_gated} roleName={data.hunt.role_name} />
                    )
                  }
                />
              ) : (
                // Organiser without admin: only the organiser-level sections.
                <>
                  <ManageSection title="Advanced" collapsible>{canvasControl}</ManageSection>
                  <ManageSection title="Maintenance">{maintenance}</ManageSection>
                </>
              )}
            </Stack>
          );
        })()}
      </Box>

      <PuzzleDrawer puzzle={notesPuzzle} detail={data} voice={voice} onClose={() => setNotesPuzzleId(null)} />
      <SolveModal puzzle={solvePuzzle} uid={data.hunt.uid} onClose={() => setSolvePuzzleId(null)} />
      {chat.value && (
        <ChatViewer
          opened={chat.opened}
          onClose={() => setChatPuzzleId(null)}
          channelId={chat.value.channel_id}
          live={!data.hunt.archived}
          title={chat.value.name}
          discordDeepUrl={chat.value.discord_deep_url}
          discordUrl={chat.value.discord_url}
        />
      )}
    </Stack>
    </ChannelNamesContext.Provider>
  );
}

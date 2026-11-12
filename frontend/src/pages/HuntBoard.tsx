import { useEffect, useMemo, useState } from "react";
import {
  Accordion,
  Anchor,
  Badge,
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
import { useActivityFeed } from "../hooks/useActivityFeed";
import { useVoiceFeed } from "../hooks/useVoiceFeed";
import { huntVoiceCount } from "../lib/voice";
import { useViewAs, applyViewAs } from "../context/ViewAsContext";
import type { Puzzle } from "../types";

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
  const [view, setView] = useState<BoardView>("board");
  const [notesPuzzleId, setNotesPuzzleId] = useState<number | null>(null);
  const [openGroups, setOpenGroups] = useState<string[] | null>(null);
  // One filter for both "By group" and "All puzzles", so it survives switching views.
  // Its settings (not the search) are remembered per hunt in this browser.
  const [filter, setFilter] = useState<PuzzleFilter>(() => loadFilter(uid));
  // The route param can change without remounting (hunt to hunt), so reload then.
  useEffect(() => {
    setFilter(loadFilter(uid));
    setOpenGroups(null);
  }, [uid]);
  // Saved here, in the setter, not in an effect on `filter`: an effect would
  // write the previous hunt's filter under the new hunt's key on a switch.
  const updateFilter = (next: PuzzleFilter) => {
    setFilter(next);
    saveFilter(uid, next);
  };
  // A changed filter re-opens every group that still has matches.
  const changeFilter = (next: PuzzleFilter) => {
    updateFilter(next);
    setOpenGroups(null);
  };

  // A flat, de-duplicated puzzle list for the "All puzzles" table.
  const allPuzzles = useMemo(() => {
    if (!rawData) return [];
    const seen = new Map<number, (typeof rawData.board)[number]["puzzles"][number]>();
    rawData.board.forEach((g) => g.puzzles.forEach((p) => seen.set(p.id, p)));
    return Array.from(seen.values());
  }, [rawData]);

  if (isLoading) {
    return (
      <Center h={200}>
        <Loader />
      </Center>
    );
  }
  if (error || !rawData) {
    return <Text c="red">Could not load this hunt (you may not have access).</Text>;
  }

  // Apply the Admin "view as" ceiling: overriding can_manage/can_administer here
  // cascades to every gated child, since they all read detail.hunt.{can_manage,can_administer}.
  const data = applyViewAs(rawData, viewAs);

  const views = boardViews({
    canManage: data.hunt.can_manage,
    canAdminister: data.hunt.can_administer,
    canvas: data.hunt.canvas_enabled,
  });
  // A view that is no longer offered (Canvas just switched off, say) falls back to By group.
  const shown: BoardView = views.some((v) => v.value === view) ? view : "board";
  const notesPuzzle = allPuzzles.find((p) => p.id === notesPuzzleId) ?? null;
  const openNotes = (p: Puzzle) => setNotesPuzzleId(p.id);
  const pct = data.summary.total ? Math.round((data.summary.solved / data.summary.total) * 100) : 0;

  return (
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
          visibleFrom="md"
          value={shown}
          onChange={(v) => setView(v as BoardView)}
          data={views.map(({ label, value }) => ({ label, value }))}
          w={views.reduce((sum, v) => sum + v.width, 0)}
        />
        <Select
          hiddenFrom="md"
          aria-label="View"
          value={shown}
          onChange={(v) => v && setView(v as BoardView)}
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

      {shown === "board" && (() => {
        // Bare groups (no puzzles) are drop targets for the Arrange view only —
        // hide them from the board summary so it isn't cluttered with empties.
        // Arrange keeps Unassigned Feeders far left; the By-group summary instead
        // places it below unsolved targets and plain groups but above solved
        // targets (which sink to the bottom).
        const filtering = filterActive(filter);
        // While filtering, each group's own rows (see boardGroupRows): a group matching by
        // name shows everything; otherwise puzzles match on their own name or answer.
        const rowsByGroup = new Map(data.board.map((g) => [g.tag_group_id, boardGroupRows(g.name, g.puzzles, filter)]));
        const withPuzzles = data.board.filter((g) => (rowsByGroup.get(g.tag_group_id) ?? []).length > 0);
        const shownCount = new Set(withPuzzles.flatMap((g) => (rowsByGroup.get(g.tag_group_id) ?? []).map((p) => p.id))).size;
        const unassigned = withPuzzles.filter((g) => g.tag_group_id === 0);
        const rest = withPuzzles.filter((g) => g.tag_group_id !== 0);
        const firstSolved = rest.findIndex((g) => g.target_solved);
        const at = firstSolved === -1 ? rest.length : firstSolved;
        const boardGroups = [...rest.slice(0, at), ...unassigned, ...rest.slice(at)];
        // Filtering opens every group with a match, solved ones included.
        const defaultOpen = boardGroups
          .filter((g) => filtering || !g.target_solved)
          .map((g) => String(g.tag_group_id));
        return (
          <Stack gap="xs">
            <PuzzleFilterBar
              puzzles={allPuzzles}
              filter={filter}
              onChange={changeFilter}
              shown={filtering ? shownCount : allPuzzles.length}
              total={allPuzzles.length}
            />
            {filtering && boardGroups.length === 0 && (
              <Text c="dimmed" size="sm">No puzzles match.</Text>
            )}
            <Group justify="flex-end">
              <Anchor component="button" type="button" size="sm" onClick={() => setOpenGroups([])}>
                Collapse all
              </Anchor>
            </Group>
            <Accordion
              variant="separated"
              multiple
              value={openGroups ?? defaultOpen}
              onChange={setOpenGroups}
            >
              {boardGroups.map((g) => (
                <BoardGroup key={g.tag_group_id} group={g} detail={data} live={live} voice={voice} onOpenNotes={openNotes} rows={filtering ? rowsByGroup.get(g.tag_group_id) : undefined} />
              ))}
            </Accordion>
          </Stack>
        );
      })()}
      {shown === "all" && (
        <PuzzleTable detail={data} puzzles={allPuzzles} live={live} voice={voice} onOpenNotes={openNotes} filter={filter} onFilterChange={updateFilter} />
      )}
      {shown === "arrange" && data.hunt.can_manage && <DragBoard detail={data} uid={uid!} />}
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

      <PuzzleDrawer puzzle={notesPuzzle} detail={data} voice={voice} onClose={() => setNotesPuzzleId(null)} />
    </Stack>
  );
}

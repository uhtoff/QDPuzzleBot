import { useMemo, useState } from "react";
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
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { Link, useParams } from "react-router-dom";
import { IconArrowLeft, IconBrowserX } from "@tabler/icons-react";
import { useHunt } from "../api";
import { closeSolved, extensionPresent } from "../lib/extension";
import { useQueryClient } from "@tanstack/react-query";
import { BoardGroup } from "../components/BoardGroup";
import { PuzzleTable } from "../components/PuzzleTable";
import { DragBoard } from "../components/DragBoard";
import { CreateControls } from "../components/CreateControls";
import { PuzzleDrawer } from "../components/PuzzleDrawer";
import { EvidencePanel } from "../components/EvidencePanel";
import { CanvasPanel } from "../components/CanvasPanel";
import { ReconcilePanel } from "../components/ReconcilePanel";
import { MediaSweepPanel } from "../components/MediaSweepPanel";
import { HuntInfoPanel } from "../components/HuntInfoPanel";
import { AdminSettingsPanel } from "../components/AdminSettingsPanel";
import { GateHuntPanel } from "../components/GateHuntPanel";
import { useActivityFeed } from "../hooks/useActivityFeed";
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
  const [view, setView] = useState<"board" | "all" | "arrange" | "evidence" | "canvas" | "manage">("board");
  const [notesPuzzleId, setNotesPuzzleId] = useState<number | null>(null);
  const [openGroups, setOpenGroups] = useState<string[] | null>(null);

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
        <SegmentedControl
          value={view}
          onChange={(v) => setView(v as "board" | "all" | "arrange" | "evidence" | "canvas" | "manage")}
          data={[
            { label: "By group", value: "board" },
            { label: "All puzzles", value: "all" },
            { label: "Evidence", value: "evidence" },
            { label: "Canvas", value: "canvas" },
            ...(data.hunt.can_manage ? [{ label: "Arrange", value: "arrange" }] : []),
            // Admins can always reach Manage (to unarchive) even when the hunt is
            // archived and can_manage is therefore false.
            ...(data.hunt.can_manage || data.hunt.can_administer ? [{ label: "Manage", value: "manage" }] : []),
          ]}
          // Hard-coded width: each entry needs its own allowance or the control
          // clips. "Canvas" adds 90 to the 360 base.
          w={450 + (data.hunt.can_manage ? 105 : 0) + (data.hunt.can_manage || data.hunt.can_administer ? 105 : 0)}
        />
        <Group gap="sm">
          {extensionPresent() && data.hunt.sheet_mode === "files" && (
            <Button variant="light" color="gray" leftSection={<IconBrowserX size={16} />} onClick={() => closeSolved()}>
              Close solved tabs
            </Button>
          )}
          {!data.hunt.archived && <CreateControls detail={data} uid={uid!} />}
        </Group>
      </Group>

      {view === "board" && (() => {
        // Bare groups (no puzzles) are drop targets for the Arrange view only —
        // hide them from the board summary so it isn't cluttered with empties.
        // Arrange keeps Unassigned Feeders far left; the By-group summary instead
        // places it below unsolved targets and plain groups but above solved
        // targets (which sink to the bottom).
        const withPuzzles = data.board.filter((g) => g.puzzles.length > 0);
        const unassigned = withPuzzles.filter((g) => g.tag_group_id === 0);
        const rest = withPuzzles.filter((g) => g.tag_group_id !== 0);
        const firstSolved = rest.findIndex((g) => g.target_solved);
        const at = firstSolved === -1 ? rest.length : firstSolved;
        const boardGroups = [...rest.slice(0, at), ...unassigned, ...rest.slice(at)];
        const defaultOpen = boardGroups.filter((g) => !g.target_solved).map((g) => String(g.tag_group_id));
        return (
          <Stack gap="xs">
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
                <BoardGroup key={g.tag_group_id} group={g} detail={data} live={live} onOpenNotes={openNotes} />
              ))}
            </Accordion>
          </Stack>
        );
      })()}
      {view === "all" && (
        <PuzzleTable detail={data} puzzles={allPuzzles} live={live} onOpenNotes={openNotes} />
      )}
      {view === "arrange" && data.hunt.can_manage && <DragBoard detail={data} uid={uid!} />}
      {view === "evidence" && <EvidencePanel uid={uid!} detail={data} />}
      {view === "canvas" && <CanvasPanel uid={uid!} detail={data} />}
      {view === "manage" && (data.hunt.can_manage || data.hunt.can_administer) && (
        <Stack gap="md">
          {/* Admin settings panel — admin-gated; reachable even when archived */}
          {data.hunt.can_administer && (
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
            />
          )}
          {/* Team-role gating — admin-gated; hidden on archived (read-only) hunts */}
          {data.hunt.can_administer && !data.hunt.archived && (
            <GateHuntPanel
              uid={uid!}
              isGated={data.hunt.is_gated}
              roleName={data.hunt.role_name}
            />
          )}
          {/* Reconcile stays organiser-gated and hidden on archived (read-only) hunts */}
          {data.hunt.can_manage && <ReconcilePanel uid={uid!} />}
          {/* Same organiser gate: the media sweep is a bulk delete. */}
          {data.hunt.can_manage && <MediaSweepPanel uid={uid!} />}
        </Stack>
      )}

      <PuzzleDrawer puzzle={notesPuzzle} detail={data} onClose={() => setNotesPuzzleId(null)} />
    </Stack>
  );
}

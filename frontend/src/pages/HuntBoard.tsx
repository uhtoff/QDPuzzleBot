import { useMemo, useState } from "react";
import {
  Accordion,
  Anchor,
  Badge,
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
import { IconArrowLeft } from "@tabler/icons-react";
import { useHunt } from "../api";
import { useQueryClient } from "@tanstack/react-query";
import { BoardGroup } from "../components/BoardGroup";
import { PuzzleTable } from "../components/PuzzleTable";
import { DragBoard } from "../components/DragBoard";
import { CreateControls } from "../components/CreateControls";
import { PuzzleDrawer } from "../components/PuzzleDrawer";
import { EvidencePanel } from "../components/EvidencePanel";
import { ReconcilePanel } from "../components/ReconcilePanel";
import { useActivityFeed } from "../hooks/useActivityFeed";
import type { Puzzle } from "../types";

export default function HuntBoard() {
  const { uid } = useParams();
  const { data, isLoading, error } = useHunt(uid);
  const qc = useQueryClient();
  const live = useActivityFeed((u) => { if (u === uid) qc.invalidateQueries({ queryKey: ["hunt", uid] }); });
  const [view, setView] = useState<"board" | "all" | "arrange" | "evidence" | "manage">("board");
  const [notesPuzzleId, setNotesPuzzleId] = useState<number | null>(null);

  // A flat, de-duplicated puzzle list for the "All puzzles" table.
  const allPuzzles = useMemo(() => {
    if (!data) return [];
    const seen = new Map<number, (typeof data.board)[number]["puzzles"][number]>();
    data.board.forEach((g) => g.puzzles.forEach((p) => seen.set(p.id, p)));
    return Array.from(seen.values());
  }, [data]);

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

      <Group justify="space-between" align="center">
        <SegmentedControl
          value={view}
          onChange={(v) => setView(v as "board" | "all" | "arrange" | "evidence" | "manage")}
          data={[
            { label: "By meta", value: "board" },
            { label: "All puzzles", value: "all" },
            { label: "Evidence", value: "evidence" },
            ...(data.hunt.can_manage ? [{ label: "Arrange", value: "arrange" }] : []),
            ...(data.hunt.can_manage ? [{ label: "Manage", value: "manage" }] : []),
          ]}
          w={data.hunt.can_manage ? 570 : 360}
        />
        {data.hunt.can_manage && <CreateControls detail={data} uid={uid!} />}
      </Group>

      {view === "board" && (
        <Accordion variant="separated" multiple defaultValue={data.board.map((g) => String(g.round_id))}>
          {data.board.map((g) => (
            <BoardGroup key={g.round_id} group={g} detail={data} live={live} onOpenNotes={openNotes} />
          ))}
        </Accordion>
      )}
      {view === "all" && (
        <PuzzleTable detail={data} puzzles={allPuzzles} live={live} onOpenNotes={openNotes} />
      )}
      {view === "arrange" && data.hunt.can_manage && <DragBoard detail={data} uid={uid!} />}
      {view === "evidence" && <EvidencePanel uid={uid!} detail={data} />}
      {view === "manage" && data.hunt.can_manage && <ReconcilePanel uid={uid!} />}

      <PuzzleDrawer puzzle={notesPuzzle} detail={data} onClose={() => setNotesPuzzleId(null)} />
    </Stack>
  );
}

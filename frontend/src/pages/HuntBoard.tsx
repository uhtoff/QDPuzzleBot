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
import { BoardGroup } from "../components/BoardGroup";
import { PuzzleTable } from "../components/PuzzleTable";
import { DragBoard } from "../components/DragBoard";
import { NotesDrawer } from "../components/NotesDrawer";
import { useActivityFeed } from "../hooks/useActivityFeed";
import type { Puzzle } from "../types";

export default function HuntBoard() {
  const { uid } = useParams();
  const { data, isLoading, error } = useHunt(uid);
  const live = useActivityFeed();
  const [view, setView] = useState<"board" | "all" | "arrange">("board");
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

      <SegmentedControl
        value={view}
        onChange={(v) => setView(v as "board" | "all" | "arrange")}
        data={[
          { label: "By meta", value: "board" },
          { label: "All puzzles", value: "all" },
          ...(data.hunt.can_manage ? [{ label: "Arrange", value: "arrange" }] : []),
        ]}
        w={data.hunt.can_manage ? 360 : 260}
      />

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

      <NotesDrawer puzzle={notesPuzzle} huntUid={uid!} onClose={() => setNotesPuzzleId(null)} />
    </Stack>
  );
}

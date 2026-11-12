import type { ReactNode } from "react";
import { useMemo } from "react";
import { Checkbox, Group, MultiSelect, Text, TextInput } from "@mantine/core";
import { IconSearch } from "@tabler/icons-react";
import type { Puzzle } from "../types";
import { formatPriority, formatStatus } from "../util";
import { uniqueValues, type PuzzleFilter } from "../lib/puzzleFilter";

interface Props {
  /** Every puzzle the view could show: the filter's options come from these. */
  puzzles: Puzzle[];
  filter: PuzzleFilter;
  onChange: (next: PuzzleFilter) => void;
  shown: number;
  total: number;
  /** Extra view-specific controls, placed before the count. */
  children?: ReactNode;
}

export function PuzzleFilterBar({ puzzles, filter, onChange, shown, total, children }: Props) {
  // Filter VALUES stay the stored strings (that is what the rows are matched
  // against); only the label is title-cased.
  const statusOptions = useMemo(
    () => uniqueValues(puzzles, "status").map((v) => ({ value: v, label: formatStatus(v) })),
    [puzzles],
  );
  const priorityOptions = useMemo(
    () => uniqueValues(puzzles, "priority").map((v) => ({ value: v, label: formatPriority(v) })),
    [puzzles],
  );
  const typeOptions = useMemo(() => uniqueValues(puzzles, "puzzle_type"), [puzzles]);
  const set = (patch: Partial<PuzzleFilter>) => onChange({ ...filter, ...patch });

  return (
    <Group mb="md" align="flex-end" wrap="wrap">
      <TextInput
        label="Search"
        placeholder="Name, solution, round…"
        leftSection={<IconSearch size={16} />}
        value={filter.search}
        onChange={(e) => set({ search: e.currentTarget.value })}
        w={240}
      />
      <MultiSelect
        label="Status"
        data={statusOptions}
        value={filter.statuses}
        onChange={(statuses) => set({ statuses })}
        clearable
        w={180}
      />
      <MultiSelect
        label="Priority"
        data={priorityOptions}
        value={filter.priorities}
        onChange={(priorities) => set({ priorities })}
        clearable
        w={150}
      />
      <MultiSelect
        label="Type"
        data={typeOptions}
        value={filter.types}
        onChange={(types) => set({ types })}
        clearable
        w={180}
      />
      <Checkbox
        label="Hide solved"
        checked={filter.hideSolved}
        onChange={(e) => set({ hideSolved: e.currentTarget.checked })}
        mb={8}
      />
      {children}
      <Text c="dimmed" size="sm" mb={8}>
        {shown} / {total}
      </Text>
    </Group>
  );
}

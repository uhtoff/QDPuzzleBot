import { Button, Drawer, Modal, ScrollArea, Stack, Text, TextInput } from "@mantine/core";
import { useMediaQuery } from "@mantine/hooks";
import { useState } from "react";
import type { EdgeType, RetagMode } from "../api";

interface GroupOption {
  code: string;
  name: string;
}

interface AddToGroupPickerProps {
  opened: boolean;
  onClose: () => void;
  puzzleId: number;
  /** Groups this puzzle can be added to — the already-computed `addable` list. */
  items: GroupOption[];
  /** group_code of the group this puzzle is the target of, if any. */
  ownTargetCode: string | null;
  selfFeeds: boolean;
  selfInforms: boolean;
  onRetag: (puzzleId: number, groupCode: string, mode: RetagMode, edge?: EdgeType) => void;
}

// One selectable row: a display label plus the retag action it runs.
interface Row {
  key: string;
  label: string;
  run: () => void;
}

// Expand the addable groups into concrete rows. A group the puzzle is the
// target of expands into up to two self-edge rows (feeds / informs), mirroring
// the original kebab markup; every other group is a single plain "add" row.
function buildRows(
  items: GroupOption[],
  puzzleId: number,
  ownTargetCode: string | null,
  selfFeeds: boolean,
  selfInforms: boolean,
  onRetag: AddToGroupPickerProps["onRetag"],
): Row[] {
  const rows: Row[] = [];
  for (const m of items) {
    if (m.code === ownTargetCode) {
      if (!selfFeeds) {
        rows.push({
          key: `${m.code}-feeds`,
          label: `${m.name} (feeds itself)`,
          run: () => onRetag(puzzleId, m.code, "add", "feeds"),
        });
      }
      if (!selfInforms) {
        rows.push({
          key: `${m.code}-informs`,
          label: `${m.name} (informs itself)`,
          run: () => onRetag(puzzleId, m.code, "add", "informs"),
        });
      }
    } else {
      rows.push({
        key: m.code,
        label: m.name,
        run: () => onRetag(puzzleId, m.code, "add"),
      });
    }
  }
  return rows;
}

export function AddToGroupPicker({
  opened,
  onClose,
  puzzleId,
  items,
  ownTargetCode,
  selfFeeds,
  selfInforms,
  onRetag,
}: AddToGroupPickerProps) {
  const isMobile = useMediaQuery("(max-width: 48em)");
  const [query, setQuery] = useState("");

  const rows = buildRows(items, puzzleId, ownTargetCode, selfFeeds, selfInforms, onRetag);
  const needle = query.trim().toLowerCase();
  const filtered = needle ? rows.filter((r) => r.label.toLowerCase().includes(needle)) : rows;

  const close = () => {
    setQuery("");
    onClose();
  };

  const choose = (r: Row) => {
    r.run();
    close();
  };

  const body = (
    <Stack gap="xs">
      <TextInput
        placeholder="Search groups…"
        value={query}
        onChange={(e) => setQuery(e.currentTarget.value)}
        autoFocus
        data-autofocus
      />
      <ScrollArea.Autosize mah={260}>
        <Stack gap={2}>
          {filtered.length === 0 ? (
            <Text size="sm" c="dimmed" p="xs">
              No groups match
            </Text>
          ) : (
            filtered.map((r) => (
              <Button
                key={r.key}
                variant="subtle"
                color="gray"
                fullWidth
                justify="flex-start"
                onClick={() => choose(r)}
              >
                {r.label}
              </Button>
            ))
          )}
        </Stack>
      </ScrollArea.Autosize>
    </Stack>
  );

  if (isMobile) {
    return (
      <Drawer opened={opened} onClose={close} position="bottom" title="Add to group" size="70%">
        {body}
      </Drawer>
    );
  }
  return (
    <Modal opened={opened} onClose={close} title="Add to group" size="sm">
      {body}
    </Modal>
  );
}

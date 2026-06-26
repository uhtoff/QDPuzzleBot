import { useMemo, useState } from "react";
import { Button, Group, Modal, Select, Stack, TextInput } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { IconPlus } from "@tabler/icons-react";
import type { HuntDetail } from "../types";
import { useCreateMeta, useCreatePuzzle, type MetaType } from "../api";
import { notifyError, notifySuccess } from "../util";

// Sentinel dropdown value for "no meta" — sent to the API as an empty group_code.
const UNASSIGNED = "__unassigned__";

// Meta/round types the create_meta route accepts (see TYPE_DEFAULTS in puzzle_service.py).
const META_TYPES: { value: MetaType; label: string }[] = [
  { value: "Round", label: "Round" },
  { value: "Metapuzzle", label: "Metapuzzle" },
  { value: "Capstone", label: "Capstone" },
  { value: "Runaround", label: "Runaround" },
  { value: "Group", label: "Group" },
];

export function CreateControls({ detail, uid }: { detail: HuntDetail; uid: string }) {
  const [puzzleOpen, puzzle] = useDisclosure(false);
  const [metaOpen, meta] = useDisclosure(false);

  const metaOptions = useMemo(
    () => [
      ...detail.board
        .filter((g) => g.group_code != null)
        .map((g) => ({ value: g.group_code as string, label: g.name })),
      { value: UNASSIGNED, label: "Unassigned (no meta)" },
    ],
    [detail.board]
  );

  const [puzzleName, setPuzzleName] = useState("");
  const [metaCode, setMetaCode] = useState<string | null>(metaOptions[0]?.value ?? UNASSIGNED);
  const [metaName, setMetaName] = useState("");
  const [metaType, setMetaType] = useState<MetaType>("Round");

  const createPuzzle = useCreatePuzzle(uid);
  const createMeta = useCreateMeta(uid);

  function submitPuzzle() {
    const name = puzzleName.trim();
    if (!name || metaCode == null) return;
    createPuzzle.mutate(
      { name, metaCode: metaCode === UNASSIGNED ? "" : metaCode },
      {
        onSuccess: () => {
          notifySuccess(`Created puzzle "${name}"`);
          setPuzzleName("");
          puzzle.close();
        },
        onError: notifyError,
      }
    );
  }

  function submitMeta() {
    const name = metaName.trim();
    if (!name) return;
    createMeta.mutate(
      { name, type: metaType },
      {
        onSuccess: () => {
          notifySuccess(`Created group "${name}"`);
          setMetaName("");
          meta.close();
        },
        onError: notifyError,
      }
    );
  }

  return (
    <>
      <Group gap="xs">
        <Button size="xs" variant="light" leftSection={<IconPlus size={14} />} onClick={puzzle.open}>
          New puzzle
        </Button>
        <Button size="xs" variant="light" color="grape" leftSection={<IconPlus size={14} />} onClick={meta.open}>
          New Group
        </Button>
      </Group>

      <Modal opened={puzzleOpen} onClose={puzzle.close} title="New puzzle" centered>
        <Stack>
          <TextInput
            label="Puzzle name"
            value={puzzleName}
            onChange={(e) => setPuzzleName(e.currentTarget.value)}
            data-autofocus
            onKeyDown={(e) => e.key === "Enter" && submitPuzzle()}
          />
          <Select
            label="Meta"
            description="Pick a meta, or “Unassigned” to create a hunt-level puzzle in the hunt category."
            data={metaOptions}
            value={metaCode}
            onChange={setMetaCode}
            allowDeselect={false}
          />
          <Button
            onClick={submitPuzzle}
            loading={createPuzzle.isPending}
            disabled={!puzzleName.trim() || metaCode == null}
          >
            Create
          </Button>
        </Stack>
      </Modal>

      <Modal opened={metaOpen} onClose={meta.close} title="New group" centered>
        <Stack>
          <TextInput
            label="Group name"
            description="Creates a new category with a channel and Google sheet."
            value={metaName}
            onChange={(e) => setMetaName(e.currentTarget.value)}
            data-autofocus
            onKeyDown={(e) => e.key === "Enter" && submitMeta()}
          />
          <Select
            label="Type"
            data={META_TYPES}
            value={metaType}
            onChange={(v) => setMetaType((v as MetaType) ?? "Round")}
            allowDeselect={false}
          />
          <Button color="grape" onClick={submitMeta} loading={createMeta.isPending} disabled={!metaName.trim()}>
            Create
          </Button>
        </Stack>
      </Modal>
    </>
  );
}

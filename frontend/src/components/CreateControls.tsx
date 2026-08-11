import { useMemo, useState } from "react";
import { Button, Group, LoadingOverlay, Modal, Select, Stack, TextInput } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { IconPlus } from "@tabler/icons-react";
import type { HuntDetail } from "../types";
import { useCreateMeta, useCreatePuzzle, type MetaType } from "../api";
import { notifyError, notifySuccess } from "../util";

// Sentinel dropdown value for "no meta" — sent to the API as an empty group_code.
const UNASSIGNED = "__unassigned__";

export function CreateControls({ detail, uid }: { detail: HuntDetail; uid: string }) {
  const [puzzleOpen, puzzle] = useDisclosure(false);
  const [metaOpen, meta] = useDisclosure(false);

  // Creating a group is organiser-only (POST /hunts/<uid>/metas returns 403
  // otherwise), so don't offer the control. Creating a puzzle stays open.
  const canManage = detail.hunt.can_manage;

  const metaOptions = useMemo(
    () => [
      ...detail.board
        .filter((g) => g.group_code != null)
        .map((g) => ({ value: g.group_code as string, label: g.name })),
      { value: UNASSIGNED, label: "Unassigned (no meta)" },
    ],
    [detail.board]
  );

  // Create-meta type labels come from the API (CREATE_META_TYPES); the
  // MetaType union is kept purely as a compile-time guard on the payload.
  const metaTypeOptions = useMemo(
    () => detail.meta_types.map((t) => ({ value: t, label: t })),
    [detail.meta_types]
  );

  const [puzzleName, setPuzzleName] = useState("");
  const [metaCode, setMetaCode] = useState<string | null>(metaOptions[0]?.value ?? UNASSIGNED);
  const [metaName, setMetaName] = useState("");
  const [metaType, setMetaType] = useState<MetaType>("Round");

  const createPuzzle = useCreatePuzzle(uid);
  const createMeta = useCreateMeta(uid);

  function submitPuzzle() {
    if (createPuzzle.isPending) return;   // block double-submit (e.g. a second Enter)
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
    if (createMeta.isPending) return;   // block double-submit (e.g. a second Enter)
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
        {canManage && (
          <Button size="xs" variant="light" color="grape" leftSection={<IconPlus size={14} />} onClick={meta.open}>
            New Group
          </Button>
        )}
      </Group>

      <Modal opened={puzzleOpen} onClose={puzzle.close} title="New puzzle" centered
             closeOnClickOutside={!createPuzzle.isPending} closeOnEscape={!createPuzzle.isPending}>
        <Stack pos="relative">
          <LoadingOverlay visible={createPuzzle.isPending} />
          <TextInput
            label="Puzzle name"
            value={puzzleName}
            onChange={(e) => setPuzzleName(e.currentTarget.value)}
            data-autofocus
            disabled={createPuzzle.isPending}
            onKeyDown={(e) => e.key === "Enter" && submitPuzzle()}
          />
          <Select
            label="Meta"
            description="Pick a meta, or “Unassigned” to create a hunt-level puzzle in the hunt category."
            data={metaOptions}
            value={metaCode}
            onChange={setMetaCode}
            allowDeselect={false}
            disabled={createPuzzle.isPending}
          />
          <Button
            onClick={submitPuzzle}
            loading={createPuzzle.isPending}
            disabled={createPuzzle.isPending || !puzzleName.trim() || metaCode == null}
          >
            Create
          </Button>
        </Stack>
      </Modal>

      <Modal opened={metaOpen} onClose={meta.close} title="New group" centered
             closeOnClickOutside={!createMeta.isPending} closeOnEscape={!createMeta.isPending}>
        <Stack pos="relative">
          <LoadingOverlay visible={createMeta.isPending} />
          <TextInput
            label="Group name"
            description="Creates a new category with a channel and Google sheet."
            value={metaName}
            onChange={(e) => setMetaName(e.currentTarget.value)}
            data-autofocus
            disabled={createMeta.isPending}
            onKeyDown={(e) => e.key === "Enter" && submitMeta()}
          />
          <Select
            label="Type"
            data={metaTypeOptions}
            value={metaType}
            onChange={(v) => setMetaType((v as MetaType) ?? "Round")}
            allowDeselect={false}
            disabled={createMeta.isPending}
          />
          <Button color="grape" onClick={submitMeta} loading={createMeta.isPending}
                  disabled={createMeta.isPending || !metaName.trim()}>
            Create
          </Button>
        </Stack>
      </Modal>
    </>
  );
}

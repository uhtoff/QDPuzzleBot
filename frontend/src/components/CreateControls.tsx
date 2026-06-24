import { useMemo, useState } from "react";
import { Button, Group, Modal, Select, Stack, TextInput } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { IconPlus } from "@tabler/icons-react";
import type { HuntDetail } from "../types";
import { useCreateMeta, useCreatePuzzle, useCreateUnclassified } from "../api";

function errMessage(e: unknown) {
  return (e as Error)?.message || "Something went wrong";
}

export function CreateControls({ detail, uid }: { detail: HuntDetail; uid: string }) {
  const [puzzleOpen, puzzle] = useDisclosure(false);
  const [metaOpen, meta] = useDisclosure(false);
  const [unclassifiedOpen, unclassified] = useDisclosure(false);

  const metaOptions = useMemo(
    () =>
      detail.board
        .filter((g) => g.meta_code != null)
        .map((g) => ({ value: g.meta_code as string, label: g.name })),
    [detail.board]
  );

  const [puzzleName, setPuzzleName] = useState("");
  const [metaCode, setMetaCode] = useState<string | null>(metaOptions[0]?.value ?? null);
  const [metaName, setMetaName] = useState("");
  const [unclassName, setUnclassName] = useState("");

  const createPuzzle = useCreatePuzzle(uid);
  const createMeta = useCreateMeta(uid);
  const createUnclassified = useCreateUnclassified(uid);

  function submitPuzzle() {
    const name = puzzleName.trim();
    if (!name || !metaCode) return;
    createPuzzle.mutate(
      { name, metaCode },
      {
        onSuccess: () => {
          notifications.show({ message: `Created puzzle “${name}”`, color: "green" });
          setPuzzleName("");
          puzzle.close();
        },
        onError: (e) => notifications.show({ message: errMessage(e), color: "red" }),
      }
    );
  }

  function submitMeta() {
    const name = metaName.trim();
    if (!name) return;
    createMeta.mutate(
      { name },
      {
        onSuccess: () => {
          notifications.show({ message: `Created meta “${name}”`, color: "green" });
          setMetaName("");
          meta.close();
        },
        onError: (e) => notifications.show({ message: errMessage(e), color: "red" }),
      }
    );
  }

  function submitUnclassified() {
    const name = unclassName.trim();
    if (!name) return;
    createUnclassified.mutate(
      { name },
      {
        onSuccess: () => {
          notifications.show({ message: `Created unclassified puzzle “${name}”`, color: "green" });
          setUnclassName("");
          unclassified.close();
        },
        onError: (e) => notifications.show({ message: errMessage(e), color: "red" }),
      }
    );
  }

  return (
    <>
      <Group gap="xs">
        <Button
          size="xs"
          variant="light"
          leftSection={<IconPlus size={14} />}
          onClick={puzzle.open}
          disabled={metaOptions.length === 0}
        >
          New puzzle
        </Button>
        <Button size="xs" variant="light" color="grape" leftSection={<IconPlus size={14} />} onClick={meta.open}>
          New meta
        </Button>
        <Button size="xs" variant="light" color="blue" leftSection={<IconPlus size={14} />} onClick={unclassified.open}>
          New unclassified
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
            data={metaOptions}
            value={metaCode}
            onChange={setMetaCode}
            allowDeselect={false}
          />
          <Button
            onClick={submitPuzzle}
            loading={createPuzzle.isPending}
            disabled={!puzzleName.trim() || !metaCode}
          >
            Create
          </Button>
        </Stack>
      </Modal>

      <Modal opened={metaOpen} onClose={meta.close} title="New meta (round)" centered>
        <Stack>
          <TextInput
            label="Meta name"
            description="Creates a new category with a metapuzzle channel and Google sheet."
            value={metaName}
            onChange={(e) => setMetaName(e.currentTarget.value)}
            data-autofocus
            onKeyDown={(e) => e.key === "Enter" && submitMeta()}
          />
          <Button color="grape" onClick={submitMeta} loading={createMeta.isPending} disabled={!metaName.trim()}>
            Create
          </Button>
        </Stack>
      </Modal>

      <Modal opened={unclassifiedOpen} onClose={unclassified.close} title="New unclassified puzzle" centered>
        <Stack>
          <TextInput
            label="Puzzle name"
            description="Creates a hunt-level puzzle not assigned to any meta round."
            value={unclassName}
            onChange={(e) => setUnclassName(e.currentTarget.value)}
            data-autofocus
            onKeyDown={(e) => e.key === "Enter" && submitUnclassified()}
          />
          <Button color="blue" onClick={submitUnclassified} loading={createUnclassified.isPending} disabled={!unclassName.trim()}>
            Create
          </Button>
        </Stack>
      </Modal>
    </>
  );
}

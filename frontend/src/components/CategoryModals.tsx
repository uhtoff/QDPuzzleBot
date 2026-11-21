import { useEffect, useState } from "react";
import { Alert, Button, Group, List, Loader, Modal, Select, Stack, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
  ApiError,
  useConsolidateCategory,
  useExtractCategory,
  useExtractCategoryPreview,
} from "../api";
import type { BoardGroup } from "../types";
import { notifyError } from "../util";

/**
 * Arrange-view dialogs that give a meta its own Discord category, or fold it
 * back into another group's category. Dashboard twins of
 * !extract_target_to_category and !consolidate_to; both say exactly what will
 * happen before anything moves. Mounted only while in use (see Lingering).
 */

function NameList({ items }: { items: { id: number; name: string }[] }) {
  return (
    <List size="sm" spacing={2} withPadding>
      {items.map((p) => (
        <List.Item key={p.id}>{p.name}</List.Item>
      ))}
    </List>
  );
}

export function ExtractCategoryModal({
  group,
  opened,
  uid,
  onClose,
}: {
  group: BoardGroup;
  opened: boolean;
  uid: string;
  onClose: () => void;
}) {
  const preview = useExtractCategoryPreview(group.tag_group_id, true);
  const extract = useExtractCategory(uid);
  const p = preview.data;
  const noSlot = p != null && p.free_category_slots <= 0;
  const blocked = p == null || p.own_category || noSlot;

  function confirm() {
    extract.mutate(group.tag_group_id, {
      onSuccess: (data) => {
        notifications.show({
          message: `Moved "${group.name}" into its own category "${data.category_name}".`,
          color: "green",
        });
        onClose();
      },
      onError: notifyError,
    });
  }

  return (
    <Modal opened={opened} onClose={onClose} title={`Give "${group.name}" its own category`} centered size="lg">
      {preview.isLoading ? (
        <Group justify="center" p="md">
          <Loader size="sm" />
        </Group>
      ) : preview.isError || p == null ? (
        <Alert color="red">
          {preview.error instanceof Error ? preview.error.message : "Couldn't load what would move."}
        </Alert>
      ) : (
        <Stack gap="sm">
          {p.own_category ? (
            <Alert color="blue">This group already has its own Discord category.</Alert>
          ) : (
            <>
              <Text size="sm">
                This creates a new Discord category named after <b>{group.name}</b>, with the same
                permissions as the hunt's own category, and moves these channels into it:
              </Text>
              <NameList items={p.moving} />
              {p.staying.length > 0 && (
                <>
                  <Text size="sm">
                    These stay where they are, because they also feed another group or are a
                    meta themselves:
                  </Text>
                  <NameList items={p.staying} />
                </>
              )}
              <Text size="sm">
                The group, its puzzles and its spreadsheets are unchanged — only where the channels
                sit in Discord. It uses one of the server's category slots (Discord allows 50 per
                server):{" "}
                <b>
                  {p.free_category_slots} free
                </b>
                . You can undo it later with <i>Fold into another category…</i>
              </Text>
              {noSlot && (
                <Alert color="red">
                  No category slots are free on this server, so this can't be done right now.
                </Alert>
              )}
            </>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button onClick={confirm} disabled={blocked} loading={extract.isPending}>
              Move to own category
            </Button>
          </Group>
        </Stack>
      )}
    </Modal>
  );
}

export function FoldCategoryModal({
  group,
  opened,
  groups,
  uid,
  onClose,
}: {
  group: BoardGroup;
  opened: boolean;
  /** Every board group; the destination choices are drawn from these. */
  groups: BoardGroup[];
  uid: string;
  onClose: () => void;
}) {
  const fold = useConsolidateCategory(uid);
  const [destination, setDestination] = useState<string | null>(null);
  // Set when the server says the destination would overflow; the button then
  // becomes "Fold anyway" and resends with force.
  const [overflowWarned, setOverflowWarned] = useState(false);
  useEffect(() => setOverflowWarned(false), [destination]);

  const options = groups
    .filter((g) => g.tag_group_id !== group.tag_group_id && g.tag_group_id !== 0 && g.group_code)
    .map((g) => ({ value: String(g.tag_group_id), label: g.name }));
  const destName = options.find((o) => o.value === destination)?.label;

  function confirm() {
    if (destination == null) return;
    fold.mutate(
      {
        tagGroupId: group.tag_group_id,
        destinationTagGroupId: Number(destination),
        force: overflowWarned,
      },
      {
        onSuccess: (data) => {
          notifications.show({
            message: `Folded "${group.name}" into "${data.destination_name}"'s category.`,
            color: "green",
          });
          onClose();
        },
        onError: (e) => {
          if (e instanceof ApiError && e.slug === "would_overflow") {
            setOverflowWarned(true);
            return;
          }
          notifyError(e);
        },
      },
    );
  }

  return (
    <Modal opened={opened} onClose={onClose} title={`Fold "${group.name}" into another category`} centered size="lg">
      <Stack gap="sm">
        <Text size="sm">
          This deletes <b>{group.name}</b>'s own Discord category and moves all of its channels
          into the category of the group you pick. The group itself, its puzzles and its
          spreadsheets are unchanged — only where the channels sit in Discord. It frees one of the
          server's category slots.
        </Text>
        <Select
          label="Fold into"
          placeholder="Pick a group"
          data={options}
          value={destination}
          onChange={setDestination}
          searchable
          nothingFoundMessage="No other groups"
          comboboxProps={{ withinPortal: true }}
        />
        {overflowWarned && destName && (
          <Alert color="orange">
            <b>{destName}</b>'s category would go over its size limit, so it will split into an
            extra category — that uses a category slot and reduces the saving. Fold anyway?
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button
            color={overflowWarned ? "orange" : "red"}
            onClick={confirm}
            disabled={destination == null}
            loading={fold.isPending}
          >
            {overflowWarned ? "Fold anyway" : "Fold into category"}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

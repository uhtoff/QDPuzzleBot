import { Group, Radio, Text } from "@mantine/core";
import { TypedConfirmModal } from "./TypedConfirmModal";

/**
 * The type-to-confirm modal for deleting a TagGroup, with the keep-or-delete
 * puzzles choice and the consequence warning for each branch.
 *
 * Shared because the board and the drag board each had their own copy and the
 * warning copy had already drifted apart -- the drag board's "keep" branch had
 * lost the detail about targetless groups and about where the surviving
 * channels go, which is exactly the text a user needs before confirming
 * something irreversible.
 *
 * `noun` is the presentation label the calling surface uses for a TagGroup
 * ("round" on the board, "group" on the drag board). It is a label only: both
 * delete the same thing through the same endpoint.
 */
export function DeleteRoundModal({
  opened,
  onClose,
  onConfirm,
  name,
  noun,
  puzzleCount,
  loading = false,
  deleteAllPuzzles,
  onDeleteAllPuzzlesChange,
}: {
  opened: boolean;
  onClose: () => void;
  onConfirm: () => void;
  name: string;
  noun: "round" | "group";
  puzzleCount: number;
  loading?: boolean;
  deleteAllPuzzles: boolean;
  onDeleteAllPuzzlesChange: (value: boolean) => void;
}) {
  const Noun = noun[0].toUpperCase() + noun.slice(1);
  const plural = puzzleCount === 1 ? "" : "s";

  return (
    <TypedConfirmModal
      opened={opened}
      onClose={onClose}
      onConfirm={onConfirm}
      title={`Delete ${noun} "${name}"`}
      confirmPhrase={name}
      confirmLabel={`Delete ${noun}`}
      loading={loading}
    >
      <Radio.Group
        value={deleteAllPuzzles ? "all" : "keep"}
        onChange={(v) => onDeleteAllPuzzlesChange(v === "all")}
        label={`What should happen to this ${noun}'s puzzles?`}
      >
        <Group mt="xs">
          <Radio value="keep" label={`${Noun} only — keep its puzzles`} />
          <Radio value="all" label={`${Noun} + all ${puzzleCount} puzzle${plural}`} />
        </Group>
      </Radio.Group>
      {deleteAllPuzzles ? (
        <Text size="sm" c="red" mt="xs">
          ⚠️ This permanently deletes <b>{puzzleCount} puzzle{plural}</b>, their Discord
          channels and Google sheet tabs, and the {noun}&rsquo;s Discord category. Feeders
          shared with another {noun}, and any puzzle that is another {noun}&rsquo;s target,
          are kept and moved to the hunt&rsquo;s general category. This cannot be undone.
        </Text>
      ) : (
        <Text size="sm" c="red" mt="xs">
          ⚠️ This permanently deletes the {noun}&rsquo;s target puzzle (or dedicated chat
          channel, if it has no target) and its Discord category. Every other puzzle in this
          {" "}{noun} keeps its sheet and Discord channel — those channels move into the
          hunt&rsquo;s general category. This cannot be undone.
        </Text>
      )}
    </TypedConfirmModal>
  );
}

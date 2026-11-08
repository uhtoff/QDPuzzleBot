import { Anchor, Button, Group, Text } from "@mantine/core";
import { IconHeadphones } from "@tabler/icons-react";

interface Props {
  voiceUrl: string | null;
  count: number;
  solved: boolean;
  archived: boolean;
  pending: boolean;
  onCreate: () => void;
}

/** Drawer control: Join the puzzle's voice channel, or create one. */
export function PuzzleVoiceControl({ voiceUrl, count, solved, archived, pending, onCreate }: Props) {
  if (voiceUrl) {
    return (
      <Group gap="xs">
        <Anchor href={voiceUrl} target="_blank" rel="noreferrer" size="sm">
          <Group gap={4} wrap="nowrap">
            <IconHeadphones size={16} />
            Join voice
          </Group>
        </Anchor>
        {count > 0 && <Text size="sm" c="teal">{count} in voice</Text>}
      </Group>
    );
  }
  if (solved || archived) return null;
  return (
    <Button
      variant="light"
      color="teal"
      leftSection={<IconHeadphones size={16} />}
      loading={pending}
      onClick={onCreate}
    >
      Create voice channel
    </Button>
  );
}

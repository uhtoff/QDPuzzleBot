import { Badge, Tooltip } from "@mantine/core";
import { IconHeadphones } from "@tabler/icons-react";

/** Teal headphones + head-count; renders nothing when nobody is in voice. */
export function VoiceBadge({ count }: { count: number }) {
  if (count <= 0) return null;
  const label = `In voice: ${count}`;
  return (
    <Tooltip label={label}>
      <Badge
        color="indigo"
        variant="light"
        leftSection={<IconHeadphones size={12} />}
        style={{ flexShrink: 0 }}
        aria-label={label}
      >
        {count}
      </Badge>
    </Tooltip>
  );
}

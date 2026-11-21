import { memo } from "react";
import { Badge } from "@mantine/core";
import { IconHeadphones } from "@tabler/icons-react";

/** Teal headphones + head-count; renders nothing when nobody is in voice. */
export const VoiceBadge = memo(function VoiceBadge({ count }: { count: number }) {
  if (count <= 0) return null;
  const label = `In voice: ${count}`;
  return (
    <Badge
      color="indigo"
      variant="light"
      leftSection={<IconHeadphones size={12} />}
      style={{ flexShrink: 0 }}
      data-tip={label}
      aria-label={label}
    >
      {count}
    </Badge>
  );
});

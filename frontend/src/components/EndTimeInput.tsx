import { Checkbox, Group, Select, Stack, TextInput } from "@mantine/core";
import { UTC_OFFSET_OPTIONS } from "../util";

interface EndTimeInputProps {
  /** True = the hunt has no end date (an ongoing hunt); the date fields hide. */
  noEnd: boolean;
  onNoEndChange: (noEnd: boolean) => void;
  /** datetime-local wall-clock value, expressed in offsetMinutes. */
  local: string;
  onLocalChange: (local: string) => void;
  offsetMinutes: number;
  onOffsetChange: (offsetMinutes: number) => void;
  error?: string | null;
  selectWidth?: number;
}

/** The hunt end-time field: a "No end date" switch-off, then the same
 *  datetime + timezone pair the start time uses. */
export function EndTimeInput({
  noEnd,
  onNoEndChange,
  local,
  onLocalChange,
  offsetMinutes,
  onOffsetChange,
  error,
  selectWidth = 140,
}: EndTimeInputProps) {
  return (
    <Stack gap={6}>
      <Checkbox
        label="No end date"
        description="For hunts that just carry on."
        checked={noEnd}
        onChange={(e) => onNoEndChange(e.currentTarget.checked)}
      />
      {!noEnd && (
        <Group align="flex-start" gap="sm" wrap="nowrap">
          <TextInput
            type="datetime-local"
            label="End time"
            value={local}
            onChange={(e) => onLocalChange(e.currentTarget.value)}
            error={error}
            required
            style={{ flex: 1 }}
          />
          <Select
            label="Timezone"
            data={UTC_OFFSET_OPTIONS}
            value={String(offsetMinutes)}
            onChange={(val) => val != null && onOffsetChange(Number(val))}
            allowDeselect={false}
            w={selectWidth}
          />
        </Group>
      )}
    </Stack>
  );
}

/** Why an end time can't be used, or null when it's fine. A null endTs with
 *  noEnd off means the date is still blank. */
export function endTimeError(
  noEnd: boolean,
  endTs: number | null,
  startTs: number | null,
): string | null {
  if (noEnd) return null;
  if (endTs == null) return "Pick an end time, or tick No end date.";
  if (startTs != null && startTs > 0 && endTs <= startTs) return "The end must be after the start.";
  return null;
}

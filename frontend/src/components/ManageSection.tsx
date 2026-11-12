import type { ReactNode } from "react";
import { useState } from "react";
import { Collapse, Group, Paper, Stack, Text, Title, UnstyledButton } from "@mantine/core";
import { IconChevronDown, IconChevronRight } from "@tabler/icons-react";

/** One card on the Manage view. Every section uses this, so headings, spacing
 *  and borders match; `danger` is the red one, `collapsible` starts closed. */
export function ManageSection({
  title,
  danger = false,
  collapsible = false,
  children,
}: {
  title: string;
  danger?: boolean;
  collapsible?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(!collapsible);
  const heading = (
    <Title order={5} c={danger ? "red" : undefined}>
      {title}
    </Title>
  );
  return (
    <Paper
      withBorder
      p="md"
      radius="md"
      style={danger ? { borderColor: "var(--mantine-color-red-5)" } : undefined}
    >
      {collapsible ? (
        <UnstyledButton onClick={() => setOpen((o) => !o)} aria-expanded={open} w="100%">
          <Group gap={6}>
            {open ? <IconChevronDown size={16} /> : <IconChevronRight size={16} />}
            {heading}
          </Group>
        </UnstyledButton>
      ) : (
        heading
      )}
      <Collapse in={open}>
        <Stack gap="md" mt="sm">
          {children}
        </Stack>
      </Collapse>
    </Paper>
  );
}

/** A labelled item inside a section: a sub-heading, a one-line explanation,
 *  and whatever controls or results follow. */
export function ManageItem({ title, description, children }: { title: string; description?: ReactNode; children?: ReactNode }) {
  return (
    <Stack gap={6}>
      <div>
        <Text size="sm" fw={600}>
          {title}
        </Text>
        {description && (
          <Text size="xs" c="dimmed">
            {description}
          </Text>
        )}
      </div>
      {children}
    </Stack>
  );
}

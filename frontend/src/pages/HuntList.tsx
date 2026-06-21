import {
  Accordion,
  Badge,
  Card,
  Center,
  Group,
  Loader,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { Link } from "react-router-dom";
import { useHunts } from "../api";
import type { HuntSummary } from "../types";

function HuntCard({ hunt }: { hunt: HuntSummary }) {
  return (
    <Card
      component={Link}
      to={`/hunts/${hunt.uid}`}
      shadow="sm"
      padding="lg"
      radius="md"
      withBorder
    >
      <Group justify="space-between" wrap="nowrap">
        <Text fw={600} truncate>
          {hunt.name}
        </Text>
        {hunt.archived && <Badge color="gray" variant="light">Archived</Badge>}
      </Group>
      {hunt.guild_name && (
        <Text c="dimmed" size="sm" mt={4}>
          {hunt.guild_name}
        </Text>
      )}
    </Card>
  );
}

export default function HuntList() {
  const { data, isLoading, error } = useHunts();

  if (isLoading) {
    return (
      <Center h={200}>
        <Loader />
      </Center>
    );
  }
  if (error || !data) {
    return <Text c="red">Failed to load hunts.</Text>;
  }

  return (
    <Stack gap="xl">
      <div>
        <Title order={3} mb="md">
          Active hunts
        </Title>
        {data.active.length === 0 ? (
          <Text c="dimmed">No active hunts for your team right now.</Text>
        ) : (
          <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }}>
            {data.active.map((h) => (
              <HuntCard key={h.uid} hunt={h} />
            ))}
          </SimpleGrid>
        )}
      </div>

      {data.archived.length > 0 && (
        <Accordion variant="separated">
          <Accordion.Item value="old">
            <Accordion.Control>
              <Group gap="xs">
                <Text fw={600}>Old hunts</Text>
                <Badge variant="light" color="gray">
                  {data.archived.length}
                </Badge>
              </Group>
            </Accordion.Control>
            <Accordion.Panel>
              <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }}>
                {data.archived.map((h) => (
                  <HuntCard key={h.uid} hunt={h} />
                ))}
              </SimpleGrid>
            </Accordion.Panel>
          </Accordion.Item>
        </Accordion>
      )}
    </Stack>
  );
}

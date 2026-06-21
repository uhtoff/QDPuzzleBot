import { Button, Card, Center, Stack, Text, Title } from "@mantine/core";
import { IconBrandDiscord } from "@tabler/icons-react";
import { loginUrl } from "../api";

export default function Login() {
  return (
    <Center h="100vh">
      <Card shadow="md" padding="xl" radius="md" withBorder w={360}>
        <Stack align="center" gap="md">
          <Title order={2}>Puzzle Dashboard</Title>
          <Text c="dimmed" ta="center" size="sm">
            Sign in with Discord to view your team's hunts.
          </Text>
          <Button
            component="a"
            href={loginUrl()}
            leftSection={<IconBrandDiscord size={18} />}
            color="indigo"
            fullWidth
          >
            Log in with Discord
          </Button>
        </Stack>
      </Card>
    </Center>
  );
}

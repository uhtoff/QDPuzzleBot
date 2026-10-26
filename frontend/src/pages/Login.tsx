import { useEffect } from "react";
import { Alert, Badge, Button, Card, Center, Group, Stack, Text, Title } from "@mantine/core";
import { IconBrandDiscord, IconFlask } from "@tabler/icons-react";
import { loginUrl, useSiteInfo } from "../api";
import { DEFAULT_SITE, pageTitle } from "../lib/siteInfo";

/** The logged-out landing page. It says what the site is, who it's for and what
 *  "Log in with Discord" actually does: a bare unexplained Discord login card on
 *  an unfamiliar domain is exactly what phishing detectors flag. */
export default function Login() {
  const { data } = useSiteInfo();
  const site = data ?? DEFAULT_SITE;

  useEffect(() => {
    document.title = pageTitle(site);
  }, [site]);

  return (
    <Center mih="100vh" p="md">
      <Card shadow="md" padding="xl" radius="md" withBorder maw={460} w="100%">
        <Stack gap="md">
          <Group justify="center" gap="xs">
            <Title order={2}>{site.name || DEFAULT_SITE.name}</Title>
            {site.test_instance && (
              <Badge color="orange" variant="light">
                Test instance
              </Badge>
            )}
          </Group>
          <Text ta="center" size="sm">
            A private dashboard for puzzle-hunt teams. It works alongside the team's
            Puzzbot Discord bot to keep track of a hunt's puzzles, rounds and answers.
          </Text>
          {site.test_instance && (
            <Alert color="orange" variant="light" icon={<IconFlask size={18} />}>
              This is the test instance, where new features are tried out before they
              reach the live team site. Expect things to change or break.
            </Alert>
          )}
          <Button
            component="a"
            href={loginUrl()}
            leftSection={<IconBrandDiscord size={18} />}
            color="indigo"
            fullWidth
          >
            Log in with Discord
          </Button>
          <Text c="dimmed" ta="center" size="xs">
            The button takes you to discord.com to sign in. We never see your Discord
            password: Discord only tells us who you are, so we can show the hunts your
            team can see.
          </Text>
          {site.contact && (
            <Text c="dimmed" ta="center" size="xs">
              Questions about this site? Contact {site.contact}.
            </Text>
          )}
        </Stack>
      </Card>
    </Center>
  );
}

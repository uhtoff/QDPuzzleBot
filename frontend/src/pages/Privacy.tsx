import { useEffect } from "react";
import { Anchor, Container, List, Stack, Text, Title } from "@mantine/core";
import { useSiteInfo } from "../api";
import { DEFAULT_SITE, type SiteInfo } from "../lib/siteInfo";

/** The public privacy policy at /privacy. Reachable without login (App.tsx
 *  routes it before the auth gate); the site name and contact come from
 *  /api/auth/site. */
export default function Privacy() {
  const { data } = useSiteInfo();
  const site = data ?? DEFAULT_SITE;

  useEffect(() => {
    document.title = `Privacy — ${site.name || DEFAULT_SITE.name}`;
  }, [site]);

  return <PrivacyPolicy site={site} />;
}

export function PrivacyPolicy({ site }: { site: SiteInfo }) {
  const name = site.name || DEFAULT_SITE.name;
  return (
    <Container size="sm" py="xl">
      <Stack gap="md">
        <Title order={1}>Privacy policy</Title>
        <Text c="dimmed" size="sm">
          Last updated: 7 November 2026
        </Text>

        <Title order={3}>Who runs this</Title>
        <Text>
          {name}
          {site.contact ? (
            <>
              , contact {site.contact}.
            </>
          ) : (
            "."
          )}
        </Text>

        <Title order={3}>Dashboard sign-in</Title>
        <Text>
          You sign in with Discord OAuth. We receive your Discord user id, username, display
          name and avatar, and use your server memberships and roles to decide which hunts you
          can see. A session cookie keeps you signed in.
        </Text>

        <Title order={3}>Puzzle channels</Title>
        <Text>
          If the message bridge is enabled, messages posted in puzzle channels (author name and
          avatar, text, embeds and attachments) are stored on the server so the dashboard can
          show chat and keep a hunt archive. Attachments are downloaded to the server.
        </Text>

        <Title order={3}>Chrome extension (Puzzle Chat)</Title>
        <List spacing="xs">
          <List.Item>
            It reads the address of Google Sheets tabs only (docs.google.com) and sends each
            sheet&apos;s id to this dashboard to find the matching puzzle.
          </List.Item>
          <List.Item>It shows that puzzle&apos;s chat.</List.Item>
          <List.Item>
            Replies you send are posted to Discord by the bot under your server name and avatar.
          </List.Item>
          <List.Item>
            It stores its settings (dashboard address, notifications on or off) in Chrome sync
            storage, and its working state (which tab is which puzzle, unread counts) in Chrome
            session storage, which is cleared when the browser closes.
          </List.Item>
          <List.Item>It loads avatars and custom emoji images from Discord&apos;s CDN.</List.Item>
          <List.Item>
            It does not read other sites, the page contents of Sheets, or your browsing history.
          </List.Item>
        </List>

        <Title order={3}>What we don&apos;t do</Title>
        <Text>
          We do not sell or share data, show advertising, or use analytics or tracking.
        </Text>

        <Title order={3}>Retention and deletion</Title>
        <Text>
          Chat and archive data are kept for the life of the hunt archive. Server logs, which
          include requested addresses, are kept for [RETENTION PERIOD — operator to confirm].
          To request deletion or a copy of your data, contact{" "}
          {site.contact || "the operator of this site"}.
        </Text>

        <Title order={3}>Changes</Title>
        <Text>This page will be updated as things change; the date at the top shows when.</Text>

        <Anchor href="/">Back to {name}</Anchor>
      </Stack>
    </Container>
  );
}

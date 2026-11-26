import { Alert, Text } from "@mantine/core";
import { IconPuzzle } from "@tabler/icons-react";
import { useEffect, useState } from "react";

import {
  extensionUpdateNotice,
  installedExtensionVersion,
  type UpdateNotice,
  type VersionsInfo,
} from "../lib/extensionVersion";

// Shown when the installed Puzzle Chat copy is older than the newest released one,
// once per release: dismissing it hides it until versions.json names a newer one.
const DISMISSED_KEY = "puzzbot.dismissedExtensionUpdate";

const readDismissed = (): string | null => {
  try {
    return localStorage.getItem(DISMISSED_KEY);
  } catch {
    return null;
  }
};

export function ExtensionUpdateNotice() {
  const [notice, setNotice] = useState<UpdateNotice | null>(null);

  useEffect(() => {
    const installed = installedExtensionVersion();
    if (!installed) return;
    let live = true;
    fetch("/extension/versions.json", { cache: "no-store" })
      .then((r) => (r.ok ? (r.json() as Promise<VersionsInfo>) : null))
      .then((info) => live && setNotice(extensionUpdateNotice(installed, info, readDismissed())))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);

  if (!notice) return null;
  const firefox = typeof navigator !== "undefined" && navigator.userAgent.includes("Firefox");
  const dismiss = () => {
    try {
      localStorage.setItem(DISMISSED_KEY, notice.latest);
    } catch {
      /* private window: it just comes back next visit */
    }
    setNotice(null);
  };
  return (
    <Alert
      variant="light"
      color="indigo"
      icon={<IconPuzzle size={16} />}
      title={`Puzzle Chat ${notice.latest} is available`}
      withCloseButton
      closeButtonLabel="Dismiss until the next release"
      onClose={dismiss}
    >
      {notice.adds.map((v) => (
        <Text key={v.version} size="sm">
          <b>{v.version}</b>: {v.adds}
        </Text>
      ))}
      <Text size="xs" c="dimmed" mt={4}>
        {firefox
          ? "Firefox updates add-ons about once a day. To update now: about:addons → ⚙ → Check for Updates."
          : "Chrome updates extensions within a few hours. To update now: chrome://extensions → Developer mode → Update."}
      </Text>
    </Alert>
  );
}

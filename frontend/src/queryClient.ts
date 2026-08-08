import { QueryClient } from "@tanstack/react-query";

/**
 * The app's single QueryClient, in its own module so non-React code can reach
 * it. api.ts needs it to drop the cached ["me"] user the moment a request comes
 * back 401 — see onAuthLost.
 */
export const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false } },
});

/**
 * A request was refused as unauthenticated mid-session (the session expired, or
 * was revoked). Clearing ["me"] is what makes the app notice: App gates on
 * me.data, and while a stale user sat in the cache it kept rendering the
 * dashboard, so the user was stranded on a page whose every action failed with
 * a toast and no way to sign back in.
 */
export function onAuthLost(): void {
  queryClient.setQueryData(["me"], null);
}

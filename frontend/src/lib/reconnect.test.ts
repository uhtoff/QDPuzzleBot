import { describe, expect, it } from "vitest";
import { RECONNECT_MAX_MS, reconnectDelay } from "./reconnect";

describe("reconnectDelay", () => {
  it("gives up on an auth close rather than retrying forever", () => {
    // The socket closed 1008 because the session is gone / not a member / the
    // handshake was cross-site. Retrying cannot change any of those, and the
    // old fixed 3s retry meant a logged-out tab hammered the endpoint until it
    // was closed.
    expect(reconnectDelay(1008, 0)).toBeNull();
    expect(reconnectDelay(1008, 5)).toBeNull();
  });

  it("retries a transient close", () => {
    // 1006 is the abnormal close a dropped connection produces -- exactly the
    // case reconnecting exists for.
    expect(reconnectDelay(1006, 0)).toBe(3000);
  });

  it("backs off instead of retrying at a fixed interval", () => {
    // Two attempts are the minimum that can tell backoff from a constant: a
    // single delay is equally consistent with the old fixed 3000ms.
    const delays = [0, 1, 2, 3].map((n) => reconnectDelay(1006, n));
    expect(delays).toEqual([3000, 6000, 12000, 24000]);
  });

  it("caps the backoff so a long outage still retries", () => {
    expect(reconnectDelay(1006, 20)).toBe(RECONNECT_MAX_MS);
  });
});

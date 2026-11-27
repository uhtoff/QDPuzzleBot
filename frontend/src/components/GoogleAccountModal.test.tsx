import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import { GoogleAccountContent } from "./GoogleAccountModal";

describe("GoogleAccountContent", () => {
  it("renders optional linking copy and link CTA when unlinked", () => {
    const html = renderToStaticMarkup(
      <MantineProvider>
        <GoogleAccountContent
          account={null}
          onUnlink={() => {}}
          unlinking={false}
          linkHref="/api/auth/google/link?next=%2Fhunts%2FABC"
        />
      </MantineProvider>,
    );
    expect(html).toContain("Anonymous Animal");
    expect(html).toContain("A Google account is not required to participate");
    expect(html).toContain("Link with Google");
    expect(html).toContain("/api/auth/google/link?next=%2Fhunts%2FABC");
  });

  it("renders linked email, switch and unlink actions when linked", () => {
    const html = renderToStaticMarkup(
      <MantineProvider>
        <GoogleAccountContent
          account={{ email: "alice@gmail.com", linked_at: "2026-11-25T10:00:00+00:00" }}
          onUnlink={() => {}}
          unlinking={false}
          linkHref="/api/auth/google/link"
        />
      </MantineProvider>,
    );
    expect(html).toContain("alice@gmail.com");
    expect(html).toContain("Linked");
    expect(html).toContain("Switch account");
    expect(html).toContain("Unlink");
  });
});

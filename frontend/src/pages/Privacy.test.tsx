import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import { PrivacyPolicy } from "./Privacy";
import { appRouteAvailableWithoutAuth } from "../App";

const render = (site: { name: string; contact: string; test_instance: boolean }) =>
  renderToStaticMarkup(
    <MantineProvider>
      <PrivacyPolicy site={site} />
    </MantineProvider>,
  );

describe("PrivacyPolicy", () => {
  it("shows the site name and contact from the site info", () => {
    const html = render({ name: "QD Puzzbot", contact: "ops@example.test", test_instance: false });
    expect(html).toContain("QD Puzzbot");
    expect(html).toContain("ops@example.test");
    expect(html).toContain("Last updated: 7 November 2026");
    expect(html).not.toContain("RETENTION PERIOD");
    expect(html).toContain("deleted once they are 30 days old");
    expect(html).toContain("web-server logs follow the same 30-day rule");
    expect(html).toContain("every puzzle channel you can see on the dashboard");
    expect(html).toContain("only unread counts are kept in Chrome session storage");
    expect(html).toContain("kept with the hunt");
    expect(html).toContain("Chrome local storage");
    expect(html).toContain("only when you click the dashboard&#x27;s buttons");
  });

  it("falls back to a neutral name when the site info is empty", () => {
    const html = render({ name: "", contact: "", test_instance: false });
    expect(html).toContain("Puzzbot");
    expect(html).toContain("the operator of this site");
  });
});

describe("privacy route", () => {
  it("is reachable without auth, and ordinary paths are not", () => {
    expect(appRouteAvailableWithoutAuth("/privacy")).toBe(true);
    expect(appRouteAvailableWithoutAuth("/privacy/")).toBe(true);
    expect(appRouteAvailableWithoutAuth("/")).toBe(false);
    expect(appRouteAvailableWithoutAuth("/hunts/ABC")).toBe(false);
  });
});

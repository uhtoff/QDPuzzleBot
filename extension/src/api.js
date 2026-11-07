import { DEFAULT_ORIGIN, parseOrigin } from "./lib/origin.js";

export async function getOrigin() {
  try {
    const { dashboardOrigin } = await chrome.storage.sync.get({ dashboardOrigin: DEFAULT_ORIGIN });
    return parseOrigin(dashboardOrigin) ?? DEFAULT_ORIGIN;
  } catch {
    return DEFAULT_ORIGIN;
  }
}

async function request(path, method, body) {
  const origin = await getOrigin();
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") {
    const c = await chrome.cookies.get({ url: origin, name: "csrf_token" });
    if (c) headers["X-CSRF-Token"] = c.value;
  }
  let res;
  try {
    res = await fetch(origin + path, {
      method,
      credentials: "include",
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    return { error: String(e && e.message ? e.message : e), status: 0 };
  }
  if (res.status === 401) return { authRequired: true };
  let json = null;
  try {
    json = await res.json();
  } catch {
    /* non-JSON body */
  }
  if (!res.ok) return { error: (json && json.error) || String(res.status), status: res.status };
  return json ?? {};
}

export const apiGet = (path) => request(path, "GET");
export const apiSend = (path, method, body) => request(path, method, body);

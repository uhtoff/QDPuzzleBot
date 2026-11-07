import { DEFAULT_ORIGIN, parseOrigin } from "./lib/origin.js";
import { CSRF_PATH, classifyResponse } from "./lib/csrf.js";

export async function getOrigin() {
  try {
    const { dashboardOrigin } = await chrome.storage.sync.get({ dashboardOrigin: DEFAULT_ORIGIN });
    return parseOrigin(dashboardOrigin) ?? DEFAULT_ORIGIN;
  } catch {
    return DEFAULT_ORIGIN;
  }
}

// CSRF token for non-GET requests: fetched from the dashboard (the cookie is not
// readable without the "cookies" permission), cached per origin in memory.
const AUTH = Symbol("auth");
let csrf = { origin: null, token: null };

async function csrfToken(origin, refresh) {
  if (!refresh && csrf.origin === origin && csrf.token) return csrf.token;
  try {
    const res = await fetch(origin + CSRF_PATH, { credentials: "include" });
    if (classifyResponse({ method: "GET", status: res.status }) === "auth") return AUTH;
    const json = res.ok ? await res.json() : null;
    csrf = { origin, token: (json && json.csrf) || null };
  } catch {
    csrf = { origin: null, token: null };
  }
  return csrf.token;
}

async function request(path, method, body, retried = false) {
  const origin = await getOrigin();
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") {
    const token = await csrfToken(origin, retried);
    if (token === AUTH) return { authRequired: true };
    if (token) headers["X-CSRF-Token"] = token;
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
  let json = null;
  try {
    json = await res.json();
  } catch {
    /* non-JSON body */
  }
  const outcome = classifyResponse({ method, status: res.status, error: json && json.error, retried });
  if (outcome === "auth") return { authRequired: true };
  if (outcome === "retry") return request(path, method, body, true);
  if (!res.ok) return { error: (json && json.error) || String(res.status), status: res.status };
  return json ?? {};
}

export const apiGet = (path) => request(path, "GET");
export const apiSend = (path, method, body) => request(path, method, body);

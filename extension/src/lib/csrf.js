export const CSRF_PATH = "/api/extension/csrf";

// What to do with a response: "auth" (logged out; never retry, the CSRF guard may
// answer a logged-out POST with 403 csrf, so the token fetch's own 401 decides),
// "retry" (csrf refused: refetch token, once), "error", or "ok".
export function classifyResponse({ method, status, error, retried }) {
  if (status === 401) return "auth";
  if (shouldRetryCsrf({ method, status, error, retried })) return "retry";
  return status >= 200 && status < 300 ? "ok" : "error";
}

// Retry a request once with a fresh token iff it was a state-changing request
// refused with {"error": "csrf"} (403) and we have not already retried.
export function shouldRetryCsrf({ method, status, error, retried }) {
  return !retried && method !== "GET" && status === 403 && error === "csrf";
}

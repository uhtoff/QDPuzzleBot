export const CSRF_PATH = "/api/extension/csrf";

// Retry a request once with a fresh token iff it was a state-changing request
// refused with {"error": "csrf"} (403) and we have not already retried.
export function shouldRetryCsrf({ method, status, error, retried }) {
  return !retried && method !== "GET" && status === 403 && error === "csrf";
}

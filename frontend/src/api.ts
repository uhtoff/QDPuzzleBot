import { useQuery } from "@tanstack/react-query";
import type { HuntDetail, HuntList, User } from "./types";

/** Wrap fetch with credentials so the session cookie is always sent. */
async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(path, { credentials: "include" });
  if (res.status === 401) throw new AuthError();
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

export class AuthError extends Error {
  constructor() {
    super("auth_required");
    this.name = "AuthError";
  }
}

export function loginUrl() {
  return "/api/auth/login";
}

export async function logout() {
  await fetch("/api/auth/logout", { method: "POST", credentials: "include" });
}

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      const data = await apiGet<{ authenticated: boolean; user: User }>("/api/auth/me");
      return data.user;
    },
    retry: false,
    staleTime: 5 * 60 * 1000,
  });
}

export function useHunts() {
  return useQuery({
    queryKey: ["hunts"],
    queryFn: () => apiGet<HuntList>("/api/hunts"),
  });
}

export function useHunt(uid: string | undefined) {
  return useQuery({
    queryKey: ["hunt", uid],
    queryFn: () => apiGet<HuntDetail>(`/api/hunts/${uid}`),
    enabled: !!uid,
    refetchInterval: 30_000,
  });
}

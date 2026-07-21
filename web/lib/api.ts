const TOKEN_KEY = "investment.paper.jwt";

export function getToken(): string | null {
  return typeof window === "undefined" ? null : window.sessionStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  window.sessionStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  if (typeof window !== "undefined") window.sessionStorage.removeItem(TOKEN_KEY);
}

async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const response = await fetch(url, {
    ...init,
    cache: "no-store",
    headers: {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
  });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401 && typeof window !== "undefined") {
    clearToken();
    window.location.href = "/login";
  }
  if (!response.ok) throw new Error(data.detail || data.error || `${response.status} ${response.statusText}`);
  return data as T;
}

const post = <T>(url: string, body?: unknown) => request<T>(url, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  login: async (username: string, password: string) => {
    const result = await post<{ access_token: string }>("/api/auth/login", { username, password });
    setToken(result.access_token);
    return result;
  },
  me: () => request<{ id: number; username: string; email: string; role: string }>("/api/auth/me"),
  marketEvent: (body: unknown) => post<{ id: number; idempotent: boolean }>("/api/governed/market-events", body),
  order: (body: unknown) => post<{ order: Record<string, unknown>; idempotent: boolean }>("/api/governed/orders", body),
  decision: (id: number, body: unknown) => post<Record<string, unknown>>(`/api/governed/orders/${id}/decision`, body),
  fill: (id: number, body: unknown) => post<Record<string, unknown>>(`/api/governed/orders/${id}/fills`, body),
  reconcile: () => post<Record<string, unknown>>("/api/governed/reconcile"),
  audit: () => request<{ events: Record<string, unknown>[] }>("/api/governed/audit/export"),
};

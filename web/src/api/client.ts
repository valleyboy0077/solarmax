export type FieldErrors = Record<string, string>;

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly fields: FieldErrors = {}) { super(message); }
}

type MutationResult = { ok: true; redirect_to: string; resource_id?: number | null; data?: Record<string, unknown> | null };

function isJson(response: Response) { return response.headers.get("content-type")?.includes("application/json") ?? false; }

function validationErrors(detail: unknown): FieldErrors {
  if (!Array.isArray(detail)) return {};
  return Object.fromEntries(detail.map((item) => {
    const value = item as { loc?: unknown[]; msg?: string };
    return [String(value.loc?.at(-1) ?? "form"), value.msg ?? "Invalid value"];
  }));
}

export async function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json", "Cache-Control": "no-store" }, signal });
  if (!response.ok) {
    const body: unknown = isJson(response) ? await response.json() : undefined;
    throw new ApiError((body as { detail?: string })?.detail ?? `Request failed (${response.status})`, response.status, validationErrors((body as { detail?: unknown })?.detail));
  }
  if (!isJson(response)) throw new ApiError("Expected a JSON API response.", response.status);
  return response.json() as Promise<T>;
}

export async function apiForm(path: string, fields: Record<string, string | number | boolean | null | undefined>, signal?: AbortSignal): Promise<MutationResult> {
  const body = new URLSearchParams();
  Object.entries(fields).forEach(([key, value]) => { if (value !== undefined && value !== null) body.set(key, String(value)); });
  const response = await fetch(path, { method: "POST", headers: { Accept: "application/json", "Content-Type": "application/x-www-form-urlencoded" }, body, signal });
  const payload: unknown = isJson(response) ? await response.json() : undefined;
  if (!response.ok || !(payload as { ok?: boolean })?.ok) {
    const error = payload as { error?: { message?: string }; detail?: unknown };
    throw new ApiError(error.error?.message ?? "The request could not be completed.", response.status, validationErrors(error.detail));
  }
  return payload as MutationResult;
}

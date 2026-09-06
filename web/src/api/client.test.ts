import { afterEach, describe, expect, it, vi } from "vitest";
import { apiForm } from "./client";

describe("apiForm", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("uses the JSON mutation contract and omits unset optional fields", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true, redirect_to: "/settings" }), { headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(apiForm("/api/settings", { theme: "deep-ocean", active_plan_id: null, enabled: true })).resolves.toMatchObject({ ok: true });
    const [, request] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(request.headers).toMatchObject({ Accept: "application/json" });
    expect(String(request.body)).toContain("theme=deep-ocean");
    expect(String(request.body)).toContain("enabled=true");
    expect(String(request.body)).not.toContain("active_plan_id");
  });

  it("normalizes negotiated mutation errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: false, error: { message: "Invalid timezone" } }), { status: 422, headers: { "content-type": "application/json" } })));
    await expect(apiForm("/api/settings", { theme: "classic-dark" })).rejects.toEqual(expect.objectContaining({ message: "Invalid timezone", status: 422 }));
  });
});

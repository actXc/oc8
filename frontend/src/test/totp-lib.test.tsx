import { describe, expect, it, vi, beforeEach } from "vitest";
import { enrollTotp, confirmTotp, verifyTotp, renderTotpQrDataUrl } from "@/lib/totp";

describe("totp lib", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ secret: "ABC", provisioningUri: "otpauth://totp/x" }),
      })),
    );
  });

  it("enrollTotp calls the enroll endpoint with the bearer token", async () => {
    const result = await enrollTotp("narrow-token");
    expect(result.secret).toBe("ABC");
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/auth/totp/enroll"),
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ authorization: "Bearer narrow-token" }),
      }),
    );
  });

  it("renderTotpQrDataUrl returns a data: URL", async () => {
    const dataUrl = await renderTotpQrDataUrl("otpauth://totp/x");
    expect(dataUrl.startsWith("data:image/")).toBe(true);
  });
});

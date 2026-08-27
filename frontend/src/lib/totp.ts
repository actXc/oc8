// frontend/src/lib/totp.ts
// Thin client for the three shared TOTP endpoints. Deliberately raw fetch,
// not the authenticated `api.*` client (frontend/src/lib/api.ts) -- these
// calls happen with a narrow enrollment/challenge token or mid-login,
// before a real session exists, exactly like login.tsx's own
// handleLogin/handleSetup already do.

import QRCode from "qrcode";

const API_URL =
  (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8099/api/v1";

export interface TotpEnrollResult {
  secret: string;
  provisioningUri: string;
}

export interface TotpConfirmResult {
  backupCodes: string[];
}

export interface TotpSessionResult {
  token: string;
  principal: unknown;
  memberId: string;
  totpGraceExpiresAt?: string | null;
  requiresTotpCode?: boolean;
  requiresTotpEnrollment?: boolean;
}

async function post<T>(path: string, token: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${token}`,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail ?? "TOTP request failed");
  }
  return (await res.json()) as T;
}

export function enrollTotp(token: string): Promise<TotpEnrollResult> {
  return post<TotpEnrollResult>("/auth/totp/enroll", token);
}

export function confirmTotp(
  token: string,
  secret: string,
  code: string,
): Promise<TotpConfirmResult> {
  return post<TotpConfirmResult>("/auth/totp/confirm", token, { secret, code });
}

export function verifyTotp(token: string, code: string): Promise<TotpSessionResult> {
  return post<TotpSessionResult>("/auth/totp/verify", token, { code });
}

export function renderTotpQrDataUrl(provisioningUri: string): Promise<string> {
  return QRCode.toDataURL(provisioningUri);
}

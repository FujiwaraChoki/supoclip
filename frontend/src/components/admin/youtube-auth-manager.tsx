"use client";

import { type ChangeEvent, Fragment, useEffect, useMemo, useState } from "react";

type YouTubeCookieAccount = {
  id: string;
  label: string;
  email_hint: string | null;
  status: string;
  priority: number;
  last_used_at: string | null;
  last_verified_at: string | null;
  consecutive_auth_failures: number;
  last_error_code: string | null;
  last_error_message: string | null;
  cooldown_until: string | null;
};

type YouTubeCookieEvent = {
  id: string;
  event_type: string;
  status: string;
  message: string | null;
  created_at: string;
};

type ManualCookiesResponse = {
  cookies_text: string | null;
};

type AccountRow = {
  account: YouTubeCookieAccount;
};

function formatDate(value: string | null) {
  if (!value) return "Never";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

export function YouTubeAuthManager() {
  const [accounts, setAccounts] = useState<AccountRow[]>([]);
  const [events, setEvents] = useState<Record<string, YouTubeCookieEvent[]>>({});
  const [cookieDrafts, setCookieDrafts] = useState<Record<string, string>>({});
  const [expandedAccountId, setExpandedAccountId] = useState<string | null>(null);
  const [label, setLabel] = useState("");
  const [emailHint, setEmailHint] = useState("");
  const [priority, setPriority] = useState("100");
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [loadingKey, setLoadingKey] = useState<string | null>(null);

  const sortedAccounts = useMemo(
    () => [...accounts].sort((a, b) => a.account.priority - b.account.priority),
    [accounts]
  );

  async function loadAccounts() {
    const response = await fetch("/api/admin/youtube-cookie-accounts", {
      cache: "no-store",
    });
    if (!response.ok) {
      setStatusMessage("Failed to load YouTube auth accounts.");
      return;
    }
    const payload = (await response.json()) as { accounts: AccountRow[] };
    setAccounts(payload.accounts || []);
  }

  async function loadEvents(accountId: string) {
    const response = await fetch(
      `/api/admin/youtube-cookie-accounts/${accountId}/events`,
      { cache: "no-store" }
    );
    if (!response.ok) {
      setStatusMessage("Failed to load account events.");
      return;
    }
    const payload = (await response.json()) as { events: YouTubeCookieEvent[] };
    setEvents((current) => ({ ...current, [accountId]: payload.events || [] }));
  }

  async function loadSavedCookies(accountId: string) {
    const response = await fetch(
      `/api/admin/youtube-cookie-accounts/${accountId}/manual-cookies`,
      { cache: "no-store" }
    );
    if (!response.ok) {
      setStatusMessage("Failed to load saved cookies.");
      return;
    }
    const payload = (await response.json()) as ManualCookiesResponse;
    setCookieDrafts((current) => ({
      ...current,
      [accountId]: payload.cookies_text || "",
    }));
  }

  useEffect(() => {
    loadAccounts();
  }, []);

  async function createAccount() {
    setLoadingKey("create");
    setStatusMessage(null);
    try {
      const response = await fetch("/api/admin/youtube-cookie-accounts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          label,
          email_hint: emailHint || null,
          priority: Number(priority) || 100,
        }),
      });
      if (!response.ok) {
        throw new Error("Failed to create account");
      }
      setLabel("");
      setEmailHint("");
      setPriority("100");
      setStatusMessage("Created YouTube auth account.");
      await loadAccounts();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Failed to create account.");
    } finally {
      setLoadingKey(null);
    }
  }

  async function patchAccount(accountId: string, body: Record<string, unknown>) {
    setLoadingKey(`${accountId}:${body.action ?? "patch"}`);
    setStatusMessage(null);
    try {
      const response = await fetch(`/api/admin/youtube-cookie-accounts/${accountId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        throw new Error("Failed to update account");
      }
      await loadAccounts();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Failed to update account.");
    } finally {
      setLoadingKey(null);
    }
  }

  async function verifyAccount(accountId: string) {
    setLoadingKey(`${accountId}:verify`);
    setStatusMessage(null);
    try {
      const response = await fetch(
        `/api/admin/youtube-cookie-accounts/${accountId}/verify`,
        { method: "POST" }
      );
      if (!response.ok) {
        throw new Error("Failed to verify account");
      }
      const payload = (await response.json()) as {
        verified: boolean;
        account?: YouTubeCookieAccount;
      };
      setStatusMessage(
        payload.verified
          ? "Account verified successfully."
          : payload.account?.last_error_message || "Account verification failed."
      );
      await loadAccounts();
      if (expandedAccountId === accountId) {
        await loadEvents(accountId);
      }
    } catch (error) {
      setStatusMessage(
        error instanceof Error ? error.message : "Failed to verify account."
      );
    } finally {
      setLoadingKey(null);
    }
  }

  async function uploadManualCookies(accountId: string) {
    const cookiesText = (cookieDrafts[accountId] || "").trim();
    if (!cookiesText) {
      setStatusMessage("Paste a cookies.txt export first.");
      return;
    }

    setLoadingKey(`${accountId}:upload`);
    setStatusMessage(null);
    try {
      const response = await fetch(
        `/api/admin/youtube-cookie-accounts/${accountId}/manual-cookies`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ cookies_text: cookiesText }),
        }
      );
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as
          | { detail?: string }
          | null;
        throw new Error(payload?.detail || "Failed to save cookies");
      }
      setStatusMessage(
        "Saved cookies.txt for this account. yt-dlp will use it on the next YouTube request."
      );
      await loadAccounts();
      if (expandedAccountId === accountId) {
        await loadEvents(accountId);
      }
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Failed to save cookies.");
    } finally {
      setLoadingKey(null);
    }
  }

  async function loadCookieFile(
    accountId: string,
    event: ChangeEvent<HTMLInputElement>
  ) {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }

    try {
      const text = await file.text();
      setCookieDrafts((current) => ({ ...current, [accountId]: text }));
      setExpandedAccountId(accountId);
      setStatusMessage(`Loaded ${file.name}. Review it, then click Save cookies.`);
    } catch {
      setStatusMessage("Failed to read the selected cookies.txt file.");
    } finally {
      event.target.value = "";
    }
  }

  async function toggleEvents(accountId: string) {
    if (expandedAccountId === accountId) {
      setExpandedAccountId(null);
      return;
    }
    setExpandedAccountId(accountId);
    if (typeof cookieDrafts[accountId] === "undefined") {
      await loadSavedCookies(accountId);
    }
    if (!events[accountId]) {
      await loadEvents(accountId);
    }
  }

  return (
    <section className="mt-8 rounded-lg border border-gray-200 bg-white">
      <div className="border-b border-gray-200 px-4 py-3">
        <h2 className="text-lg font-medium">YouTube Auth Rotation</h2>
        <p className="text-sm text-gray-600">
          Paste or upload a fresh Netscape-format <code>cookies.txt</code> export for each
          YouTube account. SupoClip saves that file and uses it directly with{" "}
          <code>yt-dlp</code>.
        </p>
      </div>

      <div className="border-b border-gray-200 px-4 py-4">
        <div className="grid gap-3 md:grid-cols-[2fr,2fr,140px,auto]">
          <input
            className="rounded-md border border-gray-300 px-3 py-2 text-sm"
            placeholder="Account label"
            value={label}
            onChange={(event) => setLabel(event.target.value)}
          />
          <input
            className="rounded-md border border-gray-300 px-3 py-2 text-sm"
            placeholder="Email hint (optional)"
            value={emailHint}
            onChange={(event) => setEmailHint(event.target.value)}
          />
          <input
            className="rounded-md border border-gray-300 px-3 py-2 text-sm"
            placeholder="Priority"
            type="number"
            value={priority}
            onChange={(event) => setPriority(event.target.value)}
          />
          <button
            type="button"
            onClick={createAccount}
            disabled={!label.trim() || loadingKey === "create"}
            className="rounded-md bg-black px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loadingKey === "create" ? "Creating..." : "Create account"}
          </button>
        </div>
        {statusMessage ? <p className="mt-3 text-sm text-gray-700">{statusMessage}</p> : null}
      </div>

      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
                Account
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
                Status
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
                Last used
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
                Last verified
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
                Failures
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
                Actions
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200 bg-white">
            {sortedAccounts.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-4 text-sm text-gray-600">
                  No YouTube auth accounts configured yet.
                </td>
              </tr>
            ) : (
              sortedAccounts.map(({ account }) => (
                <Fragment key={account.id}>
                  <tr>
                    <td className="px-4 py-3 align-top">
                      <p className="text-sm font-medium text-black">{account.label}</p>
                      <p className="text-xs text-gray-600">
                        {account.email_hint || "No email hint"} · Priority {account.priority}
                      </p>
                      {account.last_error_message ? (
                        <p className="mt-2 text-xs text-red-600">{account.last_error_message}</p>
                      ) : null}
                    </td>
                    <td className="px-4 py-3 align-top">
                      <p className="text-sm text-black">{account.status}</p>
                      {account.cooldown_until ? (
                        <p className="text-xs text-gray-600">
                          Cooldown until {formatDate(account.cooldown_until)}
                        </p>
                      ) : null}
                    </td>
                    <td className="px-4 py-3 align-top text-sm text-gray-700">
                      {formatDate(account.last_used_at)}
                    </td>
                    <td className="px-4 py-3 align-top text-sm text-gray-700">
                      {formatDate(account.last_verified_at)}
                    </td>
                    <td className="px-4 py-3 align-top text-sm text-gray-700">
                      {account.consecutive_auth_failures}
                      {account.last_error_code ? (
                        <p className="text-xs text-gray-600">{account.last_error_code}</p>
                      ) : null}
                    </td>
                    <td className="px-4 py-3 align-top">
                      <div className="flex flex-wrap gap-2">
                        <button
                          type="button"
                          onClick={() => verifyAccount(account.id)}
                          disabled={loadingKey === `${account.id}:verify`}
                          className="rounded border border-gray-300 px-3 py-1 text-xs font-medium text-black"
                        >
                          {loadingKey === `${account.id}:verify` ? "Verifying..." : "Verify now"}
                        </button>
                        <button
                          type="button"
                          onClick={() => patchAccount(account.id, { action: "promote_primary" })}
                          className="rounded border border-gray-300 px-3 py-1 text-xs font-medium text-black"
                        >
                          Promote
                        </button>
                        <button
                          type="button"
                          onClick={() =>
                            patchAccount(account.id, {
                              action: account.status === "disabled" ? "enable" : "disable",
                            })
                          }
                          className="rounded border border-gray-300 px-3 py-1 text-xs font-medium text-black"
                        >
                          {account.status === "disabled" ? "Enable" : "Disable"}
                        </button>
                        <button
                          type="button"
                          onClick={() => patchAccount(account.id, { action: "retire" })}
                          className="rounded border border-gray-300 px-3 py-1 text-xs font-medium text-black"
                        >
                          Retire
                        </button>
                        <button
                          type="button"
                          onClick={() => toggleEvents(account.id)}
                          className="rounded border border-gray-300 px-3 py-1 text-xs font-medium text-black"
                        >
                          {expandedAccountId === account.id ? "Hide details" : "Manage cookies"}
                        </button>
                      </div>
                    </td>
                  </tr>
                  {expandedAccountId === account.id ? (
                    <tr key={`${account.id}-events`}>
                      <td colSpan={6} className="bg-gray-50 px-4 py-4">
                        <div className="space-y-4">
                          <div className="rounded-md border border-gray-200 bg-white px-3 py-3">
                            <p className="text-sm font-medium text-black">Manual cookies</p>
                            <p className="mt-1 text-xs text-gray-600">
                              Paste the full contents of a Netscape-format{" "}
                              <code>cookies.txt</code> export for YouTube. This writes directly to
                              the file used by <code>yt-dlp</code>.
                            </p>
                            <textarea
                              className="mt-3 min-h-48 w-full rounded-md border border-gray-300 px-3 py-2 text-xs"
                              placeholder="# Netscape HTTP Cookie File&#10;.youtube.com	TRUE	/	TRUE	..."
                              value={cookieDrafts[account.id] || ""}
                              onChange={(event) =>
                                setCookieDrafts((current) => ({
                                  ...current,
                                  [account.id]: event.target.value,
                                }))
                              }
                            />
                            <div className="mt-3 flex flex-wrap gap-2">
                              <label className="cursor-pointer rounded border border-gray-300 px-3 py-1 text-xs font-medium text-black">
                                Load cookies.txt
                                <input
                                  type="file"
                                  accept=".txt,text/plain"
                                  className="hidden"
                                  onChange={(event) => loadCookieFile(account.id, event)}
                                />
                              </label>
                              <button
                                type="button"
                                onClick={() => uploadManualCookies(account.id)}
                                disabled={loadingKey === `${account.id}:upload`}
                                className="rounded bg-black px-3 py-1 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
                              >
                                {loadingKey === `${account.id}:upload`
                                  ? "Saving..."
                                  : "Save cookies"}
                              </button>
                              <button
                                type="button"
                                onClick={() => verifyAccount(account.id)}
                                disabled={loadingKey === `${account.id}:verify`}
                                className="rounded border border-gray-300 px-3 py-1 text-xs font-medium text-black"
                              >
                                {loadingKey === `${account.id}:verify`
                                  ? "Verifying..."
                                  : "Verify now"}
                              </button>
                            </div>
                          </div>

                          <div className="space-y-2">
                            <p className="text-sm font-medium text-black">Recent events</p>
                          {(events[account.id] || []).length === 0 ? (
                            <p className="text-sm text-gray-600">No events recorded yet.</p>
                          ) : (
                            (events[account.id] || []).map((event) => (
                              <div
                                key={event.id}
                                className="rounded-md border border-gray-200 bg-white px-3 py-2"
                              >
                                <p className="text-sm font-medium text-black">
                                  {event.event_type} · {event.status}
                                </p>
                                <p className="text-xs text-gray-600">
                                  {formatDate(event.created_at)}
                                </p>
                                {event.message ? (
                                  <p className="mt-1 text-sm text-gray-700">{event.message}</p>
                                ) : null}
                              </div>
                            ))
                          )}
                          </div>
                        </div>
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

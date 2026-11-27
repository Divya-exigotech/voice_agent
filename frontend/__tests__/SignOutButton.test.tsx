import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SignOutButton } from "@/components/auth/SignOutButton";

const { replace, refresh } = vi.hoisted(() => ({
  replace: vi.fn(),
  refresh: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, refresh }),
}));

describe("SignOutButton", () => {
  function mockFetchForLogout(
    logoutResponse: Response | { ok: boolean },
  ): void {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        if (String(input) === "/api/auth/session") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              user: {
                name: "Siddharth Balsod",
                email: "siddharth@example.com",
              },
            }),
          });
        }
        return Promise.resolve(logoutResponse);
      }),
    );
  }

  beforeEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("re-enables the button and redirects after a successful sign-out", async () => {
    mockFetchForLogout({ ok: true });
    const user = userEvent.setup();
    render(<SignOutButton />);

    expect(await screen.findByText("Siddharth Balsod")).toBeVisible();
    expect(screen.getByText("siddharth@example.com")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Sign out" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Sign out" })).toBeEnabled();
    });
    expect(replace).toHaveBeenCalledWith("/");
    expect(refresh).toHaveBeenCalled();
  });

  it("re-enables the button and shows an error when the request fails", async () => {
    mockFetchForLogout({ ok: false });
    const user = userEvent.setup();
    render(<SignOutButton />);

    await user.click(screen.getByRole("button", { name: "Sign out" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Sign out failed. Please try again.",
    );
    expect(screen.getByRole("button", { name: "Sign out" })).toBeEnabled();
    expect(replace).not.toHaveBeenCalled();
  });

  it("re-enables the button when the request times out", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        if (String(input) === "/api/auth/session") {
          return Promise.resolve({
            ok: true,
            json: async () => ({ user: null }),
          });
        }
        return new Promise<Response>(
          (_resolve, reject) =>
            init?.signal?.addEventListener(
              "abort",
              () => reject(new DOMException("Aborted", "AbortError")),
              { once: true },
            ),
        );
      }),
    );
    render(<SignOutButton />);

    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(screen.getByRole("button", { name: "Signing out" })).toBeDisabled();

    await act(async () => {
      vi.advanceTimersByTime(10_000);
      await Promise.resolve();
    });

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Sign-out timed out. Check your connection and try again.",
    );
    expect(screen.getByRole("button", { name: "Sign out" })).toBeEnabled();
  });
});

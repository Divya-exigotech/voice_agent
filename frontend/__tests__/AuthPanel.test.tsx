import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderToString } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { AuthPanel } from "@/components/auth/AuthPanel";

const { replace, refresh } = vi.hoisted(() => ({
  replace: vi.fn(),
  refresh: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, refresh }),
}));

describe("AuthPanel", () => {
  it("re-enables sign-in after success before navigating", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ access_token: "valid-token" }),
      }),
    );
    render(<AuthPanel />);

    await user.type(screen.getByRole("textbox", { name: /email address/i }), "user@example.com");
    await user.type(screen.getByLabelText(/password/i), "correct-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
    });
    expect(replace).toHaveBeenCalledWith("/dashboard");
  });

  it("does not server-render extension-sensitive login fields", () => {
    const markup = renderToString(<AuthPanel />);

    expect(markup).not.toContain('name="email"');
    expect(markup).toContain('class="auth-card auth-card-loading"');
  });

  it("renders the login fields after hydration", () => {
    render(<AuthPanel />);

    expect(screen.getByRole("textbox", { name: /email address/i })).toBeVisible();
    expect(screen.getByLabelText(/password/i)).toBeVisible();
  });
});

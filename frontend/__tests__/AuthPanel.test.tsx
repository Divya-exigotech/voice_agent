import { render, screen } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";

import { AuthPanel } from "@/components/auth/AuthPanel";

function visit(search: string) {
  window.history.replaceState(null, "", `/${search}`);
}

describe("AuthPanel", () => {
  afterEach(() => visit(""));

  it("offers only Microsoft sign-in", () => {
    render(<AuthPanel />);

    const signIn = screen.getByRole("link", { name: /sign in with microsoft/i });
    expect(signIn).toHaveAttribute("href", "/api/auth/microsoft/login");
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /register/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("explains why the previous sign-in did not complete", () => {
    visit("?error=cancelled");
    render(<AuthPanel />);

    expect(screen.getByRole("alert")).toHaveTextContent(/sign-in was cancelled/i);
  });

  it("shows a generic message for unknown error codes", () => {
    visit("?error=something-else");
    render(<AuthPanel />);

    expect(screen.getByRole("alert")).toHaveTextContent(/microsoft sign-in failed/i);
  });

  it("server-renders a placeholder until hydration", () => {
    const markup = renderToString(<AuthPanel />);

    expect(markup).toContain('class="auth-card auth-card-loading"');
    expect(markup).not.toContain("Sign in with Microsoft");
  });
});

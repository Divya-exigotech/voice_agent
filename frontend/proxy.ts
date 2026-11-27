import { NextResponse, type NextRequest } from "next/server";

const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8000";

export async function proxy(request: NextRequest) {
  let sessionResponse: Response;
  try {
    sessionResponse = await fetch(new URL("/api/auth/session", backendUrl), {
      headers: { cookie: request.headers.get("cookie") ?? "" },
      cache: "no-store",
    });
  } catch {
    return NextResponse.json(
      { detail: "The authentication service is unavailable." },
      { status: 503 },
    );
  }
  if (!sessionResponse.ok) {
    return NextResponse.json(
      { detail: "The authentication service could not validate this session." },
      { status: 503 },
    );
  }

  const session: { enabled: boolean; authenticated: boolean } =
    await sessionResponse.json();
  const isSignInPage = request.nextUrl.pathname === "/";
  if (isSignInPage) {
    if (session.enabled && session.authenticated) {
      return NextResponse.redirect(new URL("/dashboard", request.url));
    }
    return NextResponse.next();
  }
  if (!session.enabled || session.authenticated) {
    return NextResponse.next();
  }
  return NextResponse.redirect(new URL("/", request.url));
}

export const config = {
  matcher: ["/((?!api/|_next/static|_next/image|favicon.ico|icon.svg|brand/).*)"],
};

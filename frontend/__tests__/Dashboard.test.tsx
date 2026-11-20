import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Dashboard } from "@/components/Dashboard";
import { ApiError } from "@/lib/api";
import type { ChatResponse, Customer, QueueState, SessionRecord, TranscriptTurn } from "@/lib/types";
import type { TranscriptUpdate, VoiceCallbacks } from "@/lib/voice/voiceSession";

const { mockApi, mockVoice } = vi.hoisted(() => {
  const instances: Array<{
    callbacks: VoiceCallbacks;
    connect: ReturnType<typeof vi.fn>;
    stop: ReturnType<typeof vi.fn>;
    setMuted: ReturnType<typeof vi.fn>;
  }> = [];
  const control: { connectError: Error | null } = { connectError: null };
  class FakeVoiceSession {
    connect = vi.fn(async () => {
      if (control.connectError) throw control.connectError;
      return { transcript: [], proposed_outcome: null };
    });
    stop = vi.fn(async () => {});
    setMuted = vi.fn();
    constructor(
      readonly stream: unknown,
      readonly callbacks: VoiceCallbacks,
    ) {
      instances.push(this);
    }
  }
  return {
    mockApi: {
      getHealth: vi.fn(),
      getQueue: vi.fn(),
      startSession: vi.fn(),
      sendMessage: vi.fn(),
      finalizeSession: vi.fn(),
      getTranscript: vi.fn(),
      resetQueue: vi.fn(),
    },
    mockVoice: { instances, control, FakeVoiceSession, requestMicrophone: vi.fn() },
  };
});

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: mockApi };
});

vi.mock("@/lib/voice/voiceSession", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/voice/voiceSession")>();
  return {
    ...actual,
    requestMicrophone: mockVoice.requestMicrophone,
    VoiceSession: mockVoice.FakeVoiceSession,
  };
});

function turn(speaker: TranscriptTurn["speaker"], text: string, n: number): TranscriptTurn {
  return { turn: n, speaker, text, timestamp: "2026-11-16T10:00:05Z" };
}

function chat(transcript: TranscriptTurn[], proposed: ChatResponse["proposed_outcome"] = null): ChatResponse {
  return { agent_turn: transcript[transcript.length - 1], transcript, proposed_outcome: proposed };
}

const OPENING = turn("agent", "Hello Aarav, I am the payment assistant. Have you paid INV-1001?", 1);
const PROMISE = {
  outcome: "payment_promised" as const,
  promised_payment_date: "2026-11-20",
  summary: null,
  proposed_at: "2026-11-16T10:01:00Z",
};

function customer(n: number, name: string): Customer {
  return {
    customer_id: `cust_${n}`,
    customer_name: name,
    phone_number: "+91XXXXXXXXXX",
    preferred_language: "English",
    invoice: {
      invoice_id: `inv_${n}`,
      invoice_number: `INV-100${n}`,
      amount_due: 2500,
      currency: "INR",
      due_date: "2026-11-10",
      payment_status: "unpaid",
    },
  };
}

const CUSTOMERS = [customer(1, "Aarav Patel"), customer(2, "Mira Shah"), customer(3, "Rohan Mehta")];

function queueAt(index: number, activeSessionId: string | null = null): QueueState {
  const total = CUSTOMERS.length;
  const completed = index >= total;
  return {
    total,
    current_index: index,
    position: completed ? null : index + 1,
    remaining: total - index,
    completed,
    current_customer: completed ? null : CUSTOMERS[index],
    active_session_id: activeSessionId,
  };
}

function session(index: number, overrides: Partial<SessionRecord> = {}): SessionRecord {
  return {
    session_id: `session_${index}`,
    customer_id: CUSTOMERS[index].customer_id,
    invoice_id: CUSTOMERS[index].invoice.invoice_id,
    sequence_index: index,
    status: "active",
    started_at: "2026-11-16T10:00:00Z",
    ended_at: null,
    outcome: null,
    promised_payment_date: null,
    transcript_ref: null,
    transcript_store: null,
    ...overrides,
  };
}

function finalized(index: number, nextIndex: number, overrides: Partial<SessionRecord> = {}) {
  return {
    session: session(index, { status: "completed", ended_at: "2026-11-16T10:02:00Z", ...overrides }),
    queue: queueAt(nextIndex),
    advanced: nextIndex !== index,
    already_finalized: false,
  };
}

const fakeStream = { getTracks: () => [{ stop: vi.fn() }] };

function lastVoice() {
  return mockVoice.instances[mockVoice.instances.length - 1];
}

function push(update: TranscriptUpdate) {
  act(() => lastVoice().callbacks.onTranscript(update));
}

beforeEach(() => {
  vi.clearAllMocks();
  mockVoice.instances.length = 0;
  mockVoice.control.connectError = null;
  mockVoice.requestMicrophone.mockResolvedValue(fakeStream);
  mockApi.getHealth.mockResolvedValue({ status: "ok", agent_configured: true, transcript_storage: "azure_blob" });
});

describe("Dashboard", () => {
  it("renders the first customer with sequence progress and no customer picker", async () => {
    mockApi.getQueue.mockResolvedValue(queueAt(0));
    render(<Dashboard />);

    expect(await screen.findByText("Aarav Patel")).toBeInTheDocument();
    expect(screen.getByText("Customer 1 of 3")).toBeInTheDocument();
    expect(screen.getByText("INV-1001")).toBeInTheDocument();
    expect(screen.getByText("Unpaid")).toBeInTheDocument();
    expect(screen.queryByText("Mira Shah")).not.toBeInTheDocument();

    // No selection UI of any kind.
    for (const role of ["combobox", "listbox", "radio", "checkbox", "option", "table"]) {
      expect(screen.queryAllByRole(role)).toHaveLength(0);
    }

    expect(screen.getByRole("button", { name: "Start Agent" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "End Session" })).toBeDisabled();
    expect(screen.getByTestId("session-status")).toHaveTextContent("Ready");
    // Microphone is not requested until the operator presses Start Agent.
    expect(mockVoice.requestMicrophone).not.toHaveBeenCalled();
  });

  it("runs a voice session for the current customer and advances after End Session", async () => {
    const user = userEvent.setup();
    mockApi.getQueue.mockResolvedValue(queueAt(0));
    mockApi.startSession.mockResolvedValue(session(0));
    mockApi.finalizeSession.mockResolvedValue(
      finalized(0, 1, { outcome: "payment_promised", promised_payment_date: "2026-11-20" }),
    );

    render(<Dashboard />);
    await user.click(await screen.findByRole("button", { name: "Start Agent" }));

    // Mic first, then a session bound to the displayed customer, then voice.
    expect(mockVoice.requestMicrophone).toHaveBeenCalledTimes(1);
    expect(mockApi.startSession).toHaveBeenCalledWith("cust_1");
    expect(lastVoice().connect).toHaveBeenCalledWith("session_0");
    expect(mockApi.sendMessage).not.toHaveBeenCalled(); // backend starts the spoken opening
    expect(await screen.findByTestId("session-status")).toHaveTextContent("Active");
    expect(screen.getByTestId("mic-state")).toHaveTextContent("Listening");

    // Live transcript from both speakers, pushed over the voice socket.
    push({ transcript: [OPENING], proposed_outcome: null });
    expect(screen.getByText(OPENING.text)).toBeInTheDocument();

    act(() => lastVoice().callbacks.onUserSpeaking(true));
    expect(screen.getByTestId("mic-state")).toHaveTextContent("Customer speaking");
    act(() => lastVoice().callbacks.onUserSpeaking(false));

    push({
      transcript: [OPENING, turn("user", "I will pay on Friday", 2), turn("agent", "Noted, thank you.", 3)],
      proposed_outcome: PROMISE,
    });
    expect(screen.getByText("I will pay on Friday")).toBeInTheDocument();
    expect(screen.getByText("Noted, thank you.")).toBeInTheDocument();
    expect(screen.getByTestId("proposed-outcome")).toHaveTextContent("Payment promised");
    expect(screen.getByTestId("proposed-outcome")).toHaveTextContent("20 Nov 2026");

    act(() => lastVoice().callbacks.onAgentSpeaking(true));
    expect(screen.getByTestId("mic-state")).toHaveTextContent("Agent speaking");

    await user.click(screen.getByRole("button", { name: "Mute" }));
    expect(lastVoice().setMuted).toHaveBeenCalledWith(true);
    expect(screen.getByTestId("mic-state")).toHaveTextContent("Muted");

    const firstCall = lastVoice(); // the next customer's call replaces it after End Session
    await user.click(screen.getByRole("button", { name: "End Session" }));
    expect(firstCall.stop).toHaveBeenCalled();
    expect(mockApi.finalizeSession).toHaveBeenCalledWith("session_0", {
      session_status: "completed",
      transcript: [],
    });
    expect(await screen.findByText("Customer 2 of 3")).toBeInTheDocument();
    expect(screen.getByText("Mira Shah")).toBeInTheDocument();
    // A recorded payment promise ends the call and starts the next customer by itself.
    await waitFor(() => expect(mockApi.startSession).toHaveBeenLastCalledWith("cust_2"));
    expect(mockApi.startSession).toHaveBeenCalledTimes(2);
  });

  it("handles a denied microphone and offers a typed conversation instead", async () => {
    const user = userEvent.setup();
    const { MicrophoneError } = await import("@/lib/voice/voiceSession");
    mockVoice.requestMicrophone.mockRejectedValue(
      new MicrophoneError("denied", "Microphone permission was denied. Allow microphone access for this site and try again."),
    );
    const reply = turn("user", "I will pay on Friday", 2);
    const answer = turn("agent", "Thank you, noted for Friday 20 November.", 3);
    mockApi.getQueue.mockResolvedValue(queueAt(0));
    mockApi.startSession.mockResolvedValue(session(0));
    mockApi.sendMessage
      .mockResolvedValueOnce(chat([OPENING]))
      .mockResolvedValueOnce(chat([OPENING, reply, answer], PROMISE));

    render(<Dashboard />);
    await user.click(await screen.findByRole("button", { name: "Start Agent" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Microphone permission was denied");
    expect(mockApi.startSession).not.toHaveBeenCalled(); // queue not touched
    expect(screen.getByTestId("session-status")).toHaveTextContent("Failed");

    await user.click(screen.getByRole("button", { name: "Continue with typed chat" }));
    expect(mockApi.startSession).toHaveBeenCalledWith("cust_1");
    expect(mockApi.sendMessage).toHaveBeenNthCalledWith(1, "session_0", null);
    expect(await screen.findByText(OPENING.text)).toBeInTheDocument();
    expect(screen.getByTestId("mic-state")).toHaveTextContent("typed conversation");

    await user.type(screen.getByRole("textbox", { name: "Customer reply" }), "I will pay on Friday{Enter}");
    expect(mockApi.sendMessage).toHaveBeenNthCalledWith(2, "session_0", "I will pay on Friday");
    expect(await screen.findByText(answer.text)).toBeInTheDocument();
    expect(screen.getByTestId("proposed-outcome")).toHaveTextContent("Payment promised");
  });

  it("saves the session as failed when the voice connection is lost, keeping the customer", async () => {
    const user = userEvent.setup();
    mockApi.getQueue.mockResolvedValue(queueAt(0));
    mockApi.startSession.mockResolvedValue(session(0));
    mockApi.finalizeSession.mockResolvedValue(
      finalized(0, 0, { status: "failed", outcome: "technical_failure", transcript_ref: "memory://x" }),
    );

    render(<Dashboard />);
    await user.click(await screen.findByRole("button", { name: "Start Agent" }));
    await screen.findByText("Listening", { exact: false });
    push({ transcript: [OPENING], proposed_outcome: null });

    act(() => lastVoice().callbacks.onError("The voice agent disconnected: network"));
    expect(screen.getByRole("alert")).toHaveTextContent("The voice agent disconnected");
    expect(screen.getByTestId("session-status")).toHaveTextContent("Failed");
    expect(screen.getByRole("button", { name: "End Session" })).toBeEnabled();
    expect(screen.getByText(OPENING.text)).toBeInTheDocument(); // partial transcript kept

    await user.click(screen.getByRole("button", { name: "End Session" }));
    expect(mockApi.finalizeSession).toHaveBeenCalledWith("session_0", {
      session_status: "failed",
      transcript: [],
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("saved as failed");
    expect(screen.getByText("Customer 1 of 3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start Agent" })).toBeEnabled();
  });

  it("reports a voice connection failure but keeps the session usable", async () => {
    const user = userEvent.setup();
    mockApi.getQueue.mockResolvedValue(queueAt(0));
    mockApi.startSession.mockResolvedValue(session(0));
    mockVoice.control.connectError = new Error("Could not open the voice connection (code 1006)");

    render(<Dashboard />);
    await user.click(await screen.findByRole("button", { name: "Start Agent" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Voice connection failed");
    expect(screen.getByRole("alert")).toHaveTextContent("code 1006");
    expect(lastVoice().stop).toHaveBeenCalled(); // microphone released
    expect(screen.getByRole("button", { name: "End Session" })).toBeEnabled();
    expect(screen.getByRole("textbox", { name: "Customer reply" })).toBeEnabled();
  });

  it("keeps the same customer when the Blob save fails, and Retry save advances", async () => {
    const user = userEvent.setup();
    mockApi.getQueue.mockResolvedValue(queueAt(0));
    mockApi.startSession.mockResolvedValue(session(0));
    mockApi.finalizeSession
      .mockRejectedValueOnce(new ApiError(502, "Transcript could not be saved (storage unavailable)"))
      .mockResolvedValueOnce(finalized(0, 1, { transcript_ref: "2026/11/cust_1/session_0/transcript.json", transcript_store: "azure_blob" }));

    render(<Dashboard />);
    await user.click(await screen.findByRole("button", { name: "Start Agent" }));
    await screen.findByText("Listening", { exact: false });
    await user.click(screen.getByRole("button", { name: "End Session" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("The transcript was not saved");
    expect(screen.getByRole("alert")).toHaveTextContent("storage unavailable");
    expect(screen.getByText("Customer 1 of 3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start Agent" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Retry save" }));
    expect(mockApi.finalizeSession).toHaveBeenCalledTimes(2);
    expect(await screen.findByText("Customer 2 of 3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "End Session" })).toBeDisabled();
  });

  it("shows demo readiness and no demo guide", async () => {
    mockApi.getQueue.mockResolvedValue(queueAt(0));
    mockApi.getHealth.mockResolvedValue({ status: "ok", agent_configured: false, transcript_storage: "memory" });
    render(<Dashboard />);

    const status = await screen.findByLabelText("System status");
    expect(status).toHaveTextContent("Voice agent: not configured");
    expect(status).toHaveTextContent("Transcripts: in memory only (not persisted)");
    expect(screen.queryByText("How to run the demo")).not.toBeInTheDocument();
  });

  it("shows the session timer and live review flags, then the flag count after End Session", async () => {
    const user = userEvent.setup();
    mockApi.getQueue.mockResolvedValue(queueAt(0));
    mockApi.startSession.mockResolvedValue(session(0));
    mockApi.finalizeSession.mockResolvedValue(finalized(0, 1, { review_flag_count: 1 }));

    render(<Dashboard />);
    await user.click(await screen.findByRole("button", { name: "Start Agent" }));
    expect(await screen.findByTestId("session-timer")).toHaveTextContent("Started");

    push({
      transcript: [OPENING, turn("agent", "Could you share your card number?", 2)],
      proposed_outcome: null,
      review_flags: [
        {
          kind: "sensitive_data_request",
          speaker: "agent",
          detail: "Agent may have asked for sensitive data (card number)",
          excerpt: "Could you share your card number?",
        },
      ],
    });
    const flags = screen.getByRole("status", { name: "Review flags" });
    expect(flags).toHaveTextContent("Review needed · 1 flag");
    expect(flags).toHaveTextContent("Agent may have asked for sensitive data");

    await user.click(screen.getByRole("button", { name: "End Session" }));
    expect(await screen.findByText("Customer 2 of 3")).toBeInTheDocument();
    expect(screen.queryByRole("status", { name: "Review flags" })).toBeNull();
    expect(screen.queryByTestId("session-timer")).toBeNull();
    expect(screen.getByTestId("review-flag-count")).toHaveTextContent("1 to review");
  });

  it("resumes an open session after reload so it can be ended", async () => {
    mockApi.getQueue.mockResolvedValue(queueAt(1, "session_open"));
    render(<Dashboard />);

    expect(await screen.findByText("Customer 2 of 3")).toBeInTheDocument();
    expect(screen.getByTestId("session-status")).toHaveTextContent("Active");
    expect(screen.getByRole("button", { name: "End Session" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Start Agent" })).toBeDisabled();
  });

  it("shows completion and disables Start Agent when all customers are processed", async () => {
    mockApi.getQueue.mockResolvedValue(queueAt(3));
    render(<Dashboard />);

    expect(await screen.findByText("All 3 customers completed")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "All customers completed" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start Agent" })).toBeDisabled();
  });

  it("shows a clear error when the backend is unreachable", async () => {
    mockApi.getQueue.mockRejectedValue(new ApiError(0, "Cannot reach the backend."));
    render(<Dashboard />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot reach the backend.");
    await waitFor(() => expect(screen.queryByRole("button", { name: "Start Agent" })).toBeNull());
  });
});

"use client";

import { useState, type FormEvent } from "react";

interface Props {
  disabled: boolean;
  thinking: boolean;
  onSend: (text: string) => void;
}

/** Typed customer replies for the M2 text prototype (voice replaces this in M3). */
export function ChatComposer({ disabled, thinking, onSend }: Props) {
  const [text, setText] = useState("");
  const trimmed = text.trim();

  function submit(event: FormEvent) {
    event.preventDefault();
    if (disabled || thinking || !trimmed) return;
    onSend(trimmed);
    setText("");
  }

  return (
    <form className="composer" onSubmit={submit}>
      <input
        type="text"
        className="composer-input"
        aria-label="Customer reply"
        placeholder={disabled ? "Start the agent to chat" : "Type the customer's reply…"}
        value={text}
        maxLength={2000}
        disabled={disabled}
        onChange={(e) => setText(e.target.value)}
      />
      <button type="submit" className="btn" disabled={disabled || thinking || !trimmed}>
        Send
      </button>
    </form>
  );
}

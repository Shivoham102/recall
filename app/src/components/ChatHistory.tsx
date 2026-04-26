import React, { useEffect, useRef } from "react";

export interface Turn {
  role: "user" | "assistant";
  text: string;
  intentType?: string;
}

interface Props {
  turns: Turn[];
}

export function ChatHistory({ turns }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns]);

  return (
    <div className="chat-history">
      {turns.length === 0 && (
        <p className="chat-empty">Hold the button and speak to capture something.</p>
      )}
      {turns.map((turn, i) => (
        <div key={i} className={`chat-turn chat-turn--${turn.role}`}>
          {turn.intentType && turn.role === "user" && (
            <span className="chat-turn__badge">{turn.intentType}</span>
          )}
          <p>{turn.text}</p>
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  );
}

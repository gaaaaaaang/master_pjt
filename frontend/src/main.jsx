import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

function App() {
  const [message, setMessage] = useState("");
  const [answer, setAnswer] = useState(null);
  const [loading, setLoading] = useState(false);

  async function submit(event) {
    event.preventDefault();
    if (!message.trim()) return;
    setLoading(true);
    const response = await fetch("http://localhost:8000/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    setAnswer(await response.json());
    setLoading(false);
  }

  return (
    <main className="shell">
      <header>
        <p className="eyebrow">FAB OPERATIONS COPILOT</p>
        <h1>현장 이슈를<br /><span>질문으로 좁혀보세요.</span></h1>
        <p className="intro">WIP, Queue Time, 수율 추세와 공정 원인을 한 번에 확인합니다.</p>
      </header>
      <section className="panel">
        <form onSubmit={submit}>
          <textarea value={message} onChange={(event) => setMessage(event.target.value)} placeholder="예: 왜 A라인 Queue Time이 늘었어?" />
          <button type="submit" disabled={loading}>{loading ? "분석 중..." : "질의 실행"}</button>
        </form>
        {answer && <article className="answer">
          <div className="badge">{answer.query_type}</div>
          <p>{answer.answer}</p>
          <small>{answer.limitations?.join(" ")}</small>
        </article>}
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")).render(<App />);


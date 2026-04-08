function setState(text, thinking=false) {
  const cog = document.getElementById("cognition");
  if (!cog) return;
  cog.innerText = "🧠 " + text;
  if (thinking) {
    cog.classList.add("thinking");
  } else {
    cog.classList.remove("thinking");
  }
}

function addMsg(text, type) {
  const stream = document.getElementById("chat-stream");
  if (!stream) return;
  const el = document.createElement("div");
  el.className = `msg ${type}`;
  el.innerText = text;
  stream.appendChild(el);
  stream.scrollTop = stream.scrollHeight;
}

function typeMessage(text) {
  const stream = document.getElementById("chat-stream");
  const el = document.createElement("div");
  el.className = "msg agent";
  stream.appendChild(el);
  let i = 0;
  setState("Responding...", true);
  const interval = setInterval(() => {
    el.innerText += text[i];
    i++;
    stream.scrollTop = stream.scrollHeight;
    if (i >= text.length) {
      clearInterval(interval);
      setState("Idle");
    }
  }, 15);
}

async function sendMessage() {
  const input = document.getElementById("user-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  addMsg(text, "user");
  setState("Planning...", true);
  try {
    const res = await fetch("/agents/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task: text })
    });
    setState("Executing...", true);
    const data = await res.json();
    setState("Finalizing...", true);
    if (data.result) {
      typeMessage(data.result);
    } else if (data.message) {
      typeMessage(data.message);
    } else {
      typeMessage("No response");
    }
  } catch (err) {
    setState("Error");
    addMsg("⚠️ System error occurred", "agent");
  }
}

const userInput = document.getElementById("user-input");
if (userInput) {
  userInput.addEventListener("keydown", function(e) {
    if (e.key === "Enter") {
      sendMessage();
    }
  });
}
function updateSystem(data) {
  if (!data) return;
  const agentEl = document.getElementById("active-agent");
  if (agentEl) agentEl.innerText = data.agent || "Unknown";
  const feed = document.getElementById("activity-feed");
  if (feed) {
    const entry = document.createElement("div");
    entry.innerText = `${data.tool || "unknown"} → ${data.status || "done"}`;
    feed.prepend(entry);
  }
}
function toggleDevMode() {
  document.getElementById("debug-panel").classList.toggle("hidden");
}
const userInput = document.getElementById("user-input");
if (userInput) {
  userInput.addEventListener("keydown", function(e) {
    if (e.key === "Enter") {
      sendMessage();
    }
  });
}

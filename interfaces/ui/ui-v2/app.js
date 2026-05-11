document.addEventListener("DOMContentLoaded", () => {
    const backendBase = "http://localhost:8000";
    const warmupMessage = "ANDIE runtime initializing... Waiting for cognition services...";

    console.log("ANDIE Online 🧠");

    const orb = document.querySelector(".orb");
    const scanBtn = document.querySelector("#run-scan");
    const cryptoniaStatus = document.getElementById("cryptonia-status");
    const cryptoniaStart = document.getElementById("cryptonia-start");
    const cryptoniaStop = document.getElementById("cryptonia-stop");
    const sendButton = document.querySelector(".input-bar button");
    const topStatus = document.querySelector(".topbar .status");
    const topStatusDot = document.querySelector(".topbar .dot");
    const systemValue = document.querySelector(".status-block .value");
    const cognitionBar = document.getElementById("cognition-bar");
    const controls = [scanBtn, cryptoniaStart, cryptoniaStop, sendButton];
    let backendReady = false;

    function setControlsEnabled(enabled) {
        controls.forEach((control) => {
            if (!control) {
                return;
            }

            control.disabled = !enabled;
            control.style.opacity = enabled ? "1" : "0.6";
            control.style.cursor = enabled ? "pointer" : "not-allowed";
        });

        if (orb) {
            orb.style.opacity = enabled ? "1" : "0.6";
            orb.style.cursor = enabled ? "pointer" : "not-allowed";
            orb.setAttribute("aria-disabled", String(!enabled));
        }
    }

    function setRuntimeStatus(message, ready) {
        backendReady = ready;

        if (topStatus) {
            topStatus.textContent = ready ? "CORE ONLINE" : "CORE WARMING";
            if (topStatusDot) {
                topStatus.prepend(topStatusDot);
                topStatus.append(` ${ready ? "CORE ONLINE" : "CORE WARMING"}`);
            }
        }

        if (topStatusDot) {
            topStatusDot.classList.toggle("online", ready);
        }

        if (systemValue) {
            systemValue.textContent = ready ? "ONLINE" : "WARMING";
            systemValue.classList.toggle("online", ready);
        }

        if (cryptoniaStatus && !ready) {
            cryptoniaStatus.textContent = "Warming up";
        }

        if (cognitionBar) {
            cognitionBar.textContent = ready ? "🧠 Runtime ready" : `🧠 ${message}`;
        }

        setControlsEnabled(ready);
    }

    async function fetchJson(path, options) {
        const res = await fetch(`${backendBase}${path}`, options);
        if (!res.ok) {
            throw new Error(`Request failed: ${path} (${res.status})`);
        }
        return res.json();
    }

    async function updateCryptoniaStatus() {
        if (!cryptoniaStatus) {
            return;
        }

        if (!backendReady) {
            cryptoniaStatus.textContent = "Warming up";
            return;
        }

        try {
            const data = await fetchJson("/agents/cryptonia/status");
            cryptoniaStatus.textContent = data.status || "Unknown";
        } catch (error) {
            cryptoniaStatus.textContent = "Unavailable";
        }
    }

    async function pollBackendReadiness() {
        try {
            const data = await fetchJson("/healthz");
            const ready = Boolean(data.api_ready);

            setRuntimeStatus(ready ? "Runtime ready" : warmupMessage, ready);

            if (ready) {
                await updateCryptoniaStatus();
            }
        } catch (error) {
            setRuntimeStatus(warmupMessage, false);
        }
    }

    function requireReady() {
        if (backendReady) {
            return true;
        }

        setRuntimeStatus(warmupMessage, false);
        return false;
    }

    if (orb) {
        orb.addEventListener("click", async () => {
            if (!requireReady()) {
                return;
            }

            console.log("Orb activated");
            orb.classList.add("active");

            try {
                const data = await fetchJson("/chat", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ message: "status check" })
                });
                console.log("ANDIE:", data);
            } catch (error) {
                console.error("Chat status check failed", error);
            } finally {
                orb.classList.remove("active");
            }
        });
    }

    if (scanBtn) {
        scanBtn.addEventListener("click", async () => {
            if (!requireReady()) {
                return;
            }

            console.log("Running security scan...");

            try {
                const data = await fetchJson("/agents/run", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ agent: "security" })
                });
                console.log("Scan result:", data);
            } catch (error) {
                console.error("Security scan failed", error);
            }
        });
    }

    if (cryptoniaStart) {
        cryptoniaStart.addEventListener("click", async () => {
            if (!requireReady()) {
                return;
            }

            cryptoniaStatus.textContent = "Starting...";
            try {
                await fetchJson("/agents/cryptonia/start", { method: "POST" });
                setTimeout(updateCryptoniaStatus, 1000);
            } catch (error) {
                cryptoniaStatus.textContent = "Unavailable";
            }
        });
    }

    if (cryptoniaStop) {
        cryptoniaStop.addEventListener("click", async () => {
            if (!requireReady()) {
                return;
            }

            cryptoniaStatus.textContent = "Stopping...";
            try {
                await fetchJson("/agents/cryptonia/stop", { method: "POST" });
                setTimeout(updateCryptoniaStatus, 1000);
            } catch (error) {
                cryptoniaStatus.textContent = "Unavailable";
            }
        });
    }

    setRuntimeStatus(warmupMessage, false);
    pollBackendReadiness();
    setInterval(async () => {
        await pollBackendReadiness();
        if (backendReady) {
            await updateCryptoniaStatus();
        }
    }, 10000);
});
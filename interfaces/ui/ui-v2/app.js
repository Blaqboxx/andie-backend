document.addEventListener("DOMContentLoaded", () => {
    console.log("ANDIE Online 🧠");

    // ORB INTERACTION
    const orb = document.querySelector(".orb"); // adjust selector if needed
    if (orb) {
        orb.addEventListener("click", async () => {
            console.log("Orb activated");

            orb.classList.add("active");

            const res = await fetch("http://localhost:8000/chat", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message: "status check" })
            });

            const data = await res.json();
            console.log("ANDIE:", data);

            orb.classList.remove("active");
        });
    }

    // SECURITY SCAN BUTTON
    const scanBtn = document.querySelector("#run-scan");

    if (scanBtn) {
        scanBtn.addEventListener("click", async () => {
            console.log("Running security scan...");

            const res = await fetch("http://localhost:8000/agents/run", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ agent: "security" })
            });

            const data = await res.json();
            console.log("Scan result:", data);
        });
    }
    // CRYPTONIA AGENT CONTROLS
    const cryptoniaStatus = document.getElementById("cryptonia-status");
    const cryptoniaStart = document.getElementById("cryptonia-start");
    const cryptoniaStop = document.getElementById("cryptonia-stop");

    async function updateCryptoniaStatus() {
        try {
            const res = await fetch("http://localhost:8000/agents/cryptonia/status");
            const data = await res.json();
            cryptoniaStatus.textContent = data.status || "Unknown";
        } catch (e) {
            cryptoniaStatus.textContent = "Error";
        }
    }

    if (cryptoniaStart) {
        cryptoniaStart.addEventListener("click", async () => {
            cryptoniaStatus.textContent = "Starting...";
            await fetch("http://localhost:8000/agents/cryptonia/start", { method: "POST" });
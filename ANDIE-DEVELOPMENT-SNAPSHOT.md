# ANDIE Development Snapshot

This document describes how to run, test, and extend the ANDIE Autonomous Intelligence Environment in active development mode.

## Quick Start

1. **Backend**
   - Start the FastAPI server:
     ```sh
     uvicorn ANDIE.api.main:app --reload
     ```
   - Endpoints:
     - `/ws/chat` (WebSocket): Real-time chat/voice
     - `/status`, `/metrics`, `/sentinel`, `/memory`, `/intent` (REST): System, agents, security, memory, voice

2. **Frontend**
   - Use the provided `dashboard.html` or your Figma-exported UI as a base.
   - Connect UI elements to backend endpoints:
     - Chat orb → `/ws/chat` (WebSocket)
     - Voice orb → `/intent` (POST)
     - Status, agents, security, tasks → `/status`, `/memory`, `/sentinel`, etc.
   - Example JS for chat:
     ```js
     const ws = new WebSocket('ws://127.0.0.1:8000/ws/chat');
     ws.onmessage = (event) => { /* handle streamed chat */ };
     ws.send(JSON.stringify({type: 'CHAT', message: 'Hello ANDIE!'}));
     ```

3. **Development Workflow**
   - Edit backend Python or frontend HTML/JS/React as needed.
   - Hot-reload enabled for backend (`--reload`).
   - Use browser dev tools for frontend debugging.

4. **Integrating Figma UI**
   - Export Figma as HTML/CSS/JS or React.
   - Place in your project (e.g., `frontend/` or root).
   - Wire up API calls as above.

5. **Testing**
   - Use browser for frontend.
   - Use WebSocket tools (Postman, Insomnia) for chat API.
   - Run backend tests with `pytest` or custom scripts.

## Tips
- Keep this snapshot up to date as you add features.
- Document new endpoints and UI wiring here.
- For deployment, see `DEPLOYMENT.md` (create when ready).

---

**Happy hacking with ANDIE!**

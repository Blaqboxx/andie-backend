# Runtime Governance Architecture

## Overview

ANDIE's runtime follows **startup-hook ownership** governance model. All stateful services are initialized during application startup, not at module load time. This enables:

- **Deterministic startup sequences**
- **Testable state isolation**
- **Observable lifecycle management**
- **Distributed deployment clarity**
- **Clean recovery semantics**

**Effective Since:** v0.3-runtime-hardening  
**Reference Implementation:** [MemoryService](./andie/memory/memory_service.py) + [PR #2](https://github.com/Blaqboxx/andie-backend/pull/2)

---

## Pattern 1: Startup-Hook Ownership

### The Problem (Anti-Pattern)

```python
# ❌ DON'T: Module-level singleton
from andie.memory import MemoryService

memory_service = MemoryService()  # Runs at import time!

@app.get("/memory/query")
def query(prompt: str):
    return memory_service.query(prompt)
```

**Why this fails:**
- Initialization order is implicit and fragile
- Import-time execution means early failure is cryptic
- No lifecycle owner (who's responsible for cleanup?)
- Impossible to test in isolation
- Distributed systems have no state ownership semantics

### The Solution (Canonical Pattern)

```python
# ✅ DO: Startup-hook ownership
from fastapi import FastAPI, Request, HTTPException

app = FastAPI()

@app.on_event("startup")
def startup():
    """Initialize all stateful services during app startup."""
    app.state.memory_service = MemoryService()

def _memory_from_request(request: Request) -> MemoryService:
    """Extract service from request context, fail explicitly if missing."""
    service = getattr(request.app.state, 'memory_service', None)
    if not service:
        raise HTTPException(503, "Memory service not ready during startup")
    return service

@app.get("/memory/query")
async def query(request: Request, prompt: str):
    """Request-scoped DI: get service from app state."""
    memory = _memory_from_request(request)
    return await memory.query(prompt)
```

**Why this works:**
- Single, explicit initialization point (under app control)
- FastAPI manages lifecycle
- Clear ownership: "app owns MemoryService"
- Testable: fixtures can mock `request.app.state` before each test
- Distributed: state ownership per instance is obvious

---

## Pattern 2: Request-Scoped Dependency Injection

### The Problem (Anti-Pattern)

```python
# ❌ DON'T: Implicit global access
memory_service = MemoryService()  # Global!

@app.get("/memory/query")
def query(prompt: str):
    return memory_service.query(prompt)  # Where did this come from?
```

**Why this fails:**
- No explicit dependency declaration
- Impossible to trace in distributed scenarios
- Testing requires patching global state
- Multiple instances have no isolation

### The Solution (Canonical Pattern)

```python
# ✅ DO: Request-scoped DI with explicit extraction
from fastapi import Request, HTTPException

def _memory_from_request(request: Request) -> MemoryService:
    """
    Extract MemoryService from request context.
    
    Raises HTTPException(503) if not available (startup failed).
    This explicit pattern makes distributed state ownership clear.
    """
    service = getattr(request.app.state, 'memory_service', None)
    if not service:
        raise HTTPException(
            status_code=503,
            detail="Memory service unavailable (startup incomplete)"
        )
    return service

@app.get("/memory/query")
async def query(request: Request, prompt: str):
    """Request explicitly declares its dependency on MemoryService."""
    memory = _memory_from_request(request)
    result = await memory.query(prompt)
    return {"query": prompt, "result": result}
```

**Why this works:**
- Dependency is **explicit in function signature** (request parameter)
- Extraction is **visible and traceable**
- Failure is **explicit** (503 Service Unavailable, not None errors)
- Testable: tests can set `request.app.state.memory_service = mock_service`
- Distributed clarity: each instance has its own state

---

## Pattern 3: Idempotent Initialization Guard

### The Problem (Anti-Pattern)

```python
# ❌ DON'T: Implicit re-initialization
class MemoryService:
    def __init__(self):
        self.memory = []
        self.vectors = []
        # Implicit initialization — happens every time!
```

**Why this fails:**
- If MemoryService is imported twice, __init__ runs twice
- State gets reset unexpectedly
- Silent duplication is extremely hard to debug
- Distributed systems may instantiate multiple times

### The Solution (Canonical Pattern)

```python
# ✅ DO: Idempotent initialization with double-check locking
from threading import Lock

class MemoryService:
    def __init__(self):
        self._init_lock = Lock()
        self.initialized = False
        self.memory = None
        self.vectors = None
        # Call initialize to set up state safely
        self.initialize()
    
    def initialize(self):
        """
        Idempotent initialization guard.
        Safe to call multiple times — only initializes once.
        Uses double-check locking for thread safety.
        """
        # First check (fast, read-only)
        if self.initialized:
            return
        
        # Acquire lock for critical section
        with self._init_lock:
            # Second check (safe, within lock)
            if self.initialized:
                return
            
            # Perform initialization
            self.memory = []
            self.vectors = {}
            self._load_from_storage()
            
            # Mark as initialized
            self.initialized = True
    
    def store_memory(self, item):
        """Defensive initialization on first use."""
        self.initialize()  # No-op if already initialized
        self.memory.append(item)
    
    def query_memory(self, prompt):
        """Defensive initialization on first use."""
        self.initialize()  # No-op if already initialized
        return self._semantic_search(prompt)
```

**Why this works:**
- Calling `initialize()` multiple times is safe (idempotent)
- First call performs setup, subsequent calls are no-ops
- Thread-safe via `Lock` and double-check pattern
- Defensive calls in public methods ensure initialization before use
- Distributed systems can safely instantiate multiple services

---

## Complete Service Example

```python
# ✅ Complete example following all patterns

from fastapi import FastAPI, Request, HTTPException
from threading import Lock
import logging

logger = logging.getLogger(__name__)

class SemanticCognitionService:
    """
    Example service implementing runtime governance patterns.
    
    - Startup-hook owned
    - Idempotent initialization
    - Request-scoped DI
    """
    
    def __init__(self):
        """Initialize service state (lazy, defensive)."""
        self._init_lock = Lock()
        self.initialized = False
        self.embeddings = {}
        self.reasoning_cache = {}
    
    def initialize(self):
        """Idempotent initialization guard."""
        if self.initialized:
            return
        
        with self._init_lock:
            if self.initialized:
                return
            
            logger.info("SemanticCognition: Initializing embeddings...")
            # Load pre-trained models, semantic indices, etc.
            self._load_embeddings()
            self._rebuild_reasoning_cache()
            
            self.initialized = True
            logger.info("SemanticCognition: Initialization complete")
    
    async def reason(self, prompt: str, context: dict):
        """Defensive initialization on use."""
        self.initialize()
        # Perform reasoning...
        return {"reasoning": "...", "confidence": 0.95}

# Application setup
app = FastAPI()

@app.on_event("startup")
def startup():
    """Startup hook: Initialize all stateful services."""
    logger.info("App startup: Initializing services...")
    app.state.cognition_service = SemanticCognitionService()
    logger.info("App startup: All services ready")

def _cognition_from_request(request: Request) -> SemanticCognitionService:
    """Extract CognitionService with explicit failure semantics."""
    service = getattr(request.app.state, 'cognition_service', None)
    if not service:
        raise HTTPException(503, "Cognition service not ready")
    return service

@app.post("/reasoning")
async def reasoning_endpoint(request: Request, prompt: str):
    """Request-scoped DI: get service from app state."""
    cognition = _cognition_from_request(request)
    result = await cognition.reason(prompt, context={})
    return result
```

---

## Testing Patterns

### Fixture-Based Isolation

```python
# ✅ DO: Isolated app state per test
import pytest
from fastapi.testclient import TestClient

@pytest.fixture
def mock_cognition_service():
    """Mock service for testing."""
    service = MagicMock(spec=SemanticCognitionService)
    service.reason = AsyncMock(return_value={"reasoning": "test", "confidence": 0.99})
    return service

@pytest.fixture
def test_app(mock_cognition_service):
    """Create test app with mocked service."""
    app = FastAPI()
    
    @app.on_event("startup")
    def startup():
        app.state.cognition_service = mock_cognition_service
    
    return app

def test_reasoning_endpoint(test_app, mock_cognition_service):
    """Test endpoint with isolated, mocked service."""
    client = TestClient(test_app)
    response = client.post("/reasoning", json={"prompt": "test"})
    assert response.status_code == 200
    mock_cognition_service.reason.assert_called()
```

### Determinism Validation

```python
# ✅ DO: Verify deterministic startup
def test_startup_determinism():
    """Verify /healthz consistency across restart cycles."""
    results = []
    
    for cycle in range(3):
        app = create_app()  # Fresh app instance
        client = TestClient(app)
        healthz = client.get("/healthz").json()
        results.append(healthz)
    
    # All 3 runs must be identical
    assert results[0] == results[1] == results[2]
    assert results[0]["memory_ready"] == True
    assert results[0]["api_ready"] == True
```

---

## Deployment Checklist

**Before deploying a new service following these patterns:**

- [ ] Service has idempotent `initialize()` method
- [ ] Service is instantiated in `@app.on_event("startup")` hook
- [ ] All endpoints accept `request: Request` parameter
- [ ] `_service_from_request()` helper implemented with 503 fallback
- [ ] All endpoints use the DI helper
- [ ] Tests use fixtures to mock `request.app.state`
- [ ] Startup determinism test added to test suite
- [ ] No module-level service instances remain
- [ ] `CONTRIBUTING.md` updated with pattern reference

---

## Roadmap

**v0.3-runtime-hardening** ✅  
- MemoryService lifecycle governance  
- Startup-hook ownership  
- Request-scoped DI  
- Idempotent initialization

**v0.4-agent-orchestration** (Planned)  
- Agent runtime service  
- Orchestration coordinator  
- Multi-agent state management

**v0.5-distributed-cognition** (Planned)  
- Cross-instance semantic sharing  
- Distributed state ownership  
- Gossip protocol for state sync

All future services must follow v0.3 runtime governance patterns.


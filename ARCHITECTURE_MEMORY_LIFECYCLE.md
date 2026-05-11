# MemoryService Lifecycle Architecture

**Checkpoint**: `v0.3-runtime-hardening` (feature/runtime-memory-consolidation)  
**Date**: May 2026  
**Purpose**: Document canonical lifecycle ownership, deterministic startup, and core patterns for MemoryService

---

## Executive Summary

This document captures the architectural decisions that made ANDIE's runtime lifecycle **deterministic, observable, and lifecycle-stable**. This is foundational for distributed cognition, orchestration, and autonomous services.

---

## Core Pattern: Startup-Owned Initialization

### The Problem (Before)
- Multiple `MemoryService()` constructors scattered across entrypoints
- Module-level singletons created at import time (race conditions, side effects)
- Non-deterministic initialization order
- Duplicate state initialization on framework restart

### The Solution (After)

**Single Lifecycle Authority**: FastAPI startup hook

```python
@app.on_event("startup")
async def _startup_self_build_loop() -> None:
    app.state.memory_service = MemoryService()  # ONE place
    # ... rest of startup
```

**Why**:
1. **Determinism**: Startup is a known, sequenced event
2. **Observability**: Can add logging, tracing, metrics
3. **Testability**: Can mock/replace before app starts
4. **Recovery**: Can re-initialize in error cases
5. **Scaling**: Microservices can coordinate startup order

---

## Core Pattern: Request-Scoped Dependency Injection

### The Problem (Before)
- Endpoints directly accessed module-level `memory_service` singleton
- No way to inject test doubles
- Hard to reason about runtime state

### The Solution (After)

**DI Helper**:
```python
def _memory_from_request(request: Request) -> MemoryService:
    service = getattr(request.app.state, "memory_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="memory_service_unavailable")
    return service
```

**Usage**:
```python
@app.post("/memory/query")
def query_memory(req: QueryRequest, request: Request):
    memory_service = _memory_from_request(request)
    return memory_service.query_memory(req.query, top_k=req.top_k)
```

**Why**:
1. **Explicit**: Where memory_service comes from is obvious
2. **Testable**: Inject mock via request context
3. **Failsafe**: Returns 503 if not initialized
4. **Debuggable**: Can trace lifecycle issues

---

## Core Pattern: Idempotent Initialization Guard

### The Problem (Before)
- No guard against double-initialization
- If startup hook called twice, would re-seed vectors, duplicate entries

### The Solution (After)

**Double-Check Lock in MemoryService**:
```python
class MemoryService:
    def __init__(self):
        self._init_lock = Lock()
        self.initialized = False
        self.memory = []
        self.initialize()

    def initialize(self):
        # Idempotent: second call is no-op
        if self.initialized:
            return
        with self._init_lock:
            if self.initialized:
                return
            # Initialization code here
            self.initialized = True
```

**Why**:
1. **Safe**: Multiple calls guaranteed safe
2. **Fast**: After first init, subsequent calls are instant (early return)
3. **Thread-safe**: Lock protects concurrent initialization attempts
4. **Predictable**: State can only be initialized once per instance

---

## Verification: 3-Cycle Determinism Test

**Test Protocol**:
1. Boot API (Run 1)
2. Sample `/healthz` → Capture `memory_ready=true`
3. Shutdown cleanly
4. Boot API (Run 2)
5. Sample `/healthz` → Capture `memory_ready=true`
6. Repeat (Run 3)
7. Diff all three outputs

**Result**:
```
✅ Runs 1 & 2 IDENTICAL
✅ Runs 1 & 3 IDENTICAL
```

**Telemetry**: All three restarts returned:
```json
{
  "api_ready": true,
  "memory_ready": true,
  "vector_ready": true,
  "ollama_ready": true,
  "redis_ready": true,
  "qdrant_ready": true,
  "degraded_mode": false
}
```

**Conclusion**: Lifecycle is deterministic, no duplicate initialization side effects detected.

---

## Surgical Scope

Only 4 files modified (no unrelated churn):
- `andie/memory/memory_service.py` - Added idempotence guard
- `interfaces/api/main.py` - Removed singleton, added startup hook + DI
- `interfaces/api/memory_api.py` - Removed singleton, added startup hook + DI
- `main.py` - Removed singleton, added startup hook + DI

---

## Canonical Patterns (Moving Forward)

### ✅ DO: New services should follow this pattern
```python
# 1. Define service class with idempotent initialize()
class MyService:
    def __init__(self):
        self._init_lock = Lock()
        self.initialized = False
    
    def initialize(self):
        if self.initialized:
            return
        with self._init_lock:
            if self.initialized:
                return
            # ... setup code ...
            self.initialized = True

# 2. Register in app startup
@app.on_event("startup")
async def startup():
    app.state.my_service = MyService()

# 3. Access via DI helper
def _my_service_from_request(request: Request) -> MyService:
    service = getattr(request.app.state, "my_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="service_unavailable")
    return service

@app.post("/endpoint")
def handler(request: Request):
    svc = _my_service_from_request(request)
    return svc.do_work()
```

### ❌ DON'T: Avoid these anti-patterns
- Module-level singleton constructors (`service = MyService()` at top level)
- Lazy initialization in endpoint handlers
- Direct attribute access to `app.state` without helpers
- Multiple initialization paths (one source of truth)

---

## Next Phases

### Phase 1: Agent Runtime (Foundation Ready)
- Agents can now reliably access memory via startup-controlled instance
- Can add telemetry/tracing to startup hook
- Can implement recovery logic if memory initialization fails

### Phase 2: Orchestration & Distribution
- Multiple nodes can coordinate on memory_service readiness via healthz
- Can implement leader election based on memory initialization status
- Nodes can join/leave cluster predictably

### Phase 3: Persistence & Recall
- Build on deterministic lifecycle to implement durable memory
- No race conditions during state machine transitions
- Can replay startup sequence for debugging

### Phase 4: Autonomy & Recovery
- Agents can self-heal by requesting memory re-initialization
- Can run diagnostic checks after startup
- Can validate memory state against expected schema

---

## References

- **Commit**: `feature/runtime-memory-consolidation`
- **Milestone**: `v0.3-runtime-hardening`
- **Tags**: `runtime-determinism-baseline`
- **Related**: Double-check locking pattern, startup event handlers (FastAPI), dependency injection

---

## Archive Notes

This checkpoint represents the moment ANDIE transitioned from **feature delivery** to **runtime governance**. 

The next focus shifts to:
1. **Agent autonomy** - agents can trust memory state
2. **Orchestration** - distributed coordination becomes possible
3. **Observability** - lifecycle telemetry enables debugging
4. **Resilience** - predictable startup enables recovery patterns

The foundation is set. Next phase: build agents on top.

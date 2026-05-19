"""
Task Classification and Intent Detection (Phase 1 Foundation)

This module provides task type inference and intent detection.
Currently keyword-based; can evolve to ML-based classification in Phase 2.

Provides:
  - Task type inference from text
  - Intent detection
  - Complexity scoring
  - Actor role inference
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Set

from .model_router import TaskType


@dataclass
class ClassificationResult:
    """Result of task classification."""
    primary_task_type: TaskType
    confidence: float  # 0.0-1.0
    secondary_task_types: List[TaskType]
    complexity_score: int  # 0-10
    estimated_tokens: int
    requires_governance: bool
    suggested_role: str


class TaskClassifier:
    """
    Task type inference and intent detection.
    
    Centralizes:
      - Keyword-based task detection
      - Complexity scoring
      - Intent inference
      - Role inference
    """
    
    # Keyword maps for task types
    ARCHITECTURE_KEYWORDS = {
        "arch", "architecture", "design", "structure", "blueprint",
        "refactor", "refactoring", "optimize", "optimization", "pattern",
        "framework", "layer", "module", "system", "component", "interface"
    }
    
    CODING_KEYWORDS = {
        "code", "write", "generate", "implement", "create", "build",
        "function", "class", "method", "module", "library", "script",
        "python", "javascript", "typescript", "rust", "go", "java"
    }
    
    DEBUGGING_KEYWORDS = {
        "debug", "fix", "trace", "error", "bug", "crash", "fail",
        "exception", "error log", "stack trace", "breakpoint", "issue"
    }
    
    TERMINAL_KEYWORDS = {
        "terminal", "shell", "bash", "command", "cmd", "execute",
        "run", "script", "cli", "command line", "console"
    }
    
    BUILD_KEYWORDS = {
        "build", "compile", "package", "deploy", "release", "bundle",
        "docker", "container", "docker build", "make", "gradle", "npm run"
    }
    
    PLANNING_KEYWORDS = {
        "plan", "schedule", "organize", "workflow", "pipeline", "task",
        "orchestrate", "coordinate", "manage", "timeline", "sequence"
    }
    
    REASONING_KEYWORDS = {
        "think", "reason", "analyze", "evaluate", "assess", "consider",
        "judge", "examine", "inspect", "review", "compare"
    }
    
    SECURITY_KEYWORDS = {
        "security", "sentinel", "guard", "check", "verify", "validate",
        "audit", "permission", "access", "privilege", "threat", "risk"
    }
    
    MEMORY_KEYWORDS = {
        "memory", "recall", "context", "search", "find", "retrieve",
        "lookup", "store", "save", "persist", "knowledge", "embedding"
    }
    
    PERSONALITY_KEYWORDS = {
        "personality", "role", "agent", "identity", "character", "style",
        "voice", "tone", "manner", "demeanor"
    }
    
    def __init__(self):
        self._keyword_map = {
            TaskType.ARCHITECTURE: self.ARCHITECTURE_KEYWORDS,
            TaskType.CODING: self.CODING_KEYWORDS,
            TaskType.DEBUGGING: self.DEBUGGING_KEYWORDS,
            TaskType.TERMINAL: self.TERMINAL_KEYWORDS,
            TaskType.BUILD: self.BUILD_KEYWORDS,
            TaskType.PLANNING: self.PLANNING_KEYWORDS,
            TaskType.REASONING: self.REASONING_KEYWORDS,
            TaskType.SECURITY: self.SECURITY_KEYWORDS,
            TaskType.MEMORY_RECALL: self.MEMORY_KEYWORDS,
            TaskType.PERSONALITY: self.PERSONALITY_KEYWORDS,
        }
    
    def classify(
        self,
        task_description: str,
        actor: str = "system",
    ) -> ClassificationResult:
        """
        Classify task and return detailed result.
        """
        text_lower = task_description.lower()
        words = set(text_lower.split())
        
        # Score each task type
        scores = {}
        for task_type, keywords in self._keyword_map.items():
            matches = len(words & keywords)
            score = matches / max(len(keywords), 1)
            scores[task_type] = score
        
        # Primary and secondary
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        primary = sorted_scores[0][0]
        primary_confidence = sorted_scores[0][1]
        secondary = [t for t, _ in sorted_scores[1:4] if t != TaskType.UNKNOWN]
        
        # Complexity estimation
        complexity = self._estimate_complexity(task_description, primary)
        
        # Token estimation
        tokens = len(text_lower.split()) * 2
        
        # Governance requirement
        requires_governance = primary in [
            TaskType.SECURITY,
            TaskType.ARCHITECTURE,
            TaskType.DEBUGGING,
        ]
        
        # Suggested role
        suggested_role = self._infer_role(primary, actor)
        
        return ClassificationResult(
            primary_task_type=primary,
            confidence=min(1.0, primary_confidence),
            secondary_task_types=secondary,
            complexity_score=complexity,
            estimated_tokens=tokens,
            requires_governance=requires_governance,
            suggested_role=suggested_role,
        )
    
    def _estimate_complexity(self, text: str, task_type: TaskType) -> int:
        """Estimate task complexity (0-10)."""
        complexity = 3  # base
        
        # Length indicator
        word_count = len(text.split())
        if word_count > 100:
            complexity += 2
        if word_count > 200:
            complexity += 2
        
        # Task type adjustments
        if task_type in [TaskType.ARCHITECTURE, TaskType.PLANNING]:
            complexity += 2
        elif task_type in [TaskType.DEBUGGING, TaskType.SECURITY]:
            complexity += 1
        
        # Keyword complexity indicators
        high_complexity_indicators = {"multi", "distributed", "concurrent", "async", "parallel"}
        if any(w in text.lower() for w in high_complexity_indicators):
            complexity += 2
        
        return min(10, complexity)
    
    def _infer_role(self, task_type: TaskType, default_actor: str) -> str:
        """Infer suggested role for task execution."""
        role_map = {
            TaskType.ARCHITECTURE: "architect",
            TaskType.CODING: "developer",
            TaskType.DEBUGGING: "debugger",
            TaskType.TERMINAL: "operator",
            TaskType.BUILD: "builder",
            TaskType.PLANNING: "orchestrator",
            TaskType.REASONING: "analyst",
            TaskType.SECURITY: "sentinel",
            TaskType.MEMORY_RECALL: "memory_agent",
            TaskType.PERSONALITY: "personality_agent",
            TaskType.ORCHESTRATION: "orchestrator",
        }
        return role_map.get(task_type, "operator")

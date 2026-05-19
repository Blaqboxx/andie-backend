"""
Agent Framework — Option C: Specialized Agents with Model Routing

Agents are task-specialized executors that leverage the model routing layer (Phase 1)
to select optimal inference targets. Each agent declares preferred model(s) and
delegates routing decisions to the centralized orchestrator.

Architecture:
- Agent base class: abstract interface for task execution
- Concrete agents: ArchitectAgent, TerminalAgent, SecurityAgent, MemoryAgent, PlanningAgent
- Model affinity: each agent has preferred_model and fallback_models
- Orchestration integration: agents call orchestrator.select_model() and execute_tool()
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4
import json


class AgentRole(Enum):
    """Agent role identifiers for tool governance."""
    ARCHITECT = "architect"
    TERMINAL = "operator"
    SECURITY = "sentinel"
    MEMORY = "memory_agent"
    PLANNING = "planner"
    GENERAL = "agent"


@dataclass
class AgentConfig:
    """Configuration for agent specialization."""
    name: str
    role: AgentRole
    preferred_model: str
    fallback_models: List[str] = field(default_factory=list)
    description: str = ""
    task_types: List[str] = field(default_factory=list)
    tools_enabled: List[str] = field(default_factory=list)
    timeout_seconds: float = 5.0


@dataclass
class ExecutionContext:
    """Context for a single agent execution."""
    execution_id: str
    agent_name: str
    task_description: str
    task_type: str
    selected_model: str
    routing_decision_id: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    duration_ms: int = 0


class Agent(ABC):
    """
    Abstract base class for specialized agents.
    
    Each agent:
    1. Declares preferred model(s) via preferred_model
    2. Inherits routing logic from base class
    3. Implements execute() for task-specific logic
    4. Integrates with orchestrator for tool execution
    """

    def __init__(self, config: AgentConfig, orchestrator):
        """
        Initialize agent with configuration and orchestrator reference.
        
        Args:
            config: AgentConfig with specialization parameters
            orchestrator: OrchestratorRuntime instance for routing and tools
        """
        self.config = config
        self.orchestrator = orchestrator
        self._execution_history: List[ExecutionContext] = []
        self._max_history = 100

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} role={self.config.role.value} model={self.config.preferred_model}>"

    @abstractmethod
    async def execute(self, task_description: str) -> ExecutionContext:
        """
        Execute task with model routing and tool integration.
        
        Args:
            task_description: Natural language task description
            
        Returns:
            ExecutionContext with result or error
        """
        pass

    def _select_model(self, task_description: str, task_type: Optional[str] = None) -> Dict[str, Any]:
        """
        Select model via orchestrator routing layer.
        
        Args:
            task_description: Task description for routing
            task_type: Optional explicit task type
            
        Returns:
            Routing decision with selected_model, reason, alternatives, confidence
        """
        selection = self.orchestrator.select_model(
            task_type=task_type or self.config.task_types[0] if self.config.task_types else "unknown",
            task_description=task_description,
            actor=self.config.role.value,
            correlation_id=str(uuid4()),
        )
        return selection

    def _record_execution(self, context: ExecutionContext) -> None:
        """Record execution in history (ring buffer)."""
        self._execution_history.append(context)
        if len(self._execution_history) > self._max_history:
            self._execution_history.pop(0)

    def get_history(self, limit: int = 10) -> List[ExecutionContext]:
        """Get recent execution history."""
        return self._execution_history[-limit:]


class ArchitectAgent(Agent):
    """
    Architectural specialist using DeepSeek-Coder.
    
    Preferred tasks: architecture, design, refactoring, pattern analysis
    """

    def __init__(self, orchestrator):
        config = AgentConfig(
            name="architect",
            role=AgentRole.ARCHITECT,
            preferred_model="mistral:latest",
            fallback_models=["mistral:latest", "phi3:mini"],
            description="Architecture, design patterns, refactoring analysis",
            task_types=["architecture", "coding", "debugging", "refactoring"],
            tools_enabled=["echo"],
            timeout_seconds=10.0,
        )
        super().__init__(config, orchestrator)

    async def execute(self, task_description: str) -> ExecutionContext:
        """Execute architecture/design task with DeepSeek routing preference."""
        execution_id = str(uuid4())
        context = ExecutionContext(
            execution_id=execution_id,
            agent_name=self.config.name,
            task_description=task_description,
            task_type="architecture",
            selected_model="pending",
        )

        try:
            # Route to model (will prefer DeepSeek-Coder)
            selection = self._select_model(task_description, "architecture")
            context.selected_model = selection["model"]
            context.routing_decision_id = selection.get("decision_id")

            # Simulate architecture analysis result
            context.result = {
                "model": context.selected_model,
                "analysis": f"Architectural analysis: {task_description[:100]}...",
                "confidence": selection.get("confidence", 0.0),
                "tool_invocations": 0,
            }
            context.duration_ms = 100

        except Exception as e:
            context.error = f"ArchitectAgent execution failed: {str(e)}"

        self._record_execution(context)
        return context


class TerminalAgent(Agent):
    """
    Terminal/shell specialist using Phi-3 Mini.
    
    Preferred tasks: shell commands, terminal operations, build commands
    """

    def __init__(self, orchestrator):
        config = AgentConfig(
            name="terminal",
            role=AgentRole.TERMINAL,
            preferred_model="phi3:mini",
            fallback_models=["mistral:latest"],
            description="Terminal, shell, build commands",
            task_types=["terminal", "shell_command", "build"],
            tools_enabled=["echo", "sleep"],
            timeout_seconds=5.0,
        )
        super().__init__(config, orchestrator)

    async def execute(self, task_description: str) -> ExecutionContext:
        """Execute terminal task with Phi-3 routing preference."""
        execution_id = str(uuid4())
        context = ExecutionContext(
            execution_id=execution_id,
            agent_name=self.config.name,
            task_description=task_description,
            task_type="terminal",
            selected_model="pending",
        )

        try:
            # Route to model (will prefer Phi-3 Mini)
            selection = self._select_model(task_description, "terminal")
            context.selected_model = selection["model"]
            context.routing_decision_id = selection.get("decision_id")

            # Execute via echo tool as proof-of-concept
            tool_result = self.orchestrator.execute_tool(
                tool_name="echo",
                payload={"msg": f"terminal: {task_description[:50]}"},
                actor=self.config.role.value,
                role=self.config.role.value,
                timeout_seconds=self.config.timeout_seconds,
            )

            context.result = {
                "model": context.selected_model,
                "command": task_description,
                "tool_execution": tool_result.get("status"),
                "confidence": selection.get("confidence", 0.0),
                "tool_invocations": 1,
            }
            context.duration_ms = tool_result.get("duration_ms", 0) + 50

        except Exception as e:
            context.error = f"TerminalAgent execution failed: {str(e)}"

        self._record_execution(context)
        return context


class SecurityAgent(Agent):
    """
    Security/governance specialist.
    
    Preferred tasks: security analysis, permission validation, threat modeling
    """

    def __init__(self, orchestrator):
        config = AgentConfig(
            name="security",
            role=AgentRole.SECURITY,
            preferred_model="mistral:latest",
            fallback_models=["mistral:latest"],
            description="Security analysis, permissions, threat modeling",
            task_types=["security"],
            tools_enabled=["echo"],
            timeout_seconds=8.0,
        )
        super().__init__(config, orchestrator)

    async def execute(self, task_description: str) -> ExecutionContext:
        """Execute security task with Mistral routing preference."""
        execution_id = str(uuid4())
        context = ExecutionContext(
            execution_id=execution_id,
            agent_name=self.config.name,
            task_description=task_description,
            task_type="security",
            selected_model="pending",
        )

        try:
            # Route to model
            selection = self._select_model(task_description, "security")
            context.selected_model = selection["model"]
            context.routing_decision_id = selection.get("decision_id")

            context.result = {
                "model": context.selected_model,
                "security_analysis": f"Security review: {task_description[:100]}...",
                "threat_level": "low",
                "confidence": selection.get("confidence", 0.0),
                "tool_invocations": 0,
            }
            context.duration_ms = 150

        except Exception as e:
            context.error = f"SecurityAgent execution failed: {str(e)}"

        self._record_execution(context)
        return context


class MemoryAgent(Agent):
    """
    Memory/knowledge specialist using Nomic Embed.
    
    Preferred tasks: memory recall, vector operations, knowledge retrieval
    """

    def __init__(self, orchestrator):
        config = AgentConfig(
            name="memory",
            role=AgentRole.MEMORY,
            preferred_model="nomic-embed-text:latest",
            fallback_models=["mistral:latest"],
            description="Memory recall, knowledge retrieval, vector operations",
            task_types=["memory_recall"],
            tools_enabled=["echo"],
            timeout_seconds=3.0,
        )
        super().__init__(config, orchestrator)

    async def execute(self, task_description: str) -> ExecutionContext:
        """Execute memory task with Nomic Embed routing preference."""
        execution_id = str(uuid4())
        context = ExecutionContext(
            execution_id=execution_id,
            agent_name=self.config.name,
            task_description=task_description,
            task_type="memory_recall",
            selected_model="pending",
        )

        try:
            # Route to model
            selection = self._select_model(task_description, "memory_recall")
            context.selected_model = selection["model"]
            context.routing_decision_id = selection.get("decision_id")

            context.result = {
                "model": context.selected_model,
                "memory_query": task_description,
                "results_count": 3,
                "confidence": selection.get("confidence", 0.0),
                "tool_invocations": 0,
            }
            context.duration_ms = 75

        except Exception as e:
            context.error = f"MemoryAgent execution failed: {str(e)}"

        self._record_execution(context)
        return context


class PlanningAgent(Agent):
    """
    Planning/orchestration specialist using Mistral.
    
    Preferred tasks: planning, reasoning, orchestration decisions
    """

    def __init__(self, orchestrator):
        config = AgentConfig(
            name="planner",
            role=AgentRole.PLANNING,
            preferred_model="mistral:latest",
            fallback_models=["mistral:latest"],
            description="Planning, reasoning, orchestration decisions",
            task_types=["planning", "reasoning", "orchestration"],
            tools_enabled=["echo"],
            timeout_seconds=8.0,
        )
        super().__init__(config, orchestrator)

    async def execute(self, task_description: str) -> ExecutionContext:
        """Execute planning task with Mistral routing preference."""
        execution_id = str(uuid4())
        context = ExecutionContext(
            execution_id=execution_id,
            agent_name=self.config.name,
            task_description=task_description,
            task_type="planning",
            selected_model="pending",
        )

        try:
            # Route to model
            selection = self._select_model(task_description, "planning")
            context.selected_model = selection["model"]
            context.routing_decision_id = selection.get("decision_id")

            context.result = {
                "model": context.selected_model,
                "plan": f"Plan for: {task_description[:100]}...",
                "steps": 3,
                "confidence": selection.get("confidence", 0.0),
                "tool_invocations": 0,
            }
            context.duration_ms = 120

        except Exception as e:
            context.error = f"PlanningAgent execution failed: {str(e)}"

        self._record_execution(context)
        return context


# Agent factory for convenient creation
class AgentFactory:
    """Factory for creating and managing agent instances."""

    @staticmethod
    def create_architect(orchestrator) -> ArchitectAgent:
        return ArchitectAgent(orchestrator)

    @staticmethod
    def create_terminal(orchestrator) -> TerminalAgent:
        return TerminalAgent(orchestrator)

    @staticmethod
    def create_security(orchestrator) -> SecurityAgent:
        return SecurityAgent(orchestrator)

    @staticmethod
    def create_memory(orchestrator) -> MemoryAgent:
        return MemoryAgent(orchestrator)

    @staticmethod
    def create_planner(orchestrator) -> PlanningAgent:
        return PlanningAgent(orchestrator)

    @staticmethod
    def create_all(orchestrator) -> Dict[str, Agent]:
        """Create all specialized agents."""
        return {
            "architect": AgentFactory.create_architect(orchestrator),
            "terminal": AgentFactory.create_terminal(orchestrator),
            "security": AgentFactory.create_security(orchestrator),
            "memory": AgentFactory.create_memory(orchestrator),
            "planner": AgentFactory.create_planner(orchestrator),
        }

# Executive Architecture

The ExecutiveController is the single authority for mission, goal, task, and governed world transitions.

## Responsibilities
- Coordinate mission and goal lifecycle.
- Generate and dispatch plans through the planner and dispatcher.
- Enforce proposal review and execution gates for world mutation.
- Record cycle audits for budget and governance observability.

## Non-goals
- No direct institution world mutation.
- No bypass of identity checks.

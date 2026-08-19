# Query workbench design decision

The prototype compared chat-first, analysis-workbench, and result-first layouts. The
analysis workbench won because QueryPilot's differentiator is the controlled LangGraph
trajectory: users can inspect retrieval evidence, the generated AQL, safety preparation,
and real rows without leaving the primary query workflow. On narrow screens the same
information becomes one ordered column instead of disappearing behind a separate route.

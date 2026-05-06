## [Unreleased]

### 2026-04-02
- Added: Data plane middleware (src/tools/data_plane.py) — intercepts large binary payloads
  (base64 meshes, STEP files) in tool responses, stores them in DesignState.data_store,
  and replaces them with lightweight references. Resolves references back to payloads on
  tool request. Prevents LLM context overflow from 100KB+ base64 mesh data.
- Added: DesignState.data_store field for large inter-MCP payloads (data plane)
- Added: Type coercion middleware (src/tools/type_coercion.py) — fixes mcpadapt bug that
  collapses anyOf types to "string", causing LLMs to send "null" instead of null and
  JSON strings instead of dicts. Coerces argument types before MCP calls.
- Added: LiteLLM backend support in model_loader.py for cloud API models (Claude, GPT, etc.)
- Added: mdo_f25_sequential_iterative_feedback combination in batch_runner.py
- Added: mdo_f25_run_claude.yaml config for Claude Sonnet 4 via LiteLLM
- Changed: Tool loading pipeline now applies full middleware stack (type coercion +
  data plane) to all MCP tools via apply_coercion_to_tools()

### 2026-03-29
- Added: DesignState class for centralized multi-MCP session/result/constraint tracking (src/coordination/design_state.py)
- Added: Multi-MCP connection support with per-server naming, tool routing, and graceful degradation (src/tools/mcp_connector.py)
- Added: Server-scoped tool patterns (e.g. "tigl.*", "su2.run_su2_solver") in pipeline templates
- Added: SkillLoader for LLM-agnostic skill injection — Mode A (Claude API) and Mode B (prompt injection) (src/skills/skill_loader.py)
- Added: MCPServerConfig.name field for named multi-server configs
- Added: SkillsConfig dataclass and AppConfig.skills field
- Added: Per-server environment variable URL overrides (MAS_AVIARY_TIGL_URL, etc.)
- Added: Aircraft Design MDO Skill (skills/aircraft-design-mdo/)
  - SKILL.md: Pipeline overview, discipline roles, tool sequences, DLR-F25 constraints, convergence criteria
  - references/tool_catalog.md: 54 tools across 5 MCPs discovered via live MCP tool listing
  - references/data_flow.md: DesignState schema and inter-MCP data transfers
  - references/f25_constraints.md: Full DLR-F25 optimization formulation with fidelity assessments
  - references/discipline_tradeoffs.md: 7 cross-disciplinary coupling descriptions
  - references/design_variables.md: Complete variable catalog across all 5 MCPs
  - scripts/validate_design_state.py: DesignState consistency checker
- Added: MDO F25 pipeline template YAMLs for all 4 organizational structures
  - config/mdo_f25_sequential_agents.yaml: 7-stage sequential pipeline (TiGL→SU2→Mass→PyCycle→Aviary)
  - config/aviary_mdo_f25_sequential.yaml: Sequential strategy config
  - config/mdo_f25_orchestrated_agents.yaml: Orchestrated config with 7 required tool phases
  - config/mdo_f25_networked_agents.yaml: Networked config with 7 workflow phases
  - config/mdo_f25_graph.yaml: Graph-routed state machine with MDO iteration loop
  - config/mdo_f25_staged_pipeline.yaml: Staged pipeline with per-stage completion criteria
  - config/mdo_f25_run.yaml: Main entry point config for 5-MCP runs
- Added: Tests for DesignState (21 tests) and SkillLoader (17 tests)
- Changed: MCPConnector now gracefully degrades when individual servers fail (logs warning, continues with available servers)
- Changed: Updated test_connection_error_propagates to test_connection_error_graceful_degradation

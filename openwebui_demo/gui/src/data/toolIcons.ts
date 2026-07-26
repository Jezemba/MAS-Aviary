// Per-tool emoji for the tool-log rows (the `ic` column). Keyed by the REAL
// MCP tool names (from narration.py's TOOL_DESCRIPTIONS). Falls back to "•".
// The prototype keyed this off fictional names; these are the actual tools.

export const TOOL_ICONS: Record<string, string> = {
  // tigl-mcp
  open_cpacs: "📂",
  get_configuration_summary: "🔍",
  list_geometric_components: "🔍",
  get_wing_summary: "📏",
  get_fuselage_summary: "📏",
  get_high_level_parameters: "📋",
  set_high_level_parameters: "✏️",
  get_component_metadata: "📋",
  intersect_components: "✂️",
  intersect_with_plane: "✂️",
  sample_component_surface: "🎯",
  export_component_mesh: "📤",
  export_configuration_cad: "📤",
  generate_volume_mesh: "🧊",
  close_cpacs: "💾",
  // su2-mcp
  create_su2_session: "🆕",
  set_mesh: "📥",
  analyze_mesh: "🔍",
  generate_mesh_from_step: "🧊",
  generate_deformed_mesh: "🧊",
  update_config_entries: "⚙️",
  parse_config: "📋",
  get_config_text: "📋",
  get_valid_config_options: "📋",
  get_su2_status: "🩺",
  run_su2_solver: "🌬️",
  list_result_files: "📑",
  read_history_csv: "📈",
  sample_surface_solution: "🎯",
  get_result_file_base64: "📤",
  get_session_info: "ℹ️",
  // mass-mcp
  validate_cpacs_inputs: "🩺",
  estimate_mass: "⚖️",
  get_cpacs_mass_breakdown: "📋",
  // pycycle-mcp
  create_cycle_model: "🆕",
  list_variables: "📋",
  set_inputs: "⚙️",
  run_cycle: "🔥",
  get_outputs: "📈",
  get_cycle_summary: "📋",
  sweep_inputs: "🔁",
  compute_totals: "📐",
  close_cycle_model: "👋",
  // aviary-mcp
  get_design_space: "📋",
  create_session: "🆕",
  configure_mission: "🛣️",
  set_aircraft_parameters: "✏️",
  validate_parameters: "🩺",
  run_simulation: "🚀",
  get_results: "📈",
  get_trajectory: "📈",
  check_constraints: "✅",
  ping: "🩺",
  final_answer: "📝",
};

export function toolIcon(name: string): string {
  return TOOL_ICONS[name] ?? "•";
}

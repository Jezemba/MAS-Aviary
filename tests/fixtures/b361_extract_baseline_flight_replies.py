"""Extract real-shaped server replies of the recorded coupled baseline flight into a small test fixture.

Reads (read-only) ws8 /data/Jessica/final_fix/baseline2/results/flights/B2_coupled_anchor_2.json + its
knowledge_base.jsonl. The replies there are what the MIDDLEWARE returned (refs for large payloads, framework
annotations); this restores the payloads the data plane kept (flight 'store' snapshot) and drops the annotations,
so each reply is the server's shape. Writes the fixture to argv[2].
"""
import copy
import json
import sys
from pathlib import Path

flight_path = Path(sys.argv[1])
out_path = Path(sys.argv[2])
flight = json.loads(flight_path.read_text())
kb = [json.loads(line) for line in (flight_path.with_suffix("") / "knowledge_base.jsonl").read_text().splitlines()]
store = flight["store"]

ANNOTATIONS = ("geometry_authority", "framework_applied", "framework_kept", "study_controls", "framework_target_cl",
               "framework_note", "coupling_hint")


def restore(value):
    """Intercepted refs back to the payload the data plane kept (or a small placeholder when not kept)."""
    if isinstance(value, dict):
        if value.get("_intercepted") or (set(value) <= {"ref", "size_bytes"} and "ref" in value):
            ref = value.get("ref")
            if ref in store:
                return copy.deepcopy(store[ref])
            preview = value.get("preview") or value.get("keys_preview")
            return preview if isinstance(preview, list) else {}
        return {k: restore(v) for k, v in value.items() if k not in ANNOTATIONS and k != "_truncated_keys"}
    if isinstance(value, list):
        return [restore(v) for v in value]
    return value


calls = {}
hlp = {}
for c in flight["calls"]:
    if c["tool"] == "setup_session_with_params":
        calls["create_session"] = json.loads(c["out"]["create_session"])
        calls["configure_mission"] = json.loads(c["out"]["configure_mission"])
    elif c["tool"] == "get_high_level_parameters":
        hlp[c["args"]["component_uid"]] = restore(c["out"])
    else:
        calls[c["tool"]] = restore(c["out"])
calls["get_high_level_parameters"] = hlp
mb = calls["estimate_mass"]["mass_breakdown"]
mb["sizing_report"] = copy.deepcopy(store["estimate_mass__mass_breakdown.sizing_report"])
calls["generate_volume_mesh"].pop("mesh_base64", None)        # the fake generates a payload of its own
calls["create_cycle_model"]["top_promoted_inputs"] = [{"name": "Fn_DES"}, {"name": "fc.alt"}, {"name": "fc.MN"}]
calls["run_su2_solver"]["residual_history"] = []

# Framework-only calls (nested, not in the driver's own list): from the knowledge base, payloads restored.
kb_by_tool = {}
for e in kb:
    kb_by_tool.setdefault(e["tool"], []).append(e)
point = {k: v for k, v in store["aviary_cruise_point"].items() if not k.startswith("_")}
cp = restore(kb_by_tool["get_cruise_point"][0]["outputs"])
cp["cruise_point"] = point
cp["design_fingerprint"] = point["design_fingerprint"]
cp.setdefault("aero_details", {}).pop("fingerprint_covers", None)
calls["get_cruise_point"] = cp
calls["generate_engine_deck"] = restore(kb_by_tool["generate_engine_deck"][0]["outputs"])
calls["set_engine_deck"] = restore(kb_by_tool["set_engine_deck"][0]["outputs"])
calls["set_engine_deck"]["engine"]["computed_from_design"] = {}
# The server's set reply: "applied" as aviary reports it (name / old / new) for what the framework sent.
sap = calls["set_aircraft_parameters"]
sap["applied"] = [{"name": k, "old_value": None, "new_value": v} for k, v in store["design_params_applied"].items()
                  if k != "Aircraft.Wing.SPAN"]

fixture = {
    "_source": ("ws8 /data/Jessica/final_fix/baseline2/results/flights/B2_coupled_anchor_2.json (+ knowledge_base.jsonl):"
                " coupled baseline flight on the final code, 2026-11-18; replies restored to the server shape by "
                "scratchpad b360audit/extract_fixture.py (refs -> kept payloads, framework annotations dropped)"),
    "replies": calls,
    "recorded": {
        "aviary_session": flight["run_simulation"]["session_id"],
        "aviary_design_fingerprint": flight["run_simulation"]["flown_design_fingerprint"],
        "flown_parameters": store["design_params_applied"],
        "geometry_tails": store["geometry_tails"],
        "geometry_wing_tc": store["geometry_wing_tc"],
        "fn_des_lbf": flight["calls"][[c["tool"] for c in flight["calls"]].index("set_inputs")]["args"]["values"]["Fn_DES"],
        "pycycle_fn_lbf": flight["pycycle_fn_lbf"],
        "engine_scale": flight["engine_scale"],
        "fuel_burned_kg": flight["fuel_burned_kg"],
        "gtow_kg": flight["gtow_kg"],
        "cruise_point": point,
        "canonical_baseline": flight["canonical_baseline"],
        "resolved_inputs": {t: sorted({k for e in es for k in e["inputs"]}) for t, es in kb_by_tool.items()},
    },
}
text = json.dumps(fixture, indent=1, sort_keys=True, default=str)
out_path.write_text(text)
print(out_path, len(text), "chars;", sorted(calls))

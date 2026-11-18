"""Test helper (B340): make a hand-built aviary cruise point count for the CURRENT design.

Since B340 a cruise point counts only for the design it was asked for (data_plane.design_basis: geometry fingerprint +
measured tails) and when its aviary session still holds that design (per-session held fingerprint). Tests that put a
point in the store by hand call stamp() right after, so the point belongs to the design as it is at that moment; a
later morph in the test then makes it stale, as in a run.
"""


def stamp(state=None, session: str = "av-test") -> dict:
    import src.tools.data_plane as dp

    store = (state if state is not None else dp.get_design_state()).data_store
    point = dict(store.get("aviary_cruise_point") or {})
    point.update(_design_basis=dp.design_basis(), _session=session)
    store["aviary_cruise_point"] = point
    store.setdefault("aviary_held_fingerprint_by_session", {})[session] = point.get("design_fingerprint")
    return point

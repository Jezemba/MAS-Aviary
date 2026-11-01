"""budget_compare: the running values per flight and the MAS flight reader."""
from analysis.link20.budget_compare import LAMBDA_BASE, LAMBDA_GAP, flight_loss, mas_flights, running


def test_flight_loss_is_the_paper_penalty():
    assert flight_loss(9000.0, 89000.0) == 9000.0
    assert flight_loss(9000.0, 99000.0) == 9000.0 + LAMBDA_BASE + LAMBDA_GAP * 0.1


def test_running_values_ignore_unconverged_for_fuel_and_gtow():
    rows = running([{"fuel": 19000.0, "gtow": 97000.0, "converged": True},
                    {"fuel": 4760.0, "gtow": 84900.0, "converged": False},     # unconverged: not a best fuel
                    {"fuel": 18000.0, "gtow": 98000.0, "converged": True}])
    assert [r["best_conv_fuel"] for r in rows] == [19000.0, 19000.0, 18000.0]
    assert [r["min_conv_gtow"] for r in rows] == [97000.0, 97000.0, 97000.0]
    assert rows[1]["best_loss"] == 4760.0                                       # the loss does not check convergence
    assert [r["flight"] for r in rows] == [1, 2, 3]


def test_mas_flights_reads_successful_runs_above_the_floor():
    kb = [{"tool": "run_simulation", "status": "success",
           "outputs": {"converged": False, "summary": {"fuel_burned_kg": 23077.0, "gtow_kg": 102314.0}}},
          {"tool": "run_simulation", "status": "success",
           "outputs": {"converged": False, "summary": {"fuel_burned_kg": 1e-6, "gtow_kg": 79560.0}}},
          {"tool": "run_simulation", "status": "error", "outputs": {}},
          {"tool": "get_results", "status": "success",
           "outputs": {"converged": True, "fuel_burned_kg": 18000.0, "gtow_kg": 95000.0}}]
    assert mas_flights(kb) == [{"fuel": 23077.0, "gtow": 102314.0, "converged": False}]

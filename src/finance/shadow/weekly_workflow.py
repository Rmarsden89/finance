from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WorkflowStage:
    name: str
    mode: str
    description: str
    command: str | None
    outputs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["outputs"] = list(self.outputs)
        return payload


@dataclass(frozen=True)
class WeeklyWorkflowPlan:
    as_of: str
    run_dir: str
    run_log: str
    dry_run: bool
    stages: tuple[WorkflowStage, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of,
            "run_dir": self.run_dir,
            "run_log": self.run_log,
            "dry_run": self.dry_run,
            "stages": [stage.to_dict() for stage in self.stages],
        }


def build_weekly_workflow_plan(
    *,
    as_of: date,
    repo_root: str | Path,
    report_root: str | Path = "reports/shadow",
    dry_run: bool = True,
) -> WeeklyWorkflowPlan:
    repo_root = Path(repo_root)
    report_root = Path(report_root)
    if not report_root.is_absolute():
        report_root = repo_root / report_root

    run_dir = report_root / as_of.isoformat()
    run_log = run_dir / "run_log.jsonl"

    scoring_panel = repo_root / "reports/current_shadow_scoring_panel.csv"
    long_growth = repo_root / "reports/current_shadow_long_growth_v1.csv"

    portfolio_state = run_dir / "portfolio_state.csv"
    non_final_orders = run_dir / "non_final_orders.csv"
    shadow_decision_json = run_dir / "shadow_decision.json"
    shadow_decision_csv = run_dir / "shadow_decision.csv"
    execution_gate = run_dir / "execution_gate.json"
    order_intents_json = run_dir / "order_intents.json"
    order_intents_csv = run_dir / "order_intents.csv"
    pre_submit_gate = run_dir / "pre_submit_gate.json"
    submission_receipt = run_dir / "submission_receipt.json"
    submission_reconciliation = run_dir / "submission_reconciliation.json"
    post_fill_reconciliation = run_dir / "post_fill_reconciliation.json"

    stages = (
        WorkflowStage(
            name="tests",
            mode="local_safe",
            description="Run V1 shadow/execution safety tests before any broker action.",
            command=(
                "py -m pytest "
                "tests\\test_post_fill.py "
                "tests\\test_submission_receipt.py "
                "tests\\test_run_log.py "
                "tests\\test_pre_submit.py "
                "tests\\test_order_intent.py "
                "tests\\test_execution_gate.py "
                "tests\\test_robinhood_shadow_state.py "
                "tests\\test_shadow_decision.py"
            ),
            outputs=(),
        ),
        WorkflowStage(
            name="current_model_refresh",
            mode="local_safe",
            description=(
                "Refresh the current scoring artifacts using the frozen model. "
                "This stage remains separate from broker execution."
            ),
            command=None,
            outputs=(str(scoring_panel), str(long_growth)),
        ),
        WorkflowStage(
            name="broker_snapshot_pre",
            mode="external_read_only",
            description=(
                "Collect a fresh read-only Agentic broker snapshot. "
                "No order placement or modification."
            ),
            command=None,
            outputs=(str(run_dir / "broker_snapshot_pre.json"),),
        ),
        WorkflowStage(
            name="normalize_broker_state",
            mode="local_safe",
            description="Normalize current Agentic positions and open orders.",
            command=(
                f'py scripts\\normalize_robinhood_shadow_state.py '
                f'--input "{run_dir / "broker_snapshot_pre.json"}" '
                f'--portfolio-output "{portfolio_state}" '
                f'--orders-output "{non_final_orders}" '
                f'--run-log "{run_log}"'
            ),
            outputs=(str(portfolio_state), str(non_final_orders)),
        ),
        WorkflowStage(
            name="shadow_decision",
            mode="local_safe",
            description="Build deterministic V1 Top-10 decision and $10 allocation.",
            command=(
                f'py scripts\\build_shadow_decision.py '
                f'--long-growth "{long_growth}" '
                f'--portfolio-state "{portfolio_state}" '
                f'--broker-state "{run_dir / "broker_snapshot_pre.json"}" '
                f'--as-of {as_of.isoformat()} '
                f'--weekly-contribution 10 '
                f'--output-dir "{run_dir}" '
                f'--run-log "{run_log}"'
            ),
            outputs=(str(shadow_decision_json), str(shadow_decision_csv)),
        ),
        WorkflowStage(
            name="execution_gate",
            mode="local_safe",
            description="Reconcile decision against current Agentic broker state.",
            command=(
                f'py scripts\\evaluate_execution_gate.py '
                f'--decision "{shadow_decision_json}" '
                f'--broker-state "{run_dir / "broker_snapshot_pre.json"}" '
                f'--output "{execution_gate}" '
                f'--run-log "{run_log}"'
            ),
            outputs=(str(execution_gate),),
        ),
        WorkflowStage(
            name="order_intents",
            mode="local_safe",
            description="Build exact broker-ready order intents; does not submit orders.",
            command=(
                f'py scripts\\build_order_intents.py '
                f'--decision "{shadow_decision_json}" '
                f'--execution-gate "{execution_gate}" '
                f'--output-dir "{run_dir}" '
                f'--run-log "{run_log}"'
            ),
            outputs=(str(order_intents_json), str(order_intents_csv)),
        ),
        WorkflowStage(
            name="broker_snapshot_presubmit",
            mode="external_read_only",
            description=(
                "Collect a second fresh Agentic snapshot immediately before submission."
            ),
            command=None,
            outputs=(str(run_dir / "broker_snapshot_presubmit.json"),),
        ),
        WorkflowStage(
            name="pre_submit_gate",
            mode="local_safe",
            description="Final fail-closed gate using a fresh broker snapshot.",
            command=(
                f'py scripts\\evaluate_pre_submit_gate.py '
                f'--order-intents "{order_intents_json}" '
                f'--broker-state "{run_dir / "broker_snapshot_presubmit.json"}" '
                f'--output "{pre_submit_gate}" '
                f'--run-log "{run_log}"'
            ),
            outputs=(str(pre_submit_gate),),
        ),
        WorkflowStage(
            name="submission",
            mode="external_trade",
            description=(
                "Submit exactly the approved order intents only after the pre-submit "
                "gate is READY. Disabled in dry-run mode."
            ),
            command=None,
            outputs=(str(submission_receipt),),
        ),
        WorkflowStage(
            name="submission_reconciliation",
            mode="local_safe",
            description="Match Robinhood order IDs/states back to frozen intents.",
            command=(
                f'py scripts\\reconcile_submission_receipt.py '
                f'--order-intents "{order_intents_json}" '
                f'--broker-receipt "{submission_receipt}" '
                f'--output "{submission_reconciliation}" '
                f'--run-log "{run_log}"'
            ),
            outputs=(str(submission_reconciliation),),
        ),
        WorkflowStage(
            name="broker_snapshot_postfill",
            mode="external_read_only",
            description=(
                "Collect post-fill Agentic positions, cash, orders, and current "
                "valuations for all held equities."
            ),
            command=None,
            outputs=(str(run_dir / "broker_snapshot_postfill.json"),),
        ),
        WorkflowStage(
            name="post_fill_reconciliation",
            mode="local_safe",
            description="Reconcile fills to position deltas and create next portfolio state.",
            command=(
                f'py scripts\\reconcile_post_fill_portfolio.py '
                f'--submission-reconciliation "{submission_reconciliation}" '
                f'--pre-broker-state "{run_dir / "broker_snapshot_presubmit.json"}" '
                f'--post-broker-state "{run_dir / "broker_snapshot_postfill.json"}" '
                f'--output "{post_fill_reconciliation}" '
                f'--portfolio-output "{portfolio_state}" '
                f'--run-log "{run_log}"'
            ),
            outputs=(str(post_fill_reconciliation), str(portfolio_state)),
        ),
    )

    return WeeklyWorkflowPlan(
        as_of=as_of.isoformat(),
        run_dir=str(run_dir),
        run_log=str(run_log),
        dry_run=dry_run,
        stages=stages,
    )

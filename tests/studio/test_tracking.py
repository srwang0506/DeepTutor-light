import json

from deeptutor.studio.tracking import run_draft


def test_manifest_extracts_metadata_but_never_uses_declared_success_as_verified_execution():
    draft = run_draft(
        json.dumps(
            {
                "title": "Fixture only",
                "status": "succeeded",
                "configuration": {"seed": 7, "dataset": "synthetic"},
                "commit": "abc123",
            }
        )
    )
    assert draft["run"]["status"] == "planned"
    assert draft["declared_status"] == "succeeded"
    assert draft["run"]["seed"] == 7
    assert draft["run"]["dataset"] == "synthetic"
    assert draft["run"]["commit"] == "abc123"
    assert draft["execution_verified"] is False


def test_mlflow_snapshot_keeps_steps_params_and_historical_git_tag():
    raw = json.dumps(
        {
            "run": {
                "info": {"run_id": "fixture-id", "status": "FINISHED"},
                "data": {
                    "params": [{"key": "seed", "value": "42"}],
                    "tags": [{"key": "mlflow.source.git.commit", "value": "original-run-commit"}],
                    "metrics": [{"key": "loss", "value": 1.5, "step": 2, "timestamp": 100}],
                },
            }
        }
    )
    draft = run_draft(raw)
    assert draft["adapter"] == "mlflow"
    assert draft["run"]["commit"] == "original-run-commit"
    assert draft["run"]["seed"] == 42
    assert draft["metric_samples"][0]["step"] == 2
    assert draft["run"]["log"] == raw
    assert draft["run"]["status"] == "planned"


def test_wandb_export_and_tensorboard_scalar_export_are_read_without_running_code():
    wandb = run_draft(
        json.dumps(
            {
                "name": "Fixture",
                "state": "finished",
                "config": {"seed": {"value": 4, "desc": None}},
                "summary": {"loss": 1.2, "_step": 10, "text": "ignored"},
            }
        )
    )
    assert wandb["adapter"] == "wandb"
    assert wandb["run"]["seed"] == 4
    assert wandb["run"]["commit"] == ""
    assert wandb["metric_samples"] == [{"name": "loss", "value": 1.2, "step": 10}]
    tb = run_draft(
        json.dumps(
            {
                "scalars": {
                    "loss": [
                        {"value": 2, "step": 1, "wall_time": 100},
                        {"value": 1, "step": 2, "wall_time": 200},
                    ]
                }
            }
        )
    )
    assert tb["adapter"] == "tensorboard_export"
    assert len(tb["metric_samples"]) == 2
    assert tb["run"]["status"] == "planned"


def test_plain_logs_and_empty_logs_never_gain_invented_metadata():
    for text in ("epoch=1\nloss=2.5\n", ""):
        draft = run_draft(text, "Fixture log")
        assert draft["adapter"] == "plain_log"
        assert draft["run"]["configuration"] == {}
        assert draft["run"]["commit"] == ""
        assert draft["metric_samples"] == []

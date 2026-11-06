"""Deterministic drafts from existing tracking exports; never invent execution."""

import json
import math

from .models import RunInput


def run_draft(text, name="Imported record"):
    warnings = []
    base = RunInput(title=name[:500] or "Imported record", status="planned").model_dump()
    metrics, declared, adapter, external_id = [], None, "plain_log", ""
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        row = payload.get("run", payload)
        if (
            isinstance(row, dict)
            and isinstance(row.get("info"), dict)
            and isinstance(row.get("data"), dict)
        ):
            adapter = "mlflow"
            info, data = row["info"], row["data"]
            tags = {
                r["key"]: r.get("value", "")
                for r in data.get("tags", [])
                if isinstance(r, dict) and isinstance(r.get("key"), str)
            }
            config = {
                r["key"]: r.get("value", "")
                for r in data.get("params", [])
                if isinstance(r, dict) and isinstance(r.get("key"), str)
            }
            external_id = str(info.get("run_id", info.get("run_uuid", "")))
            declared = info.get("status")
            base.update(
                title=str(info.get("run_name") or tags.get("mlflow.runName") or name),
                commit=str(tags.get("mlflow.source.git.commit", "")),
                repository=str(tags.get("mlflow.source.git.repoURL", "")),
                configuration=config,
            )
            metrics = [
                {
                    "name": r.get("key"),
                    "value": r.get("value"),
                    "step": r.get("step"),
                    "timestamp": r.get("timestamp"),
                }
                for r in data.get("metrics", [])
                if isinstance(r, dict)
            ]
        elif (
            isinstance(row, dict)
            and ("summary" in row or "summaryMetrics" in row)
            and ("config" in row or "state" in row)
        ):
            adapter = "wandb"
            config, summary = (
                row.get("config", {}),
                row.get("summary", row.get("summaryMetrics", {})),
            )
            if isinstance(config, str):
                config = json.loads(config)
            if isinstance(summary, str):
                summary = json.loads(summary)
            if not isinstance(config, dict) or not isinstance(summary, dict):
                raise ValueError("W&B config and summary must be JSON objects.")
            config = {
                k: v.get("value") if isinstance(v, dict) and set(v) <= {"value", "desc"} else v
                for k, v in config.items()
            }
            external_id, declared = str(row.get("id", "")), row.get("state")
            git = row.get("git", {}) if isinstance(row.get("git"), dict) else {}
            base.update(
                title=str(row.get("displayName") or row.get("name") or name),
                configuration=config,
                commit=str(git.get("commit", "")),
                repository=str(git.get("remote", "")),
            )
            metrics = [
                {"name": k, "value": v, "step": summary.get("_step")}
                for k, v in summary.items()
                if not k.startswith("_") and isinstance(v, (int, float)) and not isinstance(v, bool)
            ]
        elif isinstance(row, dict) and isinstance(row.get("scalars"), dict):
            adapter, declared = "tensorboard_export", row.get("status")
            base["title"] = str(row.get("name") or name)
            for tag, samples in row["scalars"].items():
                if not isinstance(samples, list):
                    continue
                for sample in samples:
                    if isinstance(sample, dict):
                        metrics.append(
                            {
                                "name": tag,
                                "value": sample.get("value"),
                                "step": sample.get("step"),
                                "timestamp": sample.get("wall_time"),
                            }
                        )
        elif isinstance(row, dict) and any(
            key in row for key in ("configuration", "command", "dataset", "status", "commit")
        ):
            adapter = "manifest"
            declared = row.get("status")
            base.update(
                {
                    k: v
                    for k, v in row.items()
                    if k in RunInput.model_fields and k not in {"status", "log"}
                }
            )
    else:
        warnings.append("No structured run manifest was detected; the original text is retained.")
    if isinstance(base["configuration"], dict):
        config = base["configuration"]
        for key in ("dataset", "split", "checkpoint"):
            if not base[key] and isinstance(config.get(key), str):
                base[key] = config[key]
        if base["seed"] is None and isinstance(config.get("seed"), (str, int)):
            try:
                base["seed"] = int(config["seed"])
            except ValueError:
                pass
    valid_metrics = [
        m
        for m in metrics
        if isinstance(m.get("name"), str)
        and isinstance(m.get("value"), (int, float))
        and not isinstance(m.get("value"), bool)
        and math.isfinite(m["value"])
    ]
    if len(valid_metrics) > 10000:
        raise ValueError(
            "Tracking export exceeds 10,000 metric samples; select a smaller snapshot."
        )
    if declared:
        warnings.append(
            "The source declares a run status; confirm the actual outcome before importing."
        )
    if metrics:
        warnings.append(
            "Metric names do not establish an evaluation protocol. Verify dataset, split, units and aggregation before comparing values."
        )
    if not base["commit"]:
        warnings.append(
            "The historical run commit is unknown. Current repository HEAD is not used as a substitute."
        )
    base["status"] = "planned"
    base["log"] = text
    return {
        "run": RunInput(**base).model_dump(),
        "adapter": adapter,
        "external_id": external_id,
        "declared_status": str(declared) if declared else None,
        "metric_samples": valid_metrics,
        "warnings": warnings,
        "execution_verified": False,
    }

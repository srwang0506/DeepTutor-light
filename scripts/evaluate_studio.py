#!/usr/bin/env python3
"""Run a sealed Studio suite against the existing local Codex OAuth login.

Outputs contain public task responses and measurements, never credentials.
Use a separate --home to keep acceptance data out of personal workspaces.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", type=Path)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--resume", default="")
    args = parser.parse_args()
    os.environ["DEEPTUTOR_HOME"] = str(args.home.resolve())
    os.environ["DEEPTUTOR_USE_CODEX_LOGIN"] = "1"
    from deeptutor.services.setup.init import init_user_directories

    init_user_directories()
    from deeptutor.services.llm.config import LLMConfig
    from deeptutor.services.llm.factory import complete_with_config
    from deeptutor.studio import evaluations
    from deeptutor.studio.database import Studio

    config = LLMConfig(
        model=args.model,
        api_key="",
        binding="openai_codex",
        provider_name="openai_codex",
        provider_mode="oauth",
        reasoning_effort="low",
        temperature=0,
        max_tokens=1800,
    )
    model = {
        "binding": config.binding,
        "provider_mode": config.provider_mode,
        "model": config.model,
        "reasoning_effort": config.reasoning_effort,
        "temperature": config.temperature,
    }
    store = Studio(project="held-out-acceptance")
    if args.resume:
        run_id = args.resume
    else:
        suite = evaluations.freeze(
            store, evaluations.Suite.model_validate_json(args.suite.read_text())
        )
        run_id = evaluations.start(store, suite["id"], model)["id"]
    print(
        json.dumps({"run_id": run_id, "model": args.model, "transport": "existing_codex_oauth"}),
        flush=True,
    )

    async def run():
        from deeptutor.services.codex_auth import get_codex_oauth_service

        await get_codex_oauth_service().refresh_models()

        async def complete(prompt, **kwargs):
            return await complete_with_config(config, prompt, max_retries=0, **kwargs)

        while True:
            report = await evaluations.advance(store, run_id, model, complete)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            temporary = args.output.with_suffix(".tmp")
            temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2))
            temporary.replace(args.output)
            m = report["metrics"]["all"]
            print(
                json.dumps(
                    {
                        "completed": m["finished"],
                        "total": m["cases"],
                        "scored": m["scored"],
                        "correct": m["correct"],
                    }
                ),
                flush=True,
            )
            if report["state"] == "complete":
                break
            if m["finished"] >= 3 and m["scored"] == 0:
                raise SystemExit(
                    "Stopped after three unavailable model calls; inspect configuration before resuming."
                )

    asyncio.run(run())


if __name__ == "__main__":
    main()

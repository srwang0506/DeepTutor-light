"""Learning responses expose prompts; references are returned only after recording disclosure."""


def learning_question(contract):
    keys = (
        "question_id",
        "question_version",
        "question",
        "question_type",
        "options",
        "answerable",
        "generation_status",
    )
    return {key: contract[key] for key in keys if key in contract}


def learning_activity(row):
    if row["kind"] != "activity":
        return row
    data = row["data"]
    visible = {
        key: data[key]
        for key in (
            "title",
            "state",
            "source",
            "session_id",
            "turn_id",
            "failure_reason",
            "elapsed_seconds",
        )
        if key in data
    }
    visible["questions"] = [learning_question(q) for q in data.get("questions", [])]
    visible["view"] = "learning"
    return {**row, "data": visible}

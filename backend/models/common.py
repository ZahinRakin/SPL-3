from typing import Tuple


def check_in(col: str, values: Tuple[str, ...]) -> str:
    """SQL for a CHECK constraint. TEXT + CHECK is used instead of Postgres ENUM
    types so adding a value later is a one-line migration."""
    return f"{col} IN ({', '.join(repr(v) for v in values)})"

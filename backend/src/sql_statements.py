"""Split migration SQL outside standard strings, comments and dollar-quoted bodies."""

import re

_TOKEN = re.compile(
    r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|"
    r"\$(?P<tag>[A-Za-z_][A-Za-z_0-9]*|)\$.*?\$(?P=tag)\$|"
    r"--[^\n]*(?:\n|$)|/\*.*?\*/|;",
    re.DOTALL,
)


def split_sql_statements(sql: str) -> list[str]:
    start = 0
    statements = []
    for match in _TOKEN.finditer(sql):
        if match.group() == ";":
            statement = sql[start:match.start()].strip()
            if statement:
                statements.append(statement)
            start = match.end()
    if sql[start:].strip():
        statements.append(sql[start:].strip())
    return statements

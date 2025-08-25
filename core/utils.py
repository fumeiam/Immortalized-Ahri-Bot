import shlex, functools
from typing import List

def tokenize(text: str) -> List[str]:
    try:
        return shlex.split(text)
    except Exception:
        return text.split()

def admin_only(func):
    func._needs_admin = True
    return func

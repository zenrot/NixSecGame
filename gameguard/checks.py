from importlib import import_module
from importlib.util import find_spec


def backend(module):
    name = f"gameguard.{module}"
    return import_module(name) if find_spec(name) is not None else None


def unavailable(module):
    return {"status": "not_applicable", "reason": f"Модуль {module} ещё не подключён"}


def apply_check(module, function, *args):
    implementation = backend(module)
    if implementation is None:
        return unavailable(module)
    return getattr(implementation, function)(*args)

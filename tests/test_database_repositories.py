"""``ModdyDatabase`` mixes every repository into one class — so names collide.

`db/base.py::ModdyDatabase` inherits from ~24 repositories at once, which means
two repositories that happen to pick the same method name do **not** produce an
error: the one earlier in the MRO silently wins, and every call meant for the
other one lands on it. That is not a hypothetical — `TicketsRepository.set_claim`
shipped and was shadowed by `AppealRepository.set_claim`, so claiming a ticket
raised ``TypeError: takes 2 positional arguments but 3 were given`` in
production instead of failing at import.

The rule this file enforces: **a repository method name is unique across all
repositories.** When two of them describe the same verb on different things,
qualify the name (``set_ticket_claim``, not ``set_claim``).

    pytest tests/test_database_repositories.py -q
"""

import collections
import inspect


def _repository_methods():
    """``{method_name: [repository class name, ...]}`` over every mixin."""
    from db.base import ModdyDatabase

    owners = collections.defaultdict(list)
    for repository in ModdyDatabase.__bases__:
        for name, value in vars(repository).items():
            if name.startswith('_'):
                continue
            if not (inspect.isfunction(value) or isinstance(value, property)):
                continue
            owners[name].append(repository.__name__)
    return owners


def test_no_two_repositories_define_the_same_method():
    clashes = {
        name: sorted(owners)
        for name, owners in _repository_methods().items()
        if len(owners) > 1
    }
    assert not clashes, (
        "these method names are defined by more than one repository, so the "
        "one earliest in ModdyDatabase's MRO silently shadows the others — "
        "qualify the name (e.g. `set_ticket_claim` rather than `set_claim`):\n"
        + "\n".join(f"  {name}: {owners}" for name, owners in clashes.items())
    )


def test_the_ticket_claim_method_is_reachable_from_the_database():
    """The exact regression: `bot.db.set_ticket_claim` must be the ticket one."""
    from db.base import ModdyDatabase
    from db.repositories.tickets import TicketsRepository

    assert ModdyDatabase.set_ticket_claim is TicketsRepository.set_ticket_claim

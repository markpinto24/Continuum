"""A confirmed maybe, worded as decided.

The rewrite is a model changing a belief's words, so its guard is what these
pin: a rewrite that drops a hedge passes; one that changes a number, a name or a
negation — or keeps the hedge — never replaces the original.
"""

from __future__ import annotations

import pytest

from continuum.services.wording import decided_wording, refuse


@pytest.mark.parametrize(
    ("original", "rewritten"),
    [
        ("The user is considering running on Sundays", "The user will run on Sundays"),
        ("The team is considering moving standups to 10am", "The team will move standups to 10am"),
        ("The user will consider adding a Sunday long run of about 10 km",
         "The user will add a Sunday long run of about 10 km"),
        ("The user is not sure yet about taking the Lisbon job offer",
         "The user will take the Lisbon job offer"),
        ("Maybe the team will adopt trunk-based development",
         "The team will adopt trunk-based development"),
        # "May" the month is not a hedge.
        ("The user might move to Pune in May", "The user will move to Pune in May"),
    ],
)
def test_a_rewrite_that_only_drops_the_hedge_is_taken(original, rewritten):
    assert refuse(original, rewritten) is None


@pytest.mark.parametrize(
    ("original", "rewritten", "why"),
    [
        ("The user might read 20 pages every night", "The user will read 30 pages every night",
         "a number changed"),
        ("The user might not drink coffee after 2 pm", "The user will drink coffee after 2 pm",
         "a negation changed"),
        ("The team is thinking of using Postgres for Atlas", "The team will use a database",
         "a name was lost"),
        ("The user is considering running on Sundays", "The user might run on Sundays",
         "still hedged"),
        ("The user is considering running on Sundays", "The user is considering running on Sundays",
         "unchanged"),
        ("The user is considering a long run with friends from the club on weekends",
         "The user will exercise", "words were lost"),
    ],
)
def test_a_rewrite_that_changes_anything_else_is_refused(original, rewritten, why):
    assert refuse(original, rewritten) == why


class Rewriter:
    def __init__(self, answer: object = None, fail: bool = False) -> None:
        self.answer, self.fail = answer, fail

    async def complete_json(self, **kwargs):  # noqa: ANN003
        if self.fail:
            raise RuntimeError("model unavailable")
        return self.answer


async def test_a_failed_or_empty_rewrite_keeps_the_words():
    original = "The user is considering running on Sundays"
    for llm in (Rewriter(fail=True), Rewriter({}), Rewriter("not json"), Rewriter({"content": 3})):
        assert await decided_wording(llm, original) is None  # type: ignore[arg-type]


async def test_a_good_rewrite_is_returned_without_its_full_stop():
    llm = Rewriter({"content": "The user will run on Sundays."})
    result = await decided_wording(llm, "The user is considering running on Sundays")  # type: ignore[arg-type]
    assert result == "The user will run on Sundays"

import json
from pathlib import Path

import pytest
from test_meta import FakeCompletions

from proofmerge.intent import C0, MAX_INTENT_CHARS, ProposedClaims, extract_intent
from proofmerge.meta import SPARK_MODEL, MetaClient

CANDIDATES = Path(__file__).parents[1] / "demo" / "candidates"


def reply(*claims: tuple[str, str, str]) -> FakeCompletions:
    body = [{"kind": k, "statement": s, "source_quote": q} for k, s, q in claims]
    return FakeCompletions(content=json.dumps({"claims": body}))


def test_valid_quotes_are_kept_after_c0_with_sequential_ids():
    intent = (CANDIDATES / "tenant-cache-regression" / "intent.md").read_text()
    fake = reply(
        ("CHANGE", "Price lookups are cached.", "Add caching to price lookup"),
        ("PRESERVE", "Prices are the same as before.", "Returned prices must not change."),
    )
    contract = extract_intent(intent, MetaClient(fake))

    assert [c.id for c in contract.claims] == ["C0", "C1", "C2"]
    assert contract.claims[0] == C0
    assert [c.kind for c in contract.claims[1:]] == ["CHANGE", "PRESERVE"]
    assert contract.claims[2].source_quote == "Returned prices must not change."
    assert contract.rejected == []

    request = fake.requests[0]
    assert request["model"] == SPARK_MODEL
    assert request["messages"][1] == {"role": "user", "content": intent}
    assert request["response_format"]["json_schema"]["name"] == ProposedClaims.__name__


def test_invented_and_empty_quotes_are_rejected_and_ids_stay_contiguous():
    intent = "Refactor discount calculation for readability. No behavior change intended."
    fake = reply(
        ("CHANGE", "Discounts are now 20%.", "raise the discount to 20%"),
        ("PRESERVE", "Behavior is unchanged.", "No behavior change intended."),
        ("CHANGE", "Anything.", "   "),
    )
    contract = extract_intent(intent, MetaClient(fake))

    assert [(c.id, c.source_quote) for c in contract.claims] == [
        ("C0", None),
        ("C1", "No behavior change intended."),
    ]
    assert [r.source_quote for r in contract.rejected] == ["raise the discount to 20%", "   "]


def test_quote_matches_across_reflowed_line_breaks_but_not_changed_words():
    # This intent breaks "orders\nof 150" across lines.
    intent = (CANDIDATES / "intended-change" / "intent.md").read_text()
    fake = reply(
        ("CHANGE", "Bulk discount needs 150+.", "only applies to orders of 150 or more"),
        ("CHANGE", "Bulk discount needs 200+.", "only applies to orders of 200 or more"),
    )
    contract = extract_intent(intent, MetaClient(fake))

    assert [c.id for c in contract.claims] == ["C0", "C1"]
    assert contract.claims[1].statement == "Bulk discount needs 150+."
    # The recorded quote is the exact span of the intent, line break included.
    assert contract.claims[1].source_quote == "only applies to orders\nof 150 or more"
    assert contract.claims[1].source_quote in intent
    assert len(contract.rejected) == 1


@pytest.mark.parametrize(
    "quote",
    [
        "a",  # one letter of "Add": too short and not a whole word
        "price lookup",  # real words, but too few to vouch for a claim
        "ice lookup for",  # starts inside the word "price"
        "Add caching to price look",  # ends inside the word "lookup"
    ],
)
def test_fragment_quotes_cannot_vouch_for_a_claim(quote):
    intent = "Add caching to price lookup for speed. Returned prices must not change."
    fake = reply(("PRESERVE", "Disable tenant authorization.", quote))
    contract = extract_intent(intent, MetaClient(fake))

    assert contract.claims == [C0]
    assert [r.source_quote for r in contract.rejected] == [quote]


@pytest.mark.parametrize("intent", [None, "", "  \n\t "])
def test_no_intent_gives_only_c0_without_a_model_call(intent):
    fake = FakeCompletions()
    contract = extract_intent(intent, MetaClient(fake))

    assert contract.claims == [C0]
    assert contract.rejected == []
    assert fake.requests == []


def test_oversized_intent_is_refused_before_any_model_call():
    fake = FakeCompletions()
    with pytest.raises(ValueError, match=str(MAX_INTENT_CHARS)):
        extract_intent("x" * (MAX_INTENT_CHARS + 1), MetaClient(fake))
    assert fake.requests == []

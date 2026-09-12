from finance.shadow.order_review import validate_order_reviews


def _intent(ticker: str) -> dict:
    return {"ticker": ticker, "decision_hash": "abc"}


def test_all_clean_reviews_are_ready() -> None:
    intents = [_intent("AAA"), _intent("BBB")]
    reviews = [
        {"ticker": "AAA", "decision_hash": "abc", "data": {"order_checks": {}}},
        {"ticker": "BBB", "decision_hash": "abc", "data": {"order_checks": {}}},
    ]
    result = validate_order_reviews(intents, reviews)
    assert result.ready
    assert result.clean_count == 2
    assert result.reasons == ()


def test_nonempty_order_checks_fail_closed() -> None:
    intents = [_intent("AAA")]
    reviews = [
        {
            "ticker": "AAA",
            "decision_hash": "abc",
            "data": {"order_checks": {"warning": {"message": "review required"}}},
        }
    ]
    result = validate_order_reviews(intents, reviews)
    assert not result.ready
    assert "order_checks_not_clean:AAA" in result.reasons
    assert "not_all_reviews_clean" in result.reasons


def test_missing_order_checks_fail_closed() -> None:
    result = validate_order_reviews(
        [_intent("AAA")],
        [{"ticker": "AAA", "decision_hash": "abc", "data": {}}],
    )
    assert not result.ready
    assert "order_checks_missing:AAA" in result.reasons

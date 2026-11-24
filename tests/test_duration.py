"""itunes:duration is optional; a malformed one must not fail the feed refresh."""

import pytest

from feedelio.core.reader_extensions import duration_seconds


@pytest.mark.parametrize("value", ["inf", "-inf", "nan", "1e309", "1:inf", "abc", "", None])
def test_malformed_durations_fall_back_to_zero(value):
    assert duration_seconds(value) == 0


@pytest.mark.parametrize(("value", "seconds"), [("1:02:03", 3723), ("62:03", 3723), ("3723", 3723)])
def test_wellformed_durations_parse(value, seconds):
    assert duration_seconds(value) == seconds

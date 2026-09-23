import pytest

from scholarscope.cli import build_parser


def test_ingest_needs_exactly_one_of_profile_or_resume():
    parser = build_parser()
    assert parser.parse_args(["ingest", "--profile", "smoke"]).profile == "smoke"
    assert parser.parse_args(["ingest", "--resume", "12"]).resume == 12
    for argv in (["ingest"], ["ingest", "--profile", "smoke", "--resume", "1"], ["ingest", "--profile", "huge"]):
        with pytest.raises(SystemExit):
            parser.parse_args(argv)

"""Markdown-fence tolerance in the three LLM response parsers.

D2b's manual capture rounds showed chat UIs can wrap a JSON completion in a
```json fence. The prompts forbid it, but a fenced reply is still a correct
answer, so parsing strips one wrapping fence rather than failing the call.
"""

from __future__ import annotations

import json

import pytest

from jscc.extraction import ExtractionParseError
from jscc.extraction import _parse_response as parse_extraction
from jscc.json_utils import strip_code_fence
from jscc.routing import RoutingParseError
from jscc.routing import _parse_response as parse_routing
from jscc.scoring import ScoringParseError
from jscc.scoring import _parse_response as parse_scoring

_ROUTING = json.dumps(
    {"classification": "routine", "intent": "cadence_nudge", "reason": None, "considerations": []}
)
_SCORING = json.dumps({"score": 85, "rationale": "Strong match."})
_EXTRACTION = json.dumps(
    {
        "title": "Senior Backend Engineer",
        "level": "senior",
        "comp_band": "$180,000-$220,000",
        "location": None,
        "remote_policy": "remote",
        "must_have_skills": ["Python"],
        "responsibilities_summary": "Owns billing services.",
    }
)

_PARSERS = [
    (parse_routing, _ROUTING, RoutingParseError),
    (parse_scoring, _SCORING, ScoringParseError),
    (parse_extraction, _EXTRACTION, ExtractionParseError),
]


@pytest.mark.parametrize(
    "wrap",
    [
        "```json\n{}\n```",
        "```json\n{}\n```\n\n",
        "```\n{}\n```",
        "  ```JSON\n{}\n```  ",
        "```json\r\n{}\r\n```\r\n",
    ],
)
@pytest.mark.parametrize(("parse", "payload", "_err"), _PARSERS)
def test_parsers_accept_a_wrapping_fence(parse, payload, _err, wrap) -> None:
    assert parse(wrap.replace("{}", payload)) == parse(payload)


@pytest.mark.parametrize(("parse", "payload", "err"), _PARSERS)
def test_parsers_still_reject_prose_around_the_json(parse, payload, err) -> None:
    with pytest.raises(err):
        parse(f"Here is the result:\n```json\n{payload}\n```")


@pytest.mark.parametrize(("parse", "_payload", "err"), _PARSERS)
def test_parsers_still_reject_a_fenced_non_json_body(parse, _payload, err) -> None:
    with pytest.raises(err):
        parse("```json\nnot json\n```")


def test_strip_code_fence_leaves_unfenced_text_untouched() -> None:
    assert strip_code_fence('{"a": 1}') == '{"a": 1}'
    assert strip_code_fence("not json at all") == "not json at all"


def test_strip_code_fence_leaves_an_unterminated_fence_alone() -> None:
    text = '```json\n{"a": 1}'
    assert strip_code_fence(text) == text

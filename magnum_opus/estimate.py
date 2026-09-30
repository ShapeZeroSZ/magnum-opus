"""Cost estimation. Nothing runs before the user has seen a number.

Token counts are approximated at ~4 characters per token, which is close enough
for a budget decision and requires no API call. Rates are user-supplied so this
never goes stale in the code; the defaults reflect the cheap extraction tier and
should be checked against the console before a large run.
"""

from __future__ import annotations

from dataclasses import dataclass

from .distill import strip_for_budget
from .segment import skeleton

CHARS_PER_TOKEN = 4.0

DEFAULT_INPUT_RATE = 0.50    # $/M input tokens, batch tier
DEFAULT_OUTPUT_RATE = 2.50   # $/M output tokens, batch tier
EST_OUTPUT_TOKENS_PER_SEGMENT = 700


@dataclass
class Estimate:
    segmentation_chars: int = 0   # skeleton passes, paid before distillation
    conversations: int = 0
    messages: int = 0
    raw_chars: int = 0
    stripped_chars: int = 0
    skeleton_chars: int = 0
    segments: int = 0

    @property
    def raw_tokens(self) -> int:
        return int(self.raw_chars / CHARS_PER_TOKEN)

    @property
    def segmentation_tokens(self) -> int:
        return int(self.segmentation_chars / CHARS_PER_TOKEN)

    @property
    def input_tokens(self) -> int:
        return int((self.stripped_chars + self.skeleton_chars
                    + self.segmentation_chars) / CHARS_PER_TOKEN)

    @property
    def output_tokens(self) -> int:
        return self.segments * EST_OUTPUT_TOKENS_PER_SEGMENT

    def cost(self, input_rate=DEFAULT_INPUT_RATE, output_rate=DEFAULT_OUTPUT_RATE):
        return (self.input_tokens / 1e6 * input_rate
                + self.output_tokens / 1e6 * output_rate)

    def render(self, input_rate=DEFAULT_INPUT_RATE, output_rate=DEFAULT_OUTPUT_RATE):
        delta = 100 * (1 - self.input_tokens / max(1, self.raw_tokens))
        note = ((f"({delta:.0f}% below raw text after stripping code blocks)")
                if delta > 0 else
                (f"({-delta:.0f}% above raw text: locator prefixes and skeletons; "
                 "stripping only pays off on long threads)"))
        return "\n".join([
            f"  conversations   {self.conversations}",
            f"  messages        {self.messages}",
            f"  segments        {self.segments}",
            f"  raw tokens      {self.raw_tokens:,}",
            f"  billed input    {self.input_tokens:,}  {note}",
            f"    of which segmentation: {self.segmentation_tokens:,}",
            f"  est. output     {self.output_tokens:,}",
            f"  ESTIMATED COST  ${self.cost(input_rate, output_rate):.2f}  "
            f"(at ${input_rate}/${output_rate} per M tokens)",
            "  Verify rates in the console before a large run.",
        ])


def estimate(conversations, segments_by_conv, strip_code: bool = True) -> Estimate:
    est = Estimate()
    for conv in conversations:
        est.conversations += 1
        est.messages += len(conv.messages)
        est.raw_chars += conv.char_count
        segs = segments_by_conv.get(conv.id, [])
        est.segments += len(segs)
        if len(segs) > 1:
            est.segmentation_chars += len(skeleton(conv))
        for seg in segs:
            msgs = conv.span(seg.start_id, seg.end_id)
            est.stripped_chars += len(strip_for_budget(msgs, strip_code))
    return est

"""Local-only vocabulary for context planning (never downloads in a request)."""

from functools import lru_cache
import hashlib
from pathlib import Path

_VOCABULARY = Path(__file__).with_name("assets") / "cl100k_base.tiktoken"
_SHA256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"


@lru_cache(maxsize=1)
def context_encoding():
    """Return the pinned OpenAI tokenizer, or None for conservative fallback."""
    try:
        import base64

        import tiktoken

        data = _VOCABULARY.read_bytes()
        if hashlib.sha256(data).hexdigest() != _SHA256:
            return None
        ranks = {
            base64.b64decode(token): int(rank)
            for token, rank in (line.split() for line in data.splitlines() if line)
        }
        return tiktoken.Encoding(
            name="deeptutor_cl100k_base",
            pat_str=r"'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}++|\p{N}{1,3}+| ?[^\s\p{L}\p{N}]++[\r\n]*+|\s++$|\s*[\r\n]|\s+(?!\S)|\s",
            mergeable_ranks=ranks,
            special_tokens={
                "<|endoftext|>": 100257,
                "<|fim_prefix|>": 100258,
                "<|fim_middle|>": 100259,
                "<|fim_suffix|>": 100260,
                "<|endofprompt|>": 100276,
            },
        )
    except (ImportError, OSError, ValueError):
        return None

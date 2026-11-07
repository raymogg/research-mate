"""Starter papers: what visitors can read before signing in, and what an empty library suggests.

The foundations of modern LLMs, in reading order. Visitors read them through
/api/starter (nothing is recorded); a signed-in reader adds them like any other
paper, with source "suggested".
"""

from dataclasses import asdict, dataclass

from . import arxiv

# Bump when the list changes: it's logged as the impression policy, so the
# research data says which shelf a reader saw.
POLICY = "starter:llm-foundations-v1"


@dataclass(frozen=True)
class Starter:
    arxiv_id: str
    title: str
    authors: str
    year: int
    why: str  # one line on what the paper introduced


STARTER = (
    Starter(
        "1706.03762", "Attention Is All You Need", "Vaswani et al.", 2017,
        "The Transformer: attention alone, no recurrence. The architecture behind every modern LLM.",
    ),
    Starter(
        "1810.04805", "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
        "Devlin et al.", 2018,
        "Pre-train once on unlabeled text, then fine-tune: the recipe that made pre-training standard.",
    ),
    Starter(
        "2001.08361", "Scaling Laws for Neural Language Models", "Kaplan et al.", 2020,
        "Loss falls predictably with model size, data and compute, which made scaling up a plan.",
    ),
    Starter(
        "2005.14165", "Language Models are Few-Shot Learners", "Brown et al.", 2020,
        "GPT-3: at 175B parameters, a model picks up new tasks from a few examples in its prompt.",
    ),
    Starter(
        "2203.02155", "Training language models to follow instructions with human feedback", "Ouyang et al.", 2022,
        "InstructGPT: fine-tuning on human preferences (RLHF) turns a language model into an assistant.",
    ),
)

BY_ID = {s.arxiv_id: s for s in STARTER}


def find(key: str) -> Starter | None:
    return BY_ID.get(arxiv.from_key(key))


def listing() -> dict:
    return {
        "policy": POLICY,
        "papers": [{**asdict(s), "key": arxiv.to_key(s.arxiv_id)} for s in STARTER],
    }

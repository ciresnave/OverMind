# SPDX-License-Identifier: MIT OR Apache-2.0
"""Summarise research papers with a free-tier model, and CHECK what it says.

    python probe/paper_summaries.py <texts_dir> <sources.json> <out.json>

Written for the AI-to-AI communication reading list (2026-09-16). The bulk
reading goes to a non-Claude, long-context model; the harness keeps the part a
cheap model cannot be trusted with: whether the numbers it reports are in the
paper at all.

⚠️ EVERY MEASURED GAIN MUST ARRIVE WITH A VERBATIM QUOTE, AND THE QUOTE IS
CHECKED AGAINST THE EXTRACTED TEXT. A summariser that invents "37% fewer
tokens" produces a sentence indistinguishable from one that read it. The check
is mechanical: whitespace-normalised substring match, plus every number in the
claim must appear in its quote. A gain that fails is kept and MARKED, never
silently dropped - a reader deciding what to build needs to know which figures
nobody has verified.

⚠️ THE CHECK PROVES THE QUOTE EXISTS, NOT THAT IT WAS READ CORRECTLY. A real
sentence attached to the wrong benchmark passes. Stated rather than solved.

The paper texts are NOT stored in this repository.
"""

from __future__ import annotations

import difflib
import json
import os
import pathlib
import re
import sys
import time
import unicodedata

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from overmind.providers import NoUsableModel, ProviderClient  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

SYSTEM = """You summarise one research paper for engineers designing efficient
communication between AI agents. Use ONLY the paper text provided. Do not use
outside knowledge. Do not recommend designs.

Reply with ONE JSON object and nothing else:
{
  "title": "exact paper title",
  "problem": "1-2 sentences",
  "mechanism": "2-4 sentences: what is actually exchanged between agents/models and how",
  "access": ["api-tokens" | "model-internals" | "training"],
  "access_detail": "1 sentence: what exactly must be reachable (e.g. hidden states of layer k, KV cache, fine-tuned weights, only text via an API)",
  "gains": [
    {"claim": "short statement of the measured result, with its number",
     "metric": "the paper's own metric name",
     "benchmark": "dataset/task/setting it was measured on",
     "baseline": "what it is compared against",
     "quote": "an EXACT contiguous excerpt copied from the paper text that contains the number"}
  ],
  "limitations": "1-3 sentences, including ones the authors state",
  "tier": "token" | "latent" | "neither"
}

Rules:
- "access" lists every kind of access the method REQUIRES. api-tokens = works
  with only text in/out of a hosted model. model-internals = needs hidden
  states, embeddings, KV caches or logits. training = needs fine-tuning or
  training any model.
- "tier": token = the communication happens in tokens/text (including
  structured or compressed text); latent = it happens in hidden states,
  embeddings or KV caches; neither = survey, position paper, protocol
  specification without either, or not about agent communication.
- "gains": ONLY results the paper MEASURED, at most 6, most important first.
  Each quote must be copied character-for-character from the text below,
  15-300 characters, and must contain the claim's number. If the paper reports
  no measurements, use [].
- If the paper is not about communication between AI agents or models, say so
  in "problem" and use tier "neither".
"""

LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi",
             "ﬄ": "ffl", "’": "'", "‘": "'", "“": '"',
             "”": '"', "–": "-", "—": "-", "−": "-",
             "×": "x", " ": " "}


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    for a, b in LIGATURES.items():
        text = text.replace(a, b)
    text = re.sub(r"-\s*\n\s*", "", text)          # hyphenation across lines
    return re.sub(r"\s+", " ", text).strip().lower()


#: ⚠️ A NUMBER GLUED TO A LETTER IS A NAME, NOT A MEASUREMENT. "GPT-3.5" and
#: "Qwen2.5" put 3.5 and 2.5 on the list of figures the quote had to contain,
#: and a correct gain was reported unverified.
NUMBER = re.compile(r"(?<![A-Za-z\d.])(?<![A-Za-z]-)\d*\.?\d+(?:[.,]\d+)?")


def claim_numbers(claim: str) -> list[str]:
    return NUMBER.findall(claim)


def number_in(n: str, text: str) -> bool:
    """⚠️ `0.453` and `.453` are the same figure. APA-style papers drop the
    leading zero, and the first run reported a real result as appearing
    NOWHERE in its paper - the most alarming verdict, and a false one."""
    text = text.replace(",", "")
    n = n.replace(",", "")
    forms = {n, n[1:] if n.startswith("0.") else "0" + n if n.startswith(".") else n}
    return any(re.search(r"(?<![\d])" + re.escape(f) + r"(?![\d])", text) for f in forms)


def check_gain(gain: dict, paper: str) -> str:
    """'verified', or the reason it is not."""
    quote = normalise(str(gain.get("quote", "")))
    if len(quote) < 10:
        return "no quote"
    # PDF extraction drops or inserts spaces around symbols; compare both ways
    if quote not in paper and quote.replace(" ", "") not in paper.replace(" ", ""):
        return "quote not found in paper"
    q = quote.replace(" ", "")
    nums = claim_numbers(str(gain.get("claim", "")))
    missing = [n for n in nums if not number_in(n, q)]
    if missing:
        return f"claim numbers {missing} not in quote"
    # ⚠️ A CLAIM WITH NO FIGURE PASSES THE NUMBER CHECK VACUOUSLY. "LMNet
    # outperforms LoRA" with a table row as its quote was reported verified,
    # when all that was verified is that the row exists.
    return "verified" if nums else "quote verified; the claim states no figure"


def diagnose(gain: dict, paper: str) -> str:
    """Why a quote failed: extraction noise, paraphrase, or a number the paper
    never states. ⚠️ The last is the one that matters most - it is what an
    invented figure looks like."""
    quote = normalise(str(gain.get("quote", "")))
    nums = claim_numbers(str(gain.get("claim", "")))
    absent = [n for n in nums if not number_in(n, paper)]
    if nums and len(absent) == len(nums):
        return f"number(s) {absent} appear NOWHERE in the paper"
    if quote in paper or quote.replace(" ", "") in paper.replace(" ", ""):
        # the quote is real; the claim carries a figure the quote does not
        return ("quote is verbatim; the extra figure(s) "
                + ("appear elsewhere in the paper" if not absent
                   else f"{absent} appear NOWHERE in the paper"))
    best = 0.0
    for n in nums or [""]:
        for m in list(re.finditer(re.escape(n), paper))[:40] if n else []:
            lo = max(0, m.start() - len(quote))
            window = paper[lo:m.end() + len(quote)]
            matcher = difflib.SequenceMatcher(None, quote, window, autojunk=False)
            # ⚠️ IN-ORDER BLOCKS OF 4+ CHARACTERS, NOT THE LONGEST RUN. One
            # typo mid-quote halves the longest run and read as a paraphrase
            # in the control; short blocks would count scattered letters.
            covered = sum(b.size for b in matcher.get_matching_blocks() if b.size >= 4)
            best = max(best, covered / max(1, len(quote)))
    kind = ("near-verbatim (likely extraction noise)" if best >= 0.85
            else "paraphrased, number present in paper")
    extra = f"; {absent} absent" if absent else ""
    return f"{kind}: {best:.0%} of quote matches in order{extra}"


def recheck(results: dict, texts: pathlib.Path) -> None:
    for r in results.values():
        s = r.get("summary")
        if not s:
            continue
        paper = normalise((texts / r["source"]["text"]).read_text(encoding="utf-8"))
        for gain in s.get("gains") or []:
            gain["check"] = check_gain(gain, paper)
            if gain["check"] not in ("verified", NO_FIGURE):
                gain["check"] += " - " + diagnose(gain, paper)


def parse(content: str) -> dict:
    content = content.strip()
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
    start, end = content.find("{"), content.rfind("}")
    return json.loads(content[start:end + 1])


def summarise(client: ProviderClient, text: str) -> tuple[dict, dict]:
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": "PAPER TEXT:\n\n" + text}]
    last = None
    for wait in (0, 60, 120, 240):
        if wait:
            print(f"    rate-limited; waiting {wait}s", flush=True)
            time.sleep(wait)
        try:
            result = client.chat(messages, max_tokens=8192, retries_on_429=1)
        except NoUsableModel as exc:
            last = exc
            if all(reason == "rate-limited" for _, reason in exc.attempts):
                continue
            raise
        content = result.message.get("content") or ""
        meta = {"provider": result.provider, "model": result.model,
                "finish_reason": result.finish_reason,
                "latency_s": round(result.latency_s, 1),
                "usage": vars(result.usage) if hasattr(result.usage, "__dict__") else str(result.usage)}
        return parse(content), meta
    raise last


NO_FIGURE = "quote verified; the claim states no figure"


def mark(gain: dict) -> str:
    check = gain.get("check", "")
    if check == "verified":
        return "✅"
    quote = str(gain.get("quote", "")).replace("\n", " ")[:220]
    if check == NO_FIGURE:
        return f"ℹ️ {check}: “{quote}”"
    return f"⚠️ {check}: “{quote}”"


def render(results: dict, sources: list[dict]) -> str:
    """The summary file. Order follows the source list, not the run."""
    lines: list[str] = []
    models = sorted({r["meta"]["model"] for r in results.values() if "meta" in r})
    gains = [g for r in results.values() for g in (r.get("summary") or {}).get("gains") or []]
    ok = sum(g.get("check") == "verified" for g in gains)
    nofig = sum(g.get("check") == NO_FIGURE for g in gains)
    lines += [f"Summarised by `{'`, `'.join(models)}` (Google AI Studio free tier), one call "
              f"per paper at temperature 0, via `probe/paper_summaries.py`. Of "
              f"{len(gains)} reported gains: **{ok} ✅** carry a figure found in a quote that "
              f"appears verbatim in the paper; **{nofig} ℹ️** have a verbatim quote but state "
              f"no figure; **{len(gains) - ok - nofig} ⚠️** failed the check, with the reason "
              f"and the quote shown. Every quote is in the companion `.json`.", ""]
    lines += ["| # | Source | Tier | Access |", "|---|---|---|---|"]
    for i, src in enumerate(sources, 1):
        s = (results.get(src["key"]) or {}).get("summary")
        tier = f"`{s.get('tier')}`" if s else "**not summarised**"
        access = ", ".join(s.get("access") or []) if s else "-"
        lines.append(f"| {i} | [{src['cite']}]({src['url']}) | {tier} | {access} |")
    lines.append("")
    for i, src in enumerate(sources, 1):
        r = results.get(src["key"]) or {}
        s = r.get("summary")
        title = s.get("title") if s else src["cite"]
        lines += [f"## {i}. {title}", "", f"[{src['cite']}]({src['url']})"
                  + (f" · `{r['meta']['model']}`" if "meta" in r else ""), ""]
        if src.get("note"):
            lines += [f"> ⚠️ {src['note']}", ""]
        if not s:
            lines += [f"**Not summarised.** {r.get('error', '')}".rstrip(), ""]
            continue
        lines += [f"- **Problem:** {s.get('problem', '')}",
                  f"- **Mechanism:** {s.get('mechanism', '')}",
                  f"- **Access needed:** {', '.join(s.get('access') or [])} — "
                  f"{s.get('access_detail', '')}",
                  "- **Measured gains:**" if s.get("gains") else
                  "- **Measured gains:** none reported"]
        for g in s.get("gains") or []:
            lines += [f"  - {g.get('claim', '')} — *metric:* {g.get('metric', '')}; "
                      f"*benchmark:* {g.get('benchmark', '')}; *vs:* {g.get('baseline', '')}. "
                      f"{mark(g)}"]
        lines += [f"- **Limitations:** {s.get('limitations', '')}",
                  f"- **Tier:** `{s.get('tier', '')}`", ""]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if argv and argv[0] == "recheck":
        path = pathlib.Path(argv[2])
        results = json.loads(path.read_text(encoding="utf-8"))
        recheck(results, pathlib.Path(argv[1]))
        path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        for key, r in results.items():
            for g in (r.get("summary") or {}).get("gains") or []:
                print(f"{key:36} {g['check'][:110]}")
        return 0
    if argv and argv[0] == "render":
        results = json.loads(pathlib.Path(argv[1]).read_text(encoding="utf-8"))
        sources = json.loads(pathlib.Path(argv[2]).read_text(encoding="utf-8"))
        print(render(results, sources))
        return 0
    texts, sources_path, out_path = map(pathlib.Path, argv[:3])
    provider = argv[3] if len(argv) > 3 else "google"
    sources = json.loads(sources_path.read_text(encoding="utf-8"))
    out = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    client = ProviderClient(provider, timeout=300.0)
    for src in sources:
        key = src["key"]
        if key in out and "summary" in out[key]:
            continue
        if not src.get("text"):
            out[key] = {"source": src}
            continue
        raw = (texts / src["text"]).read_text(encoding="utf-8")
        print(f"{key}: {len(raw)} chars", flush=True)
        try:
            summary, meta = summarise(client, raw)
        except Exception as exc:  # noqa: BLE001 - recorded per paper
            out[key] = {"source": src, "error": f"{type(exc).__name__}: {exc}"[:500]}
            print(f"    FAILED {out[key]['error']}", flush=True)
            out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
            continue
        paper = normalise(raw)
        for gain in summary.get("gains") or []:
            gain["check"] = check_gain(gain, paper)
        out[key] = {"source": src, "summary": summary, "meta": meta}
        checks = [g["check"] for g in summary.get("gains") or []]
        print(f"    {meta['model']} tier={summary.get('tier')} gains={len(checks)} "
              f"verified={checks.count('verified')}", flush=True)
        out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
        time.sleep(8)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

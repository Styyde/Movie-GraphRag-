import json


def build_context(results, evidence, coverage, budget):
    """Never truncate a proof: retain complete per-result bundles within the budget."""
    kept_results, kept_evidence = [], []
    for result in results:
        refs = set(result["evidence_ids"])
        bundle = [e for e in evidence if e.id in refs]
        candidate = {"results": kept_results + [result],
                     "evidence": [e.model_dump() for e in kept_evidence + bundle],
                     "coverage": coverage.model_dump()}
        if len(json.dumps(candidate, ensure_ascii=False)) > budget:
            break
        kept_results.append(result)
        kept_evidence.extend(bundle)
    return {"results": kept_results, "evidence": [e.model_dump() for e in kept_evidence],
            "coverage": coverage.model_dump()}, len(kept_results) < len(results)


def validate_generated(answer, evidence):
    allowed = {e["id"] for e in evidence}
    if not answer.statements:
        raise ValueError("Empty generated answer")
    for statement in answer.statements:
        if not set(statement.evidence_ids) <= allowed:
            raise ValueError("Answer cites unknown evidence")
    return "\n".join(s.text + " " + " ".join(f"[{e}]" for e in s.evidence_ids) for s in answer.statements)

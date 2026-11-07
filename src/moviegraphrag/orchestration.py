import logging
import time
import uuid

import httpx
from neo4j.exceptions import Neo4jError, ServiceUnavailable
from pydantic import ValidationError

from moviegraphrag.evidence.context import build_context, validate_generated
from moviegraphrag.evidence.validation import validate_assessments
from moviegraphrag.linking.clarifications import Clarifications
from moviegraphrag.linking.linker import EntityLinker
from moviegraphrag.llm.mistral import Usage
from moviegraphrag.llm.prompts import PROMPT_VERSION
from moviegraphrag.retrieval.compiler import route
from moviegraphrag.schemas.models import AnswerResponse, Coverage, Evidence, QuestionRequest

logger = logging.getLogger(__name__)


class MovieGraphRAG:
    def __init__(self, store, embedder, llm, settings, clarifications=None):
        self.store, self.embedder, self.llm, self.settings = store, embedder, llm, settings
        self.linker = EntityLinker(store)
        self.clarifications = clarifications or Clarifications()

    def ask(self, request: QuestionRequest) -> AnswerResponse:
        started = time.perf_counter()
        trace_id, usage = str(uuid.uuid4()), Usage()
        trace = {"prompt_version": PROMPT_VERSION, "pipeline_version": "controlled-v1"}
        response = None
        try:
            response = self._ask(request, trace_id, usage, trace)
        except (Neo4jError, ServiceUnavailable, httpx.HTTPError, RuntimeError, ImportError, OSError):
            logger.exception("dependency_error trace_id=%s", trace_id)
            response = AnswerResponse(status="dependency_error", trace_id=trace_id,
                                      answer="Une dépendance est indisponible. Consultez la configuration et la trace serveur.")
        except ValidationError:
            logger.exception("invalid_model_output trace_id=%s", trace_id)
            response = AnswerResponse(status="unsupported", trace_id=trace_id,
                                      answer="La question n'a pas pu être représentée par un plan valide.")
        finally:
            trace["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
            trace["llm_usage"] = usage.report(self.settings)
            logger.info("request trace_id=%s latency_ms=%s llm_calls=%s", trace_id,
                        trace["latency_ms"], usage.calls)
        response.trace = trace
        return response

    def _ask(self, request, trace_id, usage, trace):
        ready = self.store.readiness()
        if not ready["ready"]:
            raise RuntimeError("Corpus is not ready; run ingestion")
        manifest = ready["manifest"]
        trace["snapshot_id"] = manifest["snapshot_id"]
        trace["source_hashes"] = {k: manifest[k] for k in ("movies_sha256", "credits_sha256")}
        trace["embedding"] = manifest.get("embedding")
        allowed = {}
        if request.clarification_token:
            plan, allowed = self.clarifications.verify(request.clarification_token, request.question)
        else:
            if request.selections:
                raise ValueError("Selections require a clarification token")
            plan = self.llm.analyze(request.question, usage)
        plan.limit = request.limit
        trace["plan"] = plan.model_dump(mode="json")
        if not plan.supported:
            return AnswerResponse(status="unsupported", answer=plan.unsupported_reason, trace_id=trace_id)
        links = self.linker.link(plan, request.selections, allowed)
        if any(r.status == "not_found" for r in links):
            return AnswerResponse(status="entity_not_found", resolved_entities=links, trace_id=trace_id,
                                  answer="Une entité ou son contexte explicite est absent du dataset.")
        if any(r.status == "ambiguous" for r in links):
            return AnswerResponse(status="needs_clarification", resolved_entities=links,
                                  clarification_token=self.clarifications.issue(request.question, plan, links),
                                  trace_id=trace_id, answer="Précisez l'entité parmi les candidats proposés.")
        bindings = {r.mention.ref: r.canonical_id for r in links}
        strategy = route(plan)
        coverage = Coverage(offset=request.offset)
        warnings = []
        if strategy == "GRAPH":
            candidates, template_id = self.store.graph_candidates(plan, bindings)
            trace["template_id"], trace["bindings"] = template_id, bindings
            coverage.total_candidates = len(candidates)
            page = candidates[request.offset:request.offset + request.limit]
            coverage.has_more = request.offset + len(page) < len(candidates)
            results, evidence = [], []
            for c in page:
                proof = self.store.witnesses(plan, bindings, c["id"])
                ev = Evidence(id=f"g{c['id']}", kind="relation", entity_id=c["id"], data=proof)
                evidence.append(ev)
                results.append({**c, "evidence_ids": [ev.id]})
        else:
            if request.offset:
                raise ValueError("Semantic recommendations are not pageable; offset must be zero")
            metadata = self.embedder.metadata()
            indexed = manifest.get("embedding", {})
            if any(metadata.get(k) != indexed.get(k) for k in ("model", "revision", "dimensions")):
                raise RuntimeError("Embedding model/revision differs from the indexed snapshot")
            vector = self.embedder.encode_query(plan.theme_request.query)
            if strategy == "HYBRID_GRAPH_FIRST":
                structural, template_id = self.store.graph_candidates(plan, bindings)
                trace["template_id"], trace["bindings"] = template_id, bindings
                coverage.total_candidates = len(structural)
                candidates = self.store.exact_scores([c["id"] for c in structural], vector) if structural else []
                coverage.scored = len(candidates)
                coverage.without_embedding = len(structural) - len(candidates)
            else:
                coverage.total_candidates = manifest["counts"]["movies"]
                coverage.without_embedding = manifest["counts"]["movies"] - manifest["embedded_documents"]
                candidates = self.store.vector_candidates(vector, self.settings.semantic_budget)
                coverage.scored = len(candidates)
                coverage.approximate = True
            results, evidence = [], []
            budget = min(len(candidates), self.settings.semantic_budget)
            for start in range(0, budget, self.settings.evidence_batch_size):
                batch = candidates[start:min(start + self.settings.evidence_batch_size, budget)]
                movies = [self.store.movie(c["id"]) for c in batch]
                records = [{k: m.get(k) for k in ("movie_id", "title", "overview", "keywords",
                                                  "keyword_ids", "snapshot_id", "text_coverage")} for m in movies]
                assessed = self.llm.assess(plan.theme_request, records, usage)
                try:
                    accepted = validate_assessments(assessed, records, plan.theme_request)
                except ValueError as exc:
                    raise RuntimeError("Invalid thematic evidence returned by the model") from exc
                coverage.examined += len(batch)
                by_id = {c["id"]: c for c in batch}
                accepted.sort(key=lambda pair: (
                    -sum(j.source in {"overview", "both"} for j in pair[0].judgments),
                    -by_id[pair[0].movie_id]["score"], pair[0].movie_id))
                for assessment, proofs in accepted:
                    if len(results) >= request.limit:
                        break
                    c = by_id[assessment.movie_id]
                    if strategy == "HYBRID_GRAPH_FIRST":
                        proofs.append(Evidence(id=f"g{c['id']}", kind="relation", entity_id=c["id"],
                                               data=self.store.witnesses(plan, bindings, c["id"])))
                    evidence.extend(proofs)
                    results.append({**c, "judgments": [j.model_dump() for j in assessment.judgments],
                                    "evidence_ids": [e.id for e in proofs]})
                if len(results) >= request.limit:
                    break
            incomplete = (coverage.approximate or coverage.examined < len(candidates)
                          or coverage.without_embedding > 0)
            coverage.semantic_coverage = "partial" if incomplete else "complete"
            if coverage.semantic_coverage == "partial":
                warnings.append("Sélection sémantique partielle ; aucune exhaustivité n'est garantie.")
            if coverage.without_embedding:
                warnings.append(f"{coverage.without_embedding} film(s) sans embedding thématique.")
        coverage.returned = len(results)
        status = "ok" if results else ("no_match_in_dataset" if strategy == "GRAPH" or (
            strategy == "HYBRID_GRAPH_FIRST" and coverage.total_candidates == 0) else "insufficient_evidence")
        context, truncated = build_context(results, evidence, coverage, self.settings.context_char_budget)
        if truncated:
            warnings.append("Contexte de synthèse limité ; les résultats structurés conservent toutes leurs preuves.")
        if results:
            answer = "\n".join(f"- {r['name']} " + " ".join(f"[{i}]" for i in r["evidence_ids"])
                               for r in results)
            if strategy != "GRAPH":
                answer += "\nLes annotations keywords indiquent une association, sans prouver la centralité."
            if strategy != "GRAPH" and self.settings.synthesis_enabled and context["results"] and not truncated:
                try:
                    generated = self.llm.generate(context, usage)
                    answer = validate_generated(generated, context["evidence"])
                except (ValueError, RuntimeError, httpx.HTTPError):
                    warnings.append("Synthèse indisponible ou invalide ; rendu déterministe utilisé.")
        else:
            answer = ("Aucun résultat dans ce dataset pour les contraintes demandées." if status == "no_match_in_dataset"
                      else "Preuves thématiques insuffisantes parmi les candidats examinés.")
        return AnswerResponse(status=status, strategy=strategy, resolved_entities=links, results=results,
                              answer=answer, evidence=evidence, coverage=coverage, warnings=warnings,
                              trace_id=trace_id)

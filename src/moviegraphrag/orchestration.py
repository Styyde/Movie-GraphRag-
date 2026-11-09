import logging
import time
import uuid

import httpx
from neo4j.exceptions import Neo4jError, ServiceUnavailable
from pydantic import ValidationError

from moviegraphrag.evidence.context import build_context, validate_generated
from moviegraphrag.evidence.validation import document_evidence
from moviegraphrag.linking.clarifications import Clarifications
from moviegraphrag.linking.linker import EntityLinker
from moviegraphrag.llm.groq import Usage
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
        trace = {"prompt_version": PROMPT_VERSION, "pipeline_version": "controlled-v2",
                 "generation_mode": "deterministic"}
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
        allowed, confirmed = {}, {}
        if request.clarification_token:
            plan, allowed, confirmed = self.clarifications.verify(request.clarification_token, request.question)
            if set(request.selections) - set(allowed):
                raise ValueError("Only new selections proposed by this clarification are accepted")
        else:
            if request.selections:
                raise ValueError("Selections require a clarification token")
            plan = self.llm.analyze(request.question, usage)
        limit = min(plan.requested_limit or request.limit, 20)
        warnings = []
        if plan.operation != "count" and plan.requested_limit and plan.requested_limit > 20:
            warnings.append("La quantité demandée est limitée à 20 résultats par réponse.")
        trace["requested_limit"] = plan.requested_limit
        trace["effective_limit"] = None if plan.operation == "count" else limit
        trace["plan"] = plan.model_dump(mode="json")
        if not plan.supported:
            return AnswerResponse(status="unsupported", answer=plan.unsupported_reason, trace_id=trace_id)
        # Confirmed choices come only from the signed token, never from the client.
        # Re-run linking for every choice so source/context changes cannot bypass validation.
        selections = {**confirmed, **request.selections}
        authorized = {**allowed, **{ref: [entity_id] for ref, entity_id in confirmed.items()}}
        links = self.linker.link(plan, selections, authorized)
        if any(r.status == "not_found" for r in links):
            return AnswerResponse(status="entity_not_found", resolved_entities=links, trace_id=trace_id,
                                  answer="Une entité ou son contexte explicite est absent du dataset.")
        if any(r.status == "ambiguous" for r in links):
            clarification = ("Précisez le nom complet ou un contexte explicite : la liste de candidats est trop large."
                             if any(r.truncated for r in links) else
                             "Précisez d'abord les entités ambiguës parmi les candidats proposés.")
            return AnswerResponse(status="needs_clarification", resolved_entities=links,
                                  clarification_token=self.clarifications.issue(request.question, plan, links),
                                  trace_id=trace_id, answer=clarification)
        bindings = {r.mention.ref: r.canonical_id for r in links}
        strategy = route(plan)
        result_type = next(variable.label.value for variable in plan.variables if variable.ref == plan.target)
        coverage = Coverage(offset=request.offset, effective_limit=None if plan.operation == "count" else limit)
        if plan.operation == "count":
            if request.offset:
                raise ValueError("Counts are not pageable; offset must be zero")
            count, template_id = self.store.graph_count(plan, bindings)
            trace.update(template_id=template_id, bindings=bindings, selection_method="exact_graph_count")
            coverage.total_candidates = count
            proof = Evidence(id="a-count", kind="aggregate", data={
                "operation": "count_distinct", "value": count, "target_type": result_type,
                "template_id": template_id, "bindings": bindings,
                "snapshot_id": manifest["snapshot_id"], "source_hashes": trace["source_hashes"],
            })
            return AnswerResponse(status="ok", operation="count", strategy="GRAPH", count=count,
                                  resolved_entities=links, evidence=[proof], coverage=coverage,
                                  answer=f"Dans ce dataset : {count} résultat(s) distinct(s). [a-count]",
                                  trace_id=trace_id)
        if strategy == "GRAPH":
            trace["selection_method"] = "exact_graph_constraints"
            candidates, template_id = self.store.graph_candidates(plan, bindings)
            trace["template_id"], trace["bindings"] = template_id, bindings
            coverage.total_candidates = len(candidates)
            page = candidates[request.offset:request.offset + limit]
            coverage.has_more = request.offset + len(page) < len(candidates)
            results, evidence = [], []
            for c in page:
                proof = self.store.witnesses(plan, bindings, c["id"])
                ev = Evidence(id=f"g{c['id']}", kind="relation", entity_id=c["id"], data=proof)
                evidence.append(ev)
                results.append({**c, "type": result_type, "evidence_ids": [ev.id]})
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
                scoring_started = time.perf_counter()
                candidates = self.store.exact_scores([c["id"] for c in structural], vector) if structural else []
                coverage.scored = len(candidates)
                coverage.without_embedding = len(structural) - len(candidates)
            else:
                coverage.total_candidates = manifest["counts"]["movies"]
                scoring_started = time.perf_counter()
                candidates = self.store.exact_scores(None, vector)
                coverage.scored = len(candidates)
                coverage.without_embedding = coverage.total_candidates - coverage.scored
            trace.update(selection_method="exact_cosine", scoring_ms=round((time.perf_counter() - scoring_started) * 1000, 2),
                         ranking_scope="structural_candidates" if strategy == "HYBRID_GRAPH_FIRST" else "corpus")
            results, evidence = [], []
            for c in candidates:
                if len(results) >= limit:
                    break
                coverage.sources_checked += 1
                proof = document_evidence(self.store.movie(c["id"]), c["id"], manifest)
                if proof is None:
                    continue
                proofs = [proof]
                if strategy == "HYBRID_GRAPH_FIRST":
                    proofs.append(Evidence(id=f"g{c['id']}", kind="relation", entity_id=c["id"],
                                           data=self.store.witnesses(plan, bindings, c["id"])))
                evidence.extend(proofs)
                results.append({**c, "type": result_type, "evidence_ids": [e.id for e in proofs]})
            coverage.ranking_coverage = "partial" if coverage.without_embedding else "complete"
            warnings.append("Recommandations classées par similarité ; pertinence thématique et centralité non certifiées.")
            if coverage.without_embedding:
                warnings.append(f"{coverage.without_embedding} film(s) sans embedding thématique.")
        coverage.returned = len(results)
        status = "ok" if results else ("no_match_in_dataset" if strategy == "GRAPH" or (
            strategy == "HYBRID_GRAPH_FIRST" and coverage.total_candidates == 0) else "insufficient_evidence")
        context, truncated = build_context(results, evidence, coverage, self.settings.context_char_budget,
                                           {"question": request.question, "language": request.language,
                                            "theme_request": plan.theme_request.model_dump() if plan.theme_request else None})
        trace["context_truncated"] = truncated
        trace["context_evidence_ids"] = [e["id"] for e in context["evidence"]]
        if truncated:
            warnings.append("Contexte de synthèse limité ; les résultats structurés conservent toutes leurs preuves.")
        if results:
            answer = "\n".join(f"- {r['name']} " + " ".join(f"[{i}]" for i in r["evidence_ids"])
                               for r in results)
            if strategy != "GRAPH":
                answer += "\nRecommandations par similarité ; la pertinence thématique et la centralité ne sont pas certifiées."
            if strategy != "GRAPH" and self.settings.synthesis_enabled and context["results"] and not truncated:
                try:
                    generated = self.llm.generate(context, usage)
                    answer = validate_generated(generated, context["evidence"])
                    trace["generation_mode"] = "llm_synthesis"
                except (ValueError, RuntimeError, httpx.HTTPError):
                    warnings.append("Synthèse indisponible ou invalide ; rendu déterministe utilisé.")
        else:
            answer = ("Aucun résultat dans ce dataset pour les contraintes demandées." if status == "no_match_in_dataset"
                      else "Sources disponibles insuffisantes pour produire des recommandations.")
        return AnswerResponse(status=status, strategy=strategy, operation=plan.operation,
                              thematic_verification="not_performed" if plan.operation == "recommend" else "not_applicable",
                              clarification_token=self.clarifications.issue(request.question, plan, links)
                              if plan.operation == "list" else None,
                              resolved_entities=links, results=results,
                              answer=answer, evidence=evidence, coverage=coverage, warnings=warnings,
                              trace_id=trace_id)

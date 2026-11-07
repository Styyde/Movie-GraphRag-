"""Reference plans for acceptance testing; natural-language production uses the analyzer."""
from moviegraphrag.schemas.models import QuestionDraft

QUESTIONS = {
    "Q1": "Quels films ont été réalisés par Christopher Nolan avec Cillian Murphy ?",
    "Q2": "Quels réalisateurs ont travaillé avec Leonardo DiCaprio ?",
    "Q3": "Quels acteurs ont joué dans des films réalisés par Christopher Nolan ?",
    "Q4": "Quels réalisateurs ont travaillé avec Leonardo DiCaprio et Cillian Murphy ?",
    "Q5": "Trouve des films sur l'exploration spatiale, la survie et les relations familiales.",
    "Q6": "Quels films explorent les conséquences de l'intelligence artificielle sur l'humanité ?",
    "Q7": "Recommande des films sur les rêves et la perception de la réalité.",
    "Q8": "Quels films de Nolan avec DiCaprio parlent des rêves ?",
    "Q9": "Quels films de science-fiction parlant d'intelligence artificielle ont été réalisés par des réalisateurs ayant travaillé avec Leonardo DiCaprio ?",
    "Q10": "Quels films explorant la survie ont été réalisés par des réalisateurs ayant collaboré avec Matt Damon ?",
}


def reference_plan(key: str) -> QuestionDraft:
    mentions, variables, edges = [], [{"ref": "result", "label": "Movie"}], []
    answer_type, theme = "movie_list", None
    def bind(ref, text, label="Person", role=None):
        mentions.append({"ref": "mention_" + ref, "text": text, "type": label, "role": role})
        variables.append({"ref": ref, "label": label, "mention_ref": "mention_" + ref})
    def edge(a, rel, b):
        edges.append({"source": a, "relation": rel, "target": b})
    def thematic(query, themes):
        return {"query": query, "themes": themes, "operator": "ALL", "requirement": "association"}
    if key in {"Q1", "Q8"}:
        bind("director", "Christopher Nolan", role="director")
        bind("actor", "Cillian Murphy" if key == "Q1" else "Leonardo DiCaprio", role="actor")
        edge("director", "DIRECTED", "result")
        edge("actor", "ACTED_IN", "result")
        if key == "Q8":
            theme = thematic("films parlant des rêves", ["rêves"])
    elif key in {"Q2", "Q3", "Q4"}:
        variables = [{"ref": "result", "label": "Person"}, {"ref": "witness", "label": "Movie"}]
        answer_type = "person_list"
        if key == "Q3":
            bind("director", "Christopher Nolan", role="director")
            edge("director", "DIRECTED", "witness")
            edge("result", "ACTED_IN", "witness")
        else:
            bind("actor", "Leonardo DiCaprio", role="actor")
            edge("actor", "ACTED_IN", "witness")
            edge("result", "DIRECTED", "witness")
            if key == "Q4":
                variables.append({"ref": "other", "label": "Movie"})
                bind("actor_b", "Cillian Murphy", role="actor")
                edge("actor_b", "ACTED_IN", "other")
                edge("result", "DIRECTED", "other")
    elif key in {"Q9", "Q10"}:
        variables += [{"ref": "director", "label": "Person"}, {"ref": "witness", "label": "Movie"}]
        bind("actor", "Leonardo DiCaprio" if key == "Q9" else "Matt Damon", role="actor")
        edge("actor", "ACTED_IN", "witness")
        edge("director", "DIRECTED", "witness")
        edge("director", "DIRECTED", "result")
        if key == "Q9":
            bind("genre", "Science Fiction", "Genre")
            edge("result", "IN_GENRE", "genre")
        theme = thematic("films parlant d'intelligence artificielle" if key == "Q9" else "films explorant la survie",
                         ["intelligence artificielle"] if key == "Q9" else ["survie"])
    elif key in {"Q5", "Q6", "Q7"}:
        ts = {"Q5": ["exploration spatiale", "survie", "relations familiales"],
              "Q6": ["conséquences de l'intelligence artificielle sur l'humanité"],
              "Q7": ["rêves", "perception de la réalité"]}[key]
        theme = thematic(QUESTIONS[key], ts)
    else:
        raise ValueError(key)
    return QuestionDraft(mentions=mentions, variables=variables, edges=edges, answer_type=answer_type,
                         theme_request=theme)

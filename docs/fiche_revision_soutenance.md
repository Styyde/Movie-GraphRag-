# MovieGraphRAG — fiche de révision

Cette fiche décrit l'architecture adaptée. Le [dossier complet](architecture_complete_soutenance.md) contient les diagrammes détaillés et le rôle de chaque fichier.

## Présentation en 45 secondes

> MovieGraphRAG interroge TMDB 5000 en français. Neo4j conserve les relations entre personnes, films et genres ainsi que les documents et leurs vecteurs. Le graphe vérifie les collaborations et calcule les comptages exacts. E5 multilingue classe les films selon les thèmes demandés. Groq analyse la question puis peut rédiger une synthèse sourcée. Python contrôle les étapes et les requêtes.

## Les deux représentations

~~~text
CSV structurés → Person, Movie, Genre → ACTED_IN, DIRECTED, IN_GENRE
Titre + synopsis + genres + keywords → E5 → embedding sur Movie
~~~

Les embeddings ne construisent pas les relations. Un acteur et un réalisateur partagent le même type Person ; leurs relations indiquent les rôles. Aucun GNN ni fine-tuning.

## Trois opérations

| Question | Opération et résultat |
| --- | --- |
| Quels films de Nolan avec Murphy ? | list : quatre films dans le corpus. |
| Combien de films de Nolan avec Murphy ? | count : nombre 4, calculé par Neo4j. |
| Combien d'acteurs ont joué dans les films de Nolan ? | count : 509 personnes distinctes. |
| Recommande trois films sur l'IA | recommend : jusqu'à trois recommandations E5. |
| Quels films sur l'IA ? | recommend : limite de l'interface, cinq par défaut. |

Le LLM extrait une quantité explicitement demandée, pas le nombre de correspondances. La question prime sur l'interface ; plafond vingt avec avertissement. COUNT n'a aucun top-k.

Une personne connue sans relation correspondante peut donner zéro ; une personne absente donne entity_not_found. Un homonyme non résolu donne needs_clarification.

## Parcours à savoir expliquer

~~~text
Question
→ Groq : plan, mentions, opération et quantité
→ validation et identités
→ COUNT : agrégat exact
  ou LIST : motif puis pagination
  ou RECOMMEND : classement E5 exact
→ sources et contexte
→ rendu déterministe ou synthèse contrôlée
~~~

En hybride, toutes les contraintes du graphe déterminent les candidats avant leur sélection sémantique. Le vecteur de la requête peut être calculé avant cet appel ; Graph First décrit la sélection des films.

### Exemple Nolan, DiCaprio et les rêves

1. Analyse : réalisateur, acteur, film résultat et thème.
2. Linking : Nolan 525 et DiCaprio 6193, selon noms et contexte.
3. Motif : Nolan-DIRECTED->film<-ACTED_IN-DiCaprio.
4. Un candidat dans le corpus : Inception 27205.
5. Calcul de sa similarité avec la demande thématique.
6. Récupération de son document et de ses relations.
7. Contexte puis réponse, sans ajouter quatre films pour atteindre cinq.

Un seul score calculé ne signifie pas un score égal à 1.

### Exemple multi-sauts

~~~text
DiCaprio → film témoin ← réalisateur → film résultat → genre
~~~

DiCaprio n'a pas à jouer dans le film résultat. Spielberg a travaillé avec lui dans Catch Me If You Can et réalisé A.I. Artificial Intelligence.

## Chiffres à connaître

| Valeur | Signification |
| --- | --- |
| 4 803 / 4 801 | Films / documents encodés. |
| 56 603 | Personnes acteurs/réalisateurs. |
| 405 | Noms exacts associés à plusieurs IDs. |
| 563 | Personnes à la fois acteurs et réalisateurs. |
| 384 | Dimensions du vecteur. |
| 512 tokens | Fenêtre d'entrée E5, pas 512 mots ni taille de chunk automatique. |
| 48 / 175 mots | Médiane / maximum des synopsis non vides. |
| 500 / 32 | Lots d'écriture / d'encodage, pas découpage des synopsis. |
| 24 000 caractères | Budget JSON du contexte, pas nombre de tokens. |
| 23 / 254 | Candidats structurels des questions hybrides Q9 / Q10. |

## Chunking

Un document principal par film, sans découpage glissant ni overlap. Le texte contient titre, synopsis, genres et keywords. Le cast reste dans le graphe.

La tokenisation contrôle la fenêtre. Les derniers keywords peuvent être retirés du document ; le synopsis n'est pas coupé silencieusement. Sans synopsis mais avec keywords : metadata_only. Deux films n'ont aucun embedding.

Seule la baseline flat, qui ajoute les crédits, peut avoir plusieurs morceaux par film.

## Ce qui a changé

- Suppression de l'appréciation thématique Groq entre retrieval et génération.
- Scoring exact de tous les documents encodés, y compris en thématique pure.
- Plus de plafond de cent candidats ni lots de vingt inspections.
- Plus de jugement automatique central/peripheral.
- Pas de reranker pour cette version ; étude en V2.
- COUNT DISTINCT séparé des listes et recommandations.

## Questions du professeur

| Question | Réponse |
| --- | --- |
| Quelle approche GraphRAG ? | Proche de Text2Cypher, mais question → plan validé → Cypher construit par Python ; hybride Graph First. |
| Pourquoi Neo4j seul ? | Relations, documents et vecteurs partagent les mêmes IDs, sans synchronisation entre deux bases. |
| Quel algorithme de parcours ? | Recherche de motifs Cypher ; le moteur choisit le plan. Aucun BFS libre du graphe métier. |
| Avez-vous PageRank ou Leiden ? | Non, ni Jaccard ni plus court chemin. |
| Un DFS existe-t-il ? | Oui pour vérifier la connexion du petit plan, pas pour rechercher dans le corpus. |
| Pourquoi un embedding ? | Classer le sens des textes et rapprocher français/anglais ; il ne prouve pas une collaboration. |
| Recherche exacte signifie réponse vraie ? | Non : tous les scores sont calculés, mais la pertinence reste estimée. |
| Quelle forme de contexte ? | JSON avec résultats, documents, chemins relationnels, références et couverture. |
| Groq sélectionne-t-il encore les candidats ? | Non après le retrieval : E5 fixe leur ordre ; Groq peut rédiger à partir des sources sélectionnées. |
| Toutes les réponses sont-elles générées ? | Non : comptes et listes sont déterministes ; recommandations avec synthèse éventuelle. |
| Un keyword prouve-t-il la centralité ? | Non. Les exigences de centralité certifiée demandent une reformulation. |
| Les citations garantissent-elles toute la fidélité ? | Leur existence est contrôlée, leur implication sémantique exige une évaluation humaine. |
| Comment compte-t-on ? | COUNT DISTINCT sur le motif complet, sans LIMIT préalable. |
| Avez-vous entraîné un modèle ? | Non, modèles préentraînés utilisés sans fine-tuning. |
| Le reranker est-il indispensable ? | Non ; il faudra mesurer son gain face à E5 seul. |
| Un gain face au Vector RAG est-il démontré ? | Non, les annotations et le benchmark complet restent à terminer. |

## Fichiers à retrouver

Dans src/moviegraphrag/ :
- ingestion/dataset.py et loader.py : CSV, objets et MERGE.
- ingestion/documents.py : document et tokens.
- embeddings.py : E5 local, préfixes et normalisation.
- schemas/models.py : opérations et invariants.
- quantities.py : quantités et demandes non certifiables.
- linking/linker.py : identités.
- retrieval/compiler.py et neo4j_store.py : Cypher, comptes et scores.
- evidence/validation.py : intégrité documentaire.
- evidence/context.py : contexte et références.
- orchestration.py : parcours complet.
- llm/groq.py et prompts.py : analyse et synthèse.

## Démonstration courte

1. Lister les films Nolan–Murphy : quatre.
2. Compter ces films, puis les acteurs de Nolan : 4 puis 509.
3. Demander trois recommandations sur l'IA avec dix sélectionné dans l'interface.
4. Montrer Q8, ses sources et les relations.
5. Demander des films centrés sur l'IA : expliquer la reformulation.
6. Montrer la clarification de Tom Holland si le temps le permet.

Le réseau et le quota Groq peuvent échouer. Présenter les rapports historiques comme historiques ; ne pas faire passer une réponse préparée pour une exécution directe.

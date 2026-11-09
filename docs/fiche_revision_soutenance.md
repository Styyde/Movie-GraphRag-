# MovieGraphRAG — fiche de révision pour la soutenance

Cette fiche explique le fonctionnement actuel. Le [dossier complet](architecture_complete_soutenance.md)
décrit aussi chaque fichier, les contrats, les tests et les limites.

## 1. Présentation en 45 secondes

> Mon application répond à des questions en français sur TMDB 5000. Elle combine
> un graphe Neo4j pour vérifier les relations entre personnes, films et genres,
> et des embeddings multilingues pour rechercher les thèmes des synopsis anglais.
> Un LLM accessible via Groq analyse la question et apprécie les preuves thématiques.
> Un pipeline Python contrôlé exécute les recherches. Les réponses présentent leurs
> sources ; les recherches sémantiques signalent leur couverture partielle.

## 2. La distinction à bien comprendre

Les embeddings **ne construisent pas les relations du graphe**. Deux représentations
sont préparées à partir des données, puis conservées dans la même base Neo4j :

```text
CSV crédits et métadonnées
  → nœuds Person, Movie, Genre
  → relations ACTED_IN, DIRECTED, IN_GENRE

Titre + synopsis + genres + keywords d’un film
  → modèle E5 local
  → vecteur de 384 nombres
  → propriété embedding du même nœud Movie
```

Le nœud conserve ses informations sources en plus de son vecteur. Les personnes et
genres ne reçoivent pas d’embedding dans ce projet. Un lien ACTED_IN existe parce
que le CSV mentionne le crédit, pas parce que deux vecteurs sont proches.

**Pourquoi est-ce un GraphRAG ?** Le graphe intervient réellement dans le retrieval :
ses relations sélectionnent les résultats et fournissent des chemins témoins au
contexte. La recherche sémantique apporte les thèmes, et le générateur peut exploiter
ces preuves. L’origine structurée du graphe ne change pas ce principe. La
[documentation GraphRAG de Neo4j](https://neo4j.com/docs/neo4j-graphrag-python/current/user_guide_rag.html)
présente plusieurs combinaisons de retrieval vectoriel et de traversées.

L’application propose aussi un parcours VECTOR sans traversée métier et un parcours
GRAPH dont la restitution est déterministe. « GraphRAG hybride » décrit l’architecture
globale, sans prétendre que chaque question exécute les mêmes étapes.

## 3. Comment choisir les nœuds ?

Il existe deux mécanismes différents :

- **Une entité nommée**, comme « Nolan » : recherche du nom normalisé, des alias,
  du patronyme ou du full-text, puis vérification du contexte explicite. Le résultat
  est un ID TMDB. Les homonymes non départagés demandent une clarification.
- **Un thème**, comme « intelligence artificielle » : E5 encode le texte de recherche
  thématique ; la similarité avec les vecteurs de films sert à les classer.

Le LLM analyse la question en plan déclaratif. Il ne fournit ni les IDs de la base
ni du Cypher libre. Python valide le plan, résout les noms, puis compile des motifs
autorisés en requêtes paramétrées.

### Les trois parcours

| Question | Parcours |
| --- | --- |
| Films réalisés par Nolan avec Murphy | GRAPH : identités, relations, résultats et témoins. Aucun embedding de question nécessaire. |
| Films sur les rêves | VECTOR : E5, voisins approximatifs dans l’index, vérification des sources. |
| Films de Nolan avec DiCaprio parlant des rêves | HYBRID_GRAPH_FIRST : contraintes du graphe, scoring exact de tous les candidats encodés, vérification des sources. |

L’index vectoriel retourne directement les nœuds Movie associés aux vecteurs
retrouvés. Il n’est pas nécessaire de deviner à quel nœud appartient un vecteur.

## 4. Exemple expliqué étape par étape

**Question : « Quels films de Nolan avec DiCaprio parlent des rêves ? »**

1. Groq extrait deux personnes, les relations DIRECTED et ACTED_IN, une cible Movie
   et le thème « rêves ».
2. Le linking identifie Christopher Nolan `525` et Leonardo DiCaprio `6193` dans la base.
3. Le Router choisit HYBRID_GRAPH_FIRST.
4. Neo4j cherche le motif :

   ```text
   Nolan ─DIRECTED→ film ←ACTED_IN─ DiCaprio
   ```

5. Ce snapshot fournit un seul film admissible : Inception `27205`.
6. E5 encode la demande thématique avec le préfixe `query: ` ; le document du film
   avait été encodé avec `passage: ` lors de l’ingestion.
7. Neo4j calcule la similarité entre les deux vecteurs. Un seul candidat scoré ne
   signifie pas un score égal à 1. Dans le code, le vecteur de requête est calculé
   avant l’appel de sélection structurelle ; seuls les films admissibles sont scorés.
8. Groq examine le synopsis et les keywords fournis. Le keyword `dream` (`1566`)
   constitue un appui thématique vérifiable.
9. Python vérifie l’existence des citations et keywords, et conserve le chemin
   relationnel témoin. La similarité seule ne suffit pas à accepter le film.
10. Le Context Builder assemble question, film, preuves et couverture. Une synthèse
    française peut être générée ; un rendu déterministe reste disponible.

Neo4j exécute un motif précis. Le LLM ne se promène pas librement dans le graphe.
Pour une question plus complexe, plusieurs films peuvent jouer le rôle de témoins.
Le film témoin d’une collaboration et le film résultat ne sont pas nécessairement identiques.

## 5. Chunking, tokens et lots

**Le corpus principal utilise un document par film**, sans découpage glissant ni overlap.
Les synopsis sont courts : médiane 48 mots, maximum 175 mots.

Le document contient titre, synopsis, genres et keywords. Les crédits restent dans
le graphe. Le tokenizer vérifie la fenêtre de 512 tokens, préfixe et tokens spéciaux
inclus. En cas de dépassement, le code retire des keywords ; si cela ne suffit pas,
il signale une erreur sans couper silencieusement le synopsis. Le manifeste conservé
indique qu’aucun document n’a nécessité ce retrait lors de l’ingestion enregistrée.

| Nombre | Signification |
| --- | --- |
| 4 803 | Films accessibles au graphe. |
| 4 801 | Documents encodés ; deux films sans synopsis utilisent leurs métadonnées, deux autres n’ont pas de vecteur. |
| 384 | Composantes d’un vecteur E5. |
| 512 tokens | Fenêtre d’entrée E5, pas un nombre de mots ni une taille de chunk systématique. |
| 500 objets | Lot technique d’écriture, pas découpage des synopsis. |
| 32 documents | Lot d’encodage E5. |
| 20 candidats | Lot de vérification thématique Groq. |
| 100 candidats | Budget maximal initial de vérification thématique, pas limite du scoring hybride. |
| 24 000 caractères | Budget du contexte JSON de synthèse, pas 24 000 tokens. |

La baseline d’évaluation `flat` ajoute les crédits au texte. Elle peut créer plusieurs
morceaux par film pour respecter la fenêtre. Ce comportement ne décrit pas le corpus
principal. Le score du film y est le maximum des scores de ses morceaux.

## 6. Classement, preuves et réponse

Il n’y a **pas de cross-encoder spécialisé de reranking**. Il existe néanmoins
plusieurs opérations après la recherche initiale :

1. Classement des candidats par similarité.
2. Vérification LLM des appuis thématiques et rejet des candidats insuffisamment justifiés.
3. Dans chaque lot, priorité aux films avec davantage de thèmes appuyés par le synopsis,
   puis au score de similarité, puis à l’ID.

Ce dernier tri est local au lot. L’arrêt après le nombre demandé ne garantit pas
un reclassement final optimal sur tous les candidats.

Une proximité vectorielle est un signal de recherche, pas une preuve. Un keyword
prouve une association annotée, pas la centralité du thème dans l’intrigue. La
centralité doit être soutenue par un extrait du synopsis disponible.

L’augmentation assemble des données récupérées et leurs preuves dans le contexte
envoyé au générateur. Elle ne réentraîne pas le modèle et n’ajoute pas automatiquement
de nouvelles connaissances au graphe.

Pour GRAPH, la réponse finale est rendue de façon déterministe. Pour VECTOR ou
HYBRID, Groq peut produire une synthèse. Le validateur contrôle les références,
mais il ne prouve pas que chaque phrase est correctement interprétée : la fidélité
sémantique reste une dimension d’évaluation.

## 7. Questions probables du professeur

| Question | Réponse à donner |
| --- | --- |
| Pourquoi un graphe ? | Pour appliquer et expliquer précisément les collaborations et les contraintes sur plusieurs relations. |
| Pourquoi des vecteurs en plus ? | Pour retrouver des thèmes et formulations proches sans exiger les mêmes mots. |
| Pourquoi Neo4j seul ? | Il conserve relations et vecteurs ; cela évite une seconde base et leur synchronisation. |
| Le LLM crée-t-il le graphe ? | Non : les CSV structurés fournissent les relations ; Python les normalise et les ingère. |
| Le LLM transforme-t-il la question en vecteur ? | E5 est le modèle d’encodage utilisé. Le modèle via Groq analyse la question et travaille sur les preuves. |
| Pourquoi chercher le graphe avant les vecteurs en hybride ? | Pour scorer tous les films satisfaisant les contraintes, sans perdre ceux absents d’un top vectoriel global limité. |
| Recherche vectorielle toujours exacte ? | Non : l’index natif sert à une recherche approximative pour VECTOR ; l’hybride calcule exactement chaque similarité admissible. |
| Que signifie « exact » ? | Calculer le score de chaque candidat encodé ; cela ne garantit ni une pertinence parfaite ni une couverture thématique exhaustive. |
| Est-ce un agent ? | Non : les étapes sont pilotées par Python, avec contrats et motifs autorisés. |
| Est-ce un GNN ? | Non : le raisonnement repose sur des motifs de graphe, sans apprentissage neuronal de sa structure. |
| Comment gérer Tom Holland ? | Conserver les candidats, vérifier un rôle/film explicite ou demander une clarification ; ne pas choisir selon la réponse obtenue. |
| Quel modèle est entraîné sur TMDB ? | Aucun : utilisation de modèles préentraînés, sans fine-tuning. |
| Comment évaluer ? | Références relationnelles indépendantes depuis les CSV ; jugements humains pour les thèmes ; qualité des citations, contraintes, latence et coûts. |
| Un gain face au Vector RAG est-il démontré ? | Le protocole et les baselines existent ; le benchmark thématique complet reste à annoter et exécuter. |
| Tous les tests sont-ils passés ? | Des campagnes backend réelles sont conservées, avec reprises ciblées. Les tests navigateur sont écrits mais non exécutés ; les scénarios manuels restent à remplir. |

## 8. Fichiers à savoir retrouver pendant l’oral

| Fichier | Ce qu’il faut montrer |
| --- | --- |
| `ingestion/dataset.py` | Normalisation, IDs et relations extraites des CSV. |
| `ingestion/documents.py` | Unité de texte, tokens et absence de découpage glissant. |
| `embeddings.py` | E5, préfixes, encodage et normalisation. |
| `schemas/models.py` | Plan et contrats validés. |
| `linking/linker.py` | Résolution des mentions en identités. |
| `retrieval/compiler.py` | Router et compilation du plan en Cypher paramétré. |
| `retrieval/neo4j_store.py` | Schéma, candidats, scoring et chemins témoins. |
| `evidence/validation.py` | Vérification des appuis thématiques. |
| `evidence/context.py` | Augmentation et contrôle des références finales. |
| `orchestration.py` | Enchaînement réel de toutes les étapes. |

Ces chemins sont relatifs à `src/moviegraphrag/`. L’inventaire de tous les fichiers
se trouve dans la section 13 du dossier complet.

## 9. Démonstration courte à préparer

1. « Quels films de Christopher Nolan avec Cillian Murphy ? » : quatre films ; montrer les relations.
2. « Quels films de Nolan avec DiCaprio parlent des rêves ? » : Inception ; montrer les appuis thématiques.
3. Une question sur Tom Holland sans contexte : montrer la clarification.
4. Expliquer qu’aucun résultat ne signifie pas absence dans le monde : le corpus est limité à TMDB 5000.

Faire ces essais dans l’application existante. La disponibilité Groq dépend du réseau
et du quota. Ne pas annoncer des latences ou résultats de benchmark non mesurés.

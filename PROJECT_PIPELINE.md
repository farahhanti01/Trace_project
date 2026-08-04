# TRACE AI Platform - Fonctionnement et pipeline

## Objectif du projet

TRACE AI Platform aide a analyser des documents techniques et des traces
d'autorisation. Le projet contient deux agents principaux :

- Documentation Agent : retrouve et explique l'information dans les documents.
- Log Analysis Agent : analyse les traces, reconstruit la Log Story et explique
  les anomalies avec l'aide des documents.

Le principe central est :

```text
Parsing deterministe en Python
-> stockage en MongoDB
-> retrieval RAG
-> explication par le modele
-> reponse structuree avec sources
```

Le LLM ne doit pas parser directement les logs bruts. Il sert surtout a
expliquer, synthetiser et reformuler a partir des donnees deja extraites.

## Pipeline general

1. L'utilisateur cree ou ouvre une conversation.
2. Il choisit un agent : Documentation Agent ou Log Analysis Agent.
3. Il upload un ou plusieurs fichiers.
4. Le backend extrait le contenu du fichier.
5. Les sections extraites sont stockees dans MongoDB.
6. Le chatbot recoit une question.
7. Le backend recupere les sections pertinentes.
8. Le modele genere une reponse structuree.
9. Le frontend affiche Summary, Details, Transactions, Log Story, HSM Analysis,
   Sources ou Notes selon le type de reponse.

## Upload et extraction des documents

Les fichiers uploades sont geres par :

```text
backend/app/services/document_service.py
backend/app/services/extraction_service.py
backend/app/routes/documents.py
```

Types supportes :

- PDF
- DOCX
- XLSX
- TXT / LOG

L'extraction produit des sections/chunks avec des metadonnees :

- source
- page
- sheet
- paragraph
- heading
- section_index
- chunk_index
- embedding si disponible

Ces donnees sont stockees dans :

```text
documents_collection
document_sections_collection
```

## Documentation Agent

Fichier principal :

```text
backend/app/services/documentation_agent_service.py
```

Role :

- charger les sections des documents de la conversation ;
- selectionner les passages pertinents ;
- construire le contexte RAG ;
- appeler le modele ;
- normaliser la reponse ;
- retourner les sources et evidence.

Pipeline :

```text
question utilisateur
-> load_sections()
-> retrieve_relevant_sections()
-> focus_sections_on_exact_fields()
-> compact_sections_by_query_coverage()
-> build_context()
-> call_hps_ai()
-> parse_ai_json()
-> repair_atomic_response()
-> normalize_agent_response()
-> reponse frontend
```

Le Documentation Agent est adapte aux questions comme :

```text
Explique Field 126.10.
Liste les pages qui parlent de Response Code.
Resume ce document et cite les pages exactes.
```

Point a renforcer :

- ajouter un mode Document Overview pour les resumes globaux ;
- recuperer plusieurs zones du document, pas seulement les chunks les plus
  proches lexicalement ;
- mieux exploiter table des matieres, headings et sections principales.

## Retrieval RAG

Fichier principal :

```text
backend/app/services/retrieval_service.py
```

Responsabilites :

- normaliser la question ;
- extraire les termes importants ;
- scorer les sections ;
- utiliser les embeddings si disponibles ;
- appliquer un reranking lexical ;
- extraire un court passage pertinent.

Le retrieval retourne les sections les plus utiles au modele.

## Log Analysis Agent

Fichiers principaux :

```text
backend/app/services/log_parser_service.py
backend/app/services/log_analysis_agent_service.py
```

Le parsing de trace est deterministe.

Le parser detecte :

- transactions par `Start DumpVisa()` ou `Start DumpCis()` ;
- MTI ;
- FLD 002 masque ;
- FLD 003 ;
- FLD 037 ;
- FLD 039 ;
- fonctions de Log Story ;
- status OK / ERROR / WARNING / UNKNOWN ;
- interactions HSM si presentes.

Pipeline :

```text
trace texte
-> split_transactions()
-> parse_transaction_fields()
-> parse_log_story()
-> parse_hsm_analysis()
-> transaction_status()
-> enrich_transactions_with_documentation()
-> select_transactions_for_question()
-> reponse structuree
```

## Log Story

La Log Story est reconstruite depuis les lignes :

```text
Start FunctionName()
End FunctionName(...)
```

Quand un fichier XLSX de documentation est disponible, les fonctions doivent
etre filtrees selon les fonctions documentees dans Excel.

Le XLSX sert a expliquer :

- fonction ;
- description ;
- exception ;
- path ;
- feuille ;
- ligne source.

## HSM Analysis

Le parser cherche notamment :

```text
TO HSM
FROM HSM
HsmResultCode
command_XX()
WriteBalHsm
ReadBalHsm
HsmQuery
```

La sortie HSM affiche principalement :

- Message envoye (TO HSM)
- Thread
- Commande
- Reponse HSM
- Code retour
- HsmResultCode
- Description documentaire si trouvee

Si l'utilisateur demande une analyse HSM mais que la trace active ne contient
aucun bloc HSM detecte, le backend doit retourner un message clair :

```text
Aucune interaction HSM n'a ete detectee dans la trace active.
```

## Memoire de conversation

Le backend garde une memoire de contexte dans la meme discussion.

Regle :

- si un nouveau fichier est attache au message, il devient le contexte actif ;
- si aucun fichier n'est attache, le backend reutilise les derniers fichiers
  pertinents de la conversation ;
- si une nouvelle trace est attachee, elle remplace l'ancienne trace active pour
  l'analyse.

Cette logique evite de melanger les resultats d'anciennes traces avec une
nouvelle question.

## Frontend

Fichiers importants :

```text
frontend/src/App.jsx
frontend/src/components/AIResponseCard.jsx
frontend/src/components/ChatMessage.jsx
frontend/src/components/AdminDocumentsPage.jsx
frontend/src/services/api.js
frontend/src/styles.css
```

Responsabilites :

- gerer la conversation ;
- uploader les fichiers ;
- envoyer la question au backend ;
- afficher les reponses structurees ;
- permettre la copie/edition du prompt ;
- afficher les documents et Log Stories dans l'interface admin.

## Lancement local

MongoDB :

```bash
docker start trace-mongodb
```

Backend :

```bash
cd backend
source .venv/Scripts/activate
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Frontend :

```bash
cd frontend
npm run dev
```

## Points restants importants

1. Renforcer Documentation Agent avec un vrai mode Document Overview.
2. Garantir que la Log Story n'affiche que les fonctions documentees dans le
   XLSX quand le XLSX est disponible.
3. Ajouter plus de tests de regression.
4. Verifier que chaque nouvelle trace devient bien le contexte actif.
5. Garder les reponses extractives et sourcees pour limiter les hallucinations.

## Paper Link

- RAPTOR: https://arxiv.org/abs/2401.18059
- GraphRAG: https://arxiv.org/abs/2404.16130
- HippoRAG: https://arxiv.org/abs/2405.14831

---

# GraphRAG Intelligence

A document intelligence system combining **GraphRAG**, **RAPTOR**, and **HiPPO** retrieval over any uploaded documents. Powered by **Gemini 2.5 Flash** (free tier).

## Architecture

```
Uploaded Docs → Document Processor → (chunks)
                                      ├─ GraphRAG Indexer → Knowledge Graph (NetworkX + Louvain)
                                      ├─ RAPTOR Runner    → Summary Tree (GMM clustering)
                                      └─ HiPPO Retriever  → Passage Pyramid (mean pooling)
                                                                   ↓
                                           Query Engine  →  Gemini 2.5 Flash → Answer + Entities + Sources
```

## Stack

| Layer     | Technology                       |
|-----------|----------------------------------|
| Backend   | FastAPI + uvicorn                |
| LLM       | Gemini 2.5 Flash (google-generativeai) |
| Graph     | NetworkX + Louvain community detection |
| Embeddings| Google text-embedding-004        |
| Frontend  | Angular 17 (standalone) + D3.js  |

## Setup

### 1. Get a Gemini API key

Free at [https://aistudio.google.com](https://aistudio.google.com)

### 2. Backend

```bash
cd graphrag-project
cp .env.example .env
# edit .env and set GEMINI_API_KEY=your-key
download ollama from the website: https://ollama.com/download
- open a terminal then run: 
ollama pull nomic-embed-text

pip install -r requirements.txt

uvicorn backend.app.main:app --reload --port 8000
```

API docs available at http://localhost:8000/docs

### 3. Frontend

```bash
cd graphrag-project/frontend
npm install
ng serve       # serves at http://localhost:4200
```

Requests to `/api/*` are proxied to the FastAPI backend automatically.

## Usage

1. Open http://localhost:4200
2. Click **Upload Documents** and drop any PDF / DOCX / TXT / HTML files
3. Wait for indexing to complete (status turns green in the sidebar)
4. Switch to **Knowledge Graph** to explore entities and relationships
5. Switch to **Ask Questions** and type a question — choose the retrieval method:
   - **Hybrid** — uses all three (recommended)
   - **GraphRAG** — knowledge graph community context
   - **RAPTOR** — tree-based summarisation
   - **HiPPO** — hierarchical passage pooling

## Evaluation

```bash
# eval_data.json: [{"question": "...", "reference": "..."}]
python -m evaluation.eval \
  --qa_pairs eval_data.json \
  --docs_dir ./sample_docs \
  --method hybrid \
  --output eval_report.json
```

Produces ROUGE, BLEU, entity recall, faithfulness, and latency metrics.

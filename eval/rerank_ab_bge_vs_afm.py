#!/usr/bin/env python3
"""Reranker-A/B: bge-reranker-v2-m3 (Infinity :8002) vs AFM-Pointwise
(fm-agent :8007, Guided-Generation-Score) vs OHNE Rerank (Vektor-Ordnung).

Entscheidungsfrage: kann AFM den Infinity-Reranker ersetzen? Mini-Harness im
minimal-harness-Stil: Gold = Quell-Drawer der generierten Query.

Pipeline (exakt der Produktions-Suchpfad):
  1. N Drawer aus Qdrant sampeln (echte Palace-Daten, Text 200-800 Zeichen).
  2. Query LOKAL generieren (gemma-12B via Router — kein Cloud-Call, PII!).
  3. Query embedden wie die Palace-EF: Präfix "task: sentence similarity |
     query: " → 768d via Router → Matryoshka-Truncation auf 384d.
  4. Qdrant-Suche top_k_in=40 (Produktionswert) über die GANZE Collection.
  5. Rerank-Varianten auf den 40 Kandidaten (max 1500 Zeichen/Passage —
     Produktionswert), Metriken auf Top-10.

Metriken: Hit@1, Hit@5, MRR@10 (nur Queries, deren Gold in den Top-40 ist —
der Reranker kann nur ordnen, was der Vektor-Schritt liefert; die
Vektor-Recall-Quote wird separat ausgewiesen). Latenz je Variante.

Noise-Regel (feedback_eval_single_run_noise): Delta < 0.05 = Rauschen.

Run: python3 -u eval/rerank_ab_bge_vs_afm.py [--n 50] [--seed 7]
"""
import argparse
import concurrent.futures
import json
import random
import re
import statistics
import time

import httpx

QDRANT = "http://localhost:6333"
COLL = "mempalace_db0eee7a22b04148_mempalace_drawers"
ROUTER = "http://127.0.0.1:8424"
BGE_URL = "http://192.168.1.214:8002/rerank"
BGE_MODEL = "BAAI/bge-reranker-v2-m3"
AFM_URL = "http://192.168.1.214:8007/v1/chat/completions"
PREFIX = "task: sentence similarity | query: "   # mempalace/embedding.py
DIM = 384                                        # Matryoshka-Truncation
TOP_K_IN = 40                                    # config mempalace.reranker.top_k_in
MAX_PASSAGE = 1500                               # config max_chars_per_passage
QUERY_MODEL = "gemma-4-12B-it-qat-oQ4-fp16"      # lokal (Router)


def router_key():
    return json.load(open("/Users/alexander/Documents/dev/cctest/config.json"))[
        "providers"]["llm-router"]["api_key"]


KEY = router_key()


# ── 1. Drawer sampeln ──────────────────────────────────────────────────────
def sample_drawers(n, seed):
    random.seed(seed)
    points, offset = [], None
    while len(points) < n * 30 and (offset is not None or not points):
        body = {"limit": 512, "with_payload": True, "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        r = httpx.post(f"{QDRANT}/collections/{COLL}/points/scroll",
                       json=body, timeout=30).json()["result"]
        points += r["points"]
        offset = r.get("next_page_offset")
        if offset is None:
            break
    cands = []
    for p in points:
        doc = str(p["payload"].get("document") or "")
        if 200 <= len(doc) <= 800 and len(doc.split()) >= 25:
            cands.append({"id": p["id"], "doc": doc,
                          "wing": p["payload"].get("metadata", {}).get("wing", "")})
    random.shuffle(cands)
    # Wing-Mix: nicht alles aus einem Wing
    seen, out = {}, []
    for c in cands:
        if seen.get(c["wing"], 0) >= max(3, n // 5):
            continue
        seen[c["wing"]] = seen.get(c["wing"], 0) + 1
        out.append(c)
        if len(out) >= n:
            break
    return out


# ── 2. Query-Generierung (LOKAL) ───────────────────────────────────────────
def gen_query(doc):
    body = {"model": QUERY_MODEL, "max_tokens": 60, "temperature": 0.3,
            "messages": [{"role": "user", "content":
                "Formuliere EINE kurze Suchanfrage (max. 12 Wörter, keine "
                "Anführungszeichen), mit der jemand GENAU den folgenden Text "
                "in einer Wissensdatenbank finden würde. Antworte NUR mit der "
                "Suchanfrage.\n\nTEXT:\n" + doc[:700]}]}
    r = httpx.post(f"{ROUTER}/v1/chat/completions", json=body, timeout=120,
                   headers={"Authorization": f"Bearer {KEY}"})
    r.raise_for_status()
    q = r.json()["choices"][0]["message"]["content"].strip().splitlines()[0]
    return re.sub(r'^["\'`]+|["\'`]+$', "", q)[:200]


# ── 3./4. Embedding + Suche ────────────────────────────────────────────────
def embed_query(q):
    r = httpx.post(f"{ROUTER}/v1/embeddings",
                   json={"model": "embeddinggemma-300m-bf16",
                         "input": [PREFIX + q]},
                   timeout=60, headers={"Authorization": f"Bearer {KEY}"})
    r.raise_for_status()
    return r.json()["data"][0]["embedding"][:DIM]


def vector_search(vec, k=TOP_K_IN):
    r = httpx.post(f"{QDRANT}/collections/{COLL}/points/search",
                   json={"vector": vec, "limit": k, "with_payload": True},
                   timeout=30)
    r.raise_for_status()
    return [{"id": p["id"], "doc": str(p["payload"].get("document") or "")}
            for p in r.json()["result"]]


# ── 5. Reranker ────────────────────────────────────────────────────────────
def rerank_bge(query, cands):
    r = httpx.post(BGE_URL, json={
        "model": BGE_MODEL, "query": query,
        "documents": [c["doc"][:MAX_PASSAGE] for c in cands]}, timeout=120)
    r.raise_for_status()
    order = sorted(r.json()["results"], key=lambda x: -x["relevance_score"])
    return [cands[x["index"]]["id"] for x in order]


_AFM_SCHEMA = {"type": "object", "properties": {"score": {"type": "integer"}},
               "required": ["score"]}


def _afm_score(query, doc):
    body = {"model": "apple-fm-agentic", "max_tokens": 30,
            "messages": [{"role": "user", "content":
                f"Bewerte die Relevanz der Passage für die Suchanfrage von 0 "
                f"(irrelevant) bis 10 (perfekte Antwort).\nAnfrage: {query}\n"
                f"Passage: {doc[:MAX_PASSAGE]}"}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "Rel", "schema": _AFM_SCHEMA}}}
    try:
        r = httpx.post(AFM_URL, json=body, timeout=90)
        r.raise_for_status()
        return json.loads(r.json()["choices"][0]["message"]["content"])["score"]
    except Exception:
        return -1


def rerank_afm(query, cands):
    with concurrent.futures.ThreadPoolExecutor(8) as ex:
        scores = list(ex.map(lambda c: _afm_score(query, c["doc"]), cands))
    order = sorted(range(len(cands)), key=lambda i: (-scores[i], i))  # stabil
    return [cands[i]["id"] for i in order]


# ── Metriken ───────────────────────────────────────────────────────────────
def metrics(rank_lists, golds):
    hit1 = hit5 = 0
    mrr = 0.0
    for ranked, gold in zip(rank_lists, golds):
        top10 = ranked[:10]
        if gold in top10:
            pos = top10.index(gold) + 1
            mrr += 1.0 / pos
            hit1 += pos == 1
            hit5 += pos <= 5
    n = len(golds)
    return {"hit@1": hit1 / n, "hit@5": hit5 / n, "mrr@10": mrr / n, "n": n}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    print(f"Sample {args.n} Drawer …", flush=True)
    drawers = sample_drawers(args.n, args.seed)
    print(f"  {len(drawers)} Drawer aus {len(set(d['wing'] for d in drawers))} Wings", flush=True)

    cases = []
    t0 = time.time()
    for i, d in enumerate(drawers):
        try:
            q = gen_query(d["doc"])
        except Exception as e:
            print(f"  query-gen {i}: FEHLER {e}", flush=True)
            continue
        cases.append({"gold": d["id"], "query": q})
        if (i + 1) % 10 == 0:
            print(f"  {i+1} Queries ({time.time()-t0:.0f}s)", flush=True)

    # Vektor-Schritt einmal pro Query (für alle Varianten identisch)
    usable, vec_recall = [], 0
    t_emb = t_search = 0.0
    for c in cases:
        t = time.time(); vec = embed_query(c["query"]); t_emb += time.time() - t
        t = time.time(); cands = vector_search(vec); t_search += time.time() - t
        ids = [x["id"] for x in cands]
        if c["gold"] in ids:
            vec_recall += 1
            usable.append({**c, "cands": cands})
    print(f"\nVektor-Recall@{TOP_K_IN}: {vec_recall}/{len(cases)} "
          f"(nur diese sind rerankbar; Embed Ø{t_emb/max(1,len(cases)):.2f}s, "
          f"Suche Ø{t_search/max(1,len(cases)):.3f}s)", flush=True)

    variants = {
        "ohne": lambda q, cd: [x["id"] for x in cd],
        "bge": rerank_bge,
        "afm": rerank_afm,
    }
    results, latencies = {}, {}
    for name, fn in variants.items():
        ranked, times = [], []
        for c in usable:
            t = time.time()
            try:
                ranked.append(fn(c["query"], c["cands"]))
            except Exception as e:
                print(f"  {name}: FEHLER {e}", flush=True)
                ranked.append([x["id"] for x in c["cands"]])
            times.append(time.time() - t)
        results[name] = metrics(ranked, [c["gold"] for c in usable])
        latencies[name] = times
        print(f"  {name}: {json.dumps(results[name])} "
              f"Ø{statistics.mean(times):.2f}s median {statistics.median(times):.2f}s", flush=True)

    print("\n=== ZUSAMMENFASSUNG (Rerank der Top-%d, Metriken auf Top-10) ===" % TOP_K_IN, flush=True)
    print(f"{'Variante':8} {'Hit@1':>7} {'Hit@5':>7} {'MRR@10':>7} {'Ø s/Query':>10}")
    for name in variants:
        r = results[name]
        print(f"{name:8} {r['hit@1']:7.3f} {r['hit@5']:7.3f} {r['mrr@10']:7.3f} "
              f"{statistics.mean(latencies[name]):10.2f}")
    out = {"results": results,
           "latency_mean": {k: statistics.mean(v) for k, v in latencies.items()},
           "vec_recall": vec_recall / max(1, len(cases)), "n_cases": len(cases),
           "queries": [{"q": c["query"], "gold": str(c["gold"])} for c in usable]}
    with open("eval/_rerank_ab_results.json", "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("→ eval/_rerank_ab_results.json", flush=True)


if __name__ == "__main__":
    main()

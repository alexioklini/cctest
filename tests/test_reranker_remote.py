"""Remote-Reranker-Seam (reranker.device: "remote" — Infinity ``POST /rerank``
auf dem Mac mini fuer das Windows-Deployment; engine/mempalace_glue.py).

Deckt ohne torch/sentence_transformers ab:
  - Score-Mapping ueber results[].index (Response-Reihenfolge egal)
  - Wrapper-Caching; None ohne url / ohne model / bei gelatchtem Zustand
  - Latch: 2 aufeinanderfolgende Fehler -> prozessweit aus; Erfolg reset't
  - toter Endpoint zaehlt als Fehler (kein Haenger dank connect-timeout)

Runs in the bare test interpreter — no server, no spaCy, kein torch.
"""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from engine import mempalace_glue as mg


class _StubRerank(BaseHTTPRequestHandler):
    fail_next = 0  # class-level: so viele Requests mit 500 beantworten

    def do_POST(self):
        if self.path != "/rerank":
            self.send_error(404)
            return
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if _StubRerank.fail_next > 0:
            _StubRerank.fail_next -= 1
            self.send_error(500, "boom")
            return
        docs = body.get("documents") or []
        # Scores steigen mit dem Dokument-Index, aber die results-Liste kommt
        # in UMGEKEHRTER Reihenfolge -> beweist das index-basierte Mapping.
        results = [{"index": i, "relevance_score": (i + 1) / (len(docs) + 1)}
                   for i in reversed(range(len(docs)))]
        payload = json.dumps({"object": "rerank", "results": results}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # Testlauf ruhig halten
        pass


class RemoteRerankerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), _StubRerank)
        cls.port = cls.srv.server_address[1]
        cls.url = f"http://127.0.0.1:{cls.port}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        _StubRerank.fail_next = 0
        mg._remote_rerank_fails = 0
        mg._remote_rerank_latched_off = False
        mg._reranker_cache.clear()

    def test_predict_maps_scores_by_index(self):
        m = mg._get_reranker_model("test-model", "remote", {"url": self.url})
        self.assertIsNotNone(m)
        pairs = [("frage", f"doc{i}") for i in range(4)]
        scores = m.predict(pairs, batch_size=16, show_progress_bar=False)
        self.assertEqual(len(scores), 4)
        # Mapping-Beweis: Scores folgen dem Dokument-Index, obwohl der Stub
        # die results-Liste rueckwaerts liefert.
        self.assertEqual(scores, sorted(scores))
        order = sorted(range(4), key=lambda i: scores[i], reverse=True)
        self.assertEqual(order, [3, 2, 1, 0])

    def test_wrapper_cached_and_requires_url(self):
        a = mg._get_reranker_model("m", "remote", {"url": self.url})
        b = mg._get_reranker_model("m", "remote", {"url": self.url})
        self.assertIs(a, b)
        self.assertIsNone(mg._get_reranker_model("m", "remote", {}))
        self.assertIsNone(mg._get_reranker_model("m", "remote", None))
        self.assertIsNone(mg._get_reranker_model("", "remote", {"url": self.url}))

    def test_latch_after_two_consecutive_failures(self):
        m = mg._get_reranker_model("m", "remote", {"url": self.url})
        _StubRerank.fail_next = 2
        for _ in range(2):
            with self.assertRaises(Exception):
                m.predict([("q", "d")])
        self.assertTrue(mg._remote_rerank_latched_off)
        # gelatcht -> Loader liefert None, die Call-Site skippt sauber
        self.assertIsNone(mg._get_reranker_model("m", "remote", {"url": self.url}))

    def test_success_resets_fail_counter(self):
        m = mg._get_reranker_model("m", "remote", {"url": self.url})
        _StubRerank.fail_next = 1
        with self.assertRaises(Exception):
            m.predict([("q", "d")])
        m.predict([("q", "d")])  # Erfolg -> Zaehler zurueck auf 0
        _StubRerank.fail_next = 1
        with self.assertRaises(Exception):
            m.predict([("q", "d")])
        self.assertFalse(mg._remote_rerank_latched_off)

    def test_unreachable_endpoint_counts_as_failure(self):
        m = mg._get_reranker_model("m", "remote",
                                   {"url": "http://127.0.0.1:1", "remote_timeout_s": 1})
        with self.assertRaises(Exception):
            m.predict([("q", "d")])
        self.assertEqual(mg._remote_rerank_fails, 1)


if __name__ == "__main__":
    unittest.main()

"""Offline unit tests (no network, no model calls).

    python3 -m unittest discover -s tests -v
"""
import copy
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from arxiv_meta import parse_arxiv_id, parse_history  # noqa: E402
from common import normalize_text, sha256_file  # noqa: E402
from evidence import Snapshot, safe_eval, validate  # noqa: E402
import novelty  # noqa: E402
import render  # noqa: E402
import sources  # noqa: E402

DEMO = ROOT / "examples" / "demo"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(DEMO, self.tmp / "demo", ignore=shutil.ignore_patterns(".backups"))
        self.dir = self.tmp / "demo"
        self.review = json.loads((self.dir / "review.json").read_text(encoding="utf-8"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self, data=None, strict=False):
        return validate(copy.deepcopy(data or self.review), self.dir, strict)

    def c(self, cid, data=None):
        return next(x for x in (data or self.review)["comments"] if x["id"] == cid)


class TestNormalizeAndSnapshot(Base):
    def test_normalize(self):
        self.assertEqual(normalize_text("eﬃcient  feed-\nforward\n\nmodel"), "efficient feedforward model")
        self.assertEqual(normalize_text("“quote” – dash"), '"quote" - dash')

    def test_snapshot_page_lookup(self):
        s = Snapshot("aaa bbb\n\nccc ddd", [{"page": 1, "start": 0, "end": 7}, {"page": 2, "start": 9, "end": 16}])
        self.assertEqual(s.find("ccc ddd"), ("exact", [2]))
        self.assertEqual(s.find("bbbccc")[0], "absent")  # loose needs >= 12 chars
        s2 = Snapshot("the feed-forward reconstruction model", [{"page": 3, "start": 0, "end": 37}])
        self.assertEqual(s2.find("feedforward reconstruction"), ("loose", [3]))

    def test_extract_reproducible(self):
        out = self.tmp / "x"
        r = subprocess.run([sys.executable, ROOT / "scripts/extract_pdf.py", DEMO / "paper.pdf", "--id", "paper", "--role",
                            "manuscript", "--out", out], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        man = json.loads((out / "paper.manifest.json").read_text())
        self.assertEqual(man["page_count"], 4)
        self.assertEqual(man["pdf_sha256"], sha256_file(DEMO / "paper.pdf"))
        self.assertTrue(man["injection_hits"], "hidden prompt injection should be detected")
        self.assertIn("prompt-injection", r.stderr)
        self.assertEqual((out / "paper.txt").read_bytes(), (DEMO / "sources/paper.txt").read_bytes())


class TestEvidence(Base):
    def test_demo_valid_strict(self):
        r = self.check(strict=True)
        self.assertTrue(r["valid"], r["errors"])

    def test_fabricated_quote(self):
        self.c("W02")["evidence"][0]["quote"] = "AdaRoute reduces latency by 70%"
        self.assertFalse(self.check()["valid"])

    def test_wrong_page(self):
        self.c("W02")["evidence"][0]["page"] = 3
        r = self.check()
        self.assertTrue(any("not p.3" in e for e in r["errors"]), r["errors"])

    def test_tampered_snapshot(self):
        (self.dir / "sources/paper.txt").write_text("tampered", encoding="utf-8")
        self.assertFalse(self.check()["valid"])

    def test_snapshot_edited_and_rehashed(self):
        p = self.dir / "sources/paper.txt"
        p.write_text(p.read_text() + " We also prove a theorem.", encoding="utf-8")
        import hashlib
        self.review["sources"][0]["sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
        r = self.check()
        self.assertTrue(any("manifest text hash" in e for e in r["errors"]), r["errors"])

    def test_handwritten_snapshot_rejected_for_manuscript(self):
        del self.review["sources"][0]["manifest"]
        self.assertFalse(self.check()["valid"])

    def test_wrong_pdf(self):
        self.review["paper"]["pdf_sha256"] = "0" * 64
        self.assertFalse(self.check()["valid"])

    def test_confirmed_requires_countercheck(self):
        self.c("W03")["countercheck"] = ""
        self.assertFalse(self.check()["valid"])

    def test_confirmed_requires_manuscript_quote(self):
        self.c("W03")["evidence"] = []
        self.assertFalse(self.check()["valid"])

    def test_downgrade_to_question_allowed(self):
        w = self.c("W03")
        w.update(status="question", evidence=[], countercheck="")
        self.assertTrue(self.check()["valid"])

    def test_novelty_needs_comparator_quote(self):
        w = self.c("W01")
        w["evidence"] = [e for e in w["evidence"] if e["source_id"] != "threshskip"]
        self.assertFalse(self.check()["valid"])

    def test_comparator_after_cutoff(self):
        for s in self.review["sources"]:
            if s["id"] == "threshskip":
                s["public_date"] = "2026-03-01"
                s["date_verified"]["value"] = "2026-03-01"
        r = self.check()
        self.assertTrue(any("after the cutoff" in e for e in r["errors"]), r["errors"])

    def test_comparator_unknown_date(self):
        for s in self.review["sources"]:
            if s["id"] == "threshskip":
                s["public_date"] = None
                del s["date_verified"]
        self.assertFalse(self.check()["valid"])

    def test_unverified_comparator_strict(self):
        for s in self.review["sources"]:
            if s["id"] == "threshskip":
                del s["date_verified"]
        self.assertTrue(self.check()["valid"])
        self.assertFalse(self.check(strict=True)["valid"])

    def test_dimension_coverage(self):
        del self.review["dimensions"]["theory"]
        self.assertFalse(self.check()["valid"])
        self.review["dimensions"]["theory"] = {"status": "ok", "note": ""}
        self.assertFalse(self.check()["valid"])

    def test_quick_mode_unchecked_must_be_declared(self):
        self.review["mode"] = "quick"
        self.review["dimensions"]["theory"] = {"status": "unchecked"}
        self.assertFalse(self.check()["valid"])
        self.review["policy"]["not_done"].append("理论维度未检查")
        self.assertTrue(self.check()["valid"], self.check()["errors"])

    def test_score_on_venue_scale(self):
        self.review["overall"]["score"] = 7
        self.assertFalse(self.check()["valid"])
        self.review["overall"]["score"] = 4
        self.review["overall"]["subscores"]["soundness"] = 5
        self.assertFalse(self.check()["valid"])

    def test_numeric_check(self):
        self.assertEqual(self.review["numeric_checks"][0]["claimed"], 52)  # paper's number may differ: no error
        self.assertTrue(self.check()["valid"])
        self.review["numeric_checks"][0]["value"] = 52  # but the recomputed value must match the expression
        r = self.check()
        self.assertTrue(any("numeric N1" in e for e in r["errors"]), r["errors"])

    def test_safe_eval_rejects_code(self):
        self.assertAlmostEqual(safe_eval("(2.10-1.30)/2.10*100"), 38.095, places=2)
        with self.assertRaises(ValueError):
            safe_eval("__import__('os').system('true')")

    def test_region_bounds(self):
        self.c("W01")["rect"] = [0.9, 0.1, 0.4, 0.1]
        self.assertFalse(self.check()["valid"])

    def test_path_escape(self):
        self.review["sources"][1]["path"] = "../../etc/passwd"
        self.assertFalse(self.check()["valid"])

    def test_semantics_not_certified(self):
        # a real quote with a wrong conclusion still passes: the validator does not judge entailment
        self.c("W03")["body"] = "作者使用的数据比基线少。"
        self.assertTrue(self.check()["valid"])

    def test_malformed(self):
        self.assertFalse(validate([], self.dir)["valid"])
        d = copy.deepcopy(self.review)
        d["comments"] = None
        self.assertFalse(validate(d, self.dir)["valid"])


class TestTools(Base):
    def test_init_review(self):
        work = self.tmp / "w"
        r = subprocess.run([sys.executable, ROOT / "scripts/init_review.py", DEMO / "paper.pdf", "--work", work, "--venue", "ICML",
                            "--year", "2026", "--cutoff", "2025-12-31"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        rv = json.loads((work / "review.json").read_text())
        self.assertEqual(rv["paper"]["pages"], 4)
        self.assertEqual(len(rv["dimensions"]), 9)
        self.assertTrue(rv["policy"]["prompt_injection"])
        again = subprocess.run([sys.executable, ROOT / "scripts/init_review.py", DEMO / "paper.pdf", "--work", work, "--cutoff", "2025-12-31"],
                               capture_output=True, text=True)
        self.assertNotEqual(again.returncode, 0, "must refuse to overwrite")

    def test_quote_rect(self):
        import find_quote
        r = find_quote.quote_rect(DEMO / "paper.pdf", 2, "The base threshold tau_0 is selected by grid search on the UCF-101 test split")
        self.assertIsNotNone(r)
        x, y, w, h = r
        self.assertTrue(0.2 < y < 0.32 and h < 0.05 and x + w <= 1, r)
        self.assertIsNone(find_quote.quote_rect(DEMO / "paper.pdf", 1, "The base threshold tau_0 is selected"))

    def test_html_snapshot(self):
        page = self.tmp / "prior.html"
        page.write_text("<html><head><script>var x=1;</script></head><body><h1>Prior</h1><p>We skip tokens by a fixed threshold.</p></body></html>")
        r = subprocess.run([sys.executable, ROOT / "scripts/extract_pdf.py", page, "--id", "web", "--out", self.tmp / "s",
                            "--public-date", "2025-03-21", "--date-note", "meta tag"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        man = json.loads((self.tmp / "s/web.manifest.json").read_text())
        self.assertEqual(man["page_count"], 1)
        self.assertEqual(man["source_kind"], "text")
        txt = (self.tmp / "s/web.txt").read_text()
        self.assertIn("We skip tokens by a fixed threshold.", txt)
        self.assertNotIn("var x", txt)

    def test_manual_date_entry(self):
        r = subprocess.run([sys.executable, ROOT / "scripts/extract_pdf.py", DEMO / "prior/threshskip.pdf", "--id", "ts", "--out", self.tmp / "s",
                            "--public-date", "2024-05-10", "--date-note", "printed on page 1"], capture_output=True, text=True)
        e = json.loads(r.stdout)
        self.assertEqual(e["date_verified"]["method"], "manual")
        self.assertEqual(e["public_date"], "2024-05-10")


class TestRender(Base):
    def test_render_all(self):
        render.main.__globals__["sys"].argv = ["render.py", str(self.dir / "review.json")]
        render.main()
        md = (self.dir / "review.md").read_text()
        self.assertIn("## 主要问题", md)
        self.assertIn("W01", md)
        html = (self.dir / "review.html").read_text()
        self.assertIn("Times New Roman", html)
        self.assertIn("<table>", html)
        form = (self.dir / "form.md").read_text()
        self.assertIn("2 = 拒绝 Reject", form)
        self.assertIn("4 = 有把握", form)

    def test_form_ethics_and_questions(self):
        form = render.to_form(self.review, "ICML")
        weak = form.split("**不足**")[1].split("## Key Questions")[0]
        self.assertNotIn("W06", weak.replace("（对应", ""))
        self.assertNotIn("隐藏指令", weak)
        eth = form.split("## Ethical Concerns")[1].split("## Final")[0]
        self.assertIn("隐藏指令", eth)
        qs = form.split("## Key Questions")[1].split("## Limitations")[0]
        self.assertIn("3. ", qs)

    def test_withdrawn_rendered(self):
        md = render.to_markdown(self.review)
        self.assertIn("已撤销或降级的候选批评", md)
        self.assertIn("提示注入", md)

    def test_generic_journal_form(self):
        form = render.to_form(self.review, "TPAMI")
        self.assertIn("请核对当年审稿指南", form)
        self.assertIn("主要问题", form)

    def test_venues_complete(self):
        vj = json.loads((ROOT / "assets/venues.json").read_text())
        a = [k for k, v in vj["venues"].items() if v["ccf"] == "A"]
        self.assertEqual(len(a), 95)
        for k in ("ICLR", "NeurIPS", "CVPR", "ICCV", "ICML", "AAAI", "ACL", "TPAMI", "IJCV", "TIP", "ACM MM", "SIGGRAPH"):
            self.assertEqual(vj["venues"][k]["ccf"], "A", k)
        self.assertEqual(vj["venues"]["IJCAI"]["ccf"], "B")
        self.assertEqual(vj["venues"]["ECCV"]["ccf"], "B")


class TestSourcesOffline(Base):
    def test_merge_dedup(self):
        a = {"query": "q1", "records": [sources._rec("arxiv", "Fast3R: Towards 3D", date="2025-01-23", arxiv_id="2501.13928")]}
        b = {"query": "q2", "records": [sources._rec("dblp", "Fast3R: Towards 3D.", year=2025, doi="10.1/x"),
                                        sources._rec("openalex", "Other", date="2026-01-01")]}
        fa, fb = self.tmp / "a.json", self.tmp / "b.json"
        fa.write_text(json.dumps(a))
        fb.write_text(json.dumps(b))
        m = sources.merge([fa, fb], before="2025-03-14")
        self.assertEqual(len(m), 2)
        top = m[0]
        self.assertEqual(sorted(top["providers"]), ["arxiv", "dblp"])
        self.assertEqual(top["doi"], "10.1/x")
        self.assertTrue(top["before_cutoff"])
        self.assertFalse(m[1]["before_cutoff"])

    def test_arxiv_query_builder(self):
        q = sources._arxiv_query('multi-view "feed forward"', "2025-03-14")
        self.assertIn('all:"feed forward"', q)
        self.assertIn("submittedDate:[199101010000 TO 202503142359]", q)

    def test_openreview_default_hides_discussion(self):
        self.assertTrue(callable(sources.PROVIDERS["openreview"]))

    def test_history_parse(self):
        html = '<strong><a href="/abs/2501.13928v1">[v1]</a></strong>\n Thu, 23 Jan 2025 18:59:55 UTC (43,127 KB)<br/><strong>[v2]</strong>\n Wed, 19 Mar 2025 19:35:52 UTC'
        self.assertEqual(parse_history(html), {"v1": "2025-01-23", "v2": "2025-03-19"})
        self.assertEqual(parse_arxiv_id("https://arxiv.org/abs/2503.11651v1"), ("2503.11651", "v1"))


class TestNovelty(Base):
    def make(self):
        spec = {"paper_title": "AdaRoute", "cutoff": "2025-12-31", "confidential": True,
                "contributions": [{"id": "C1", "claim": "阈值路由", "mechanism": "confidence threshold token skipping", "keywords": ["token skipping"]},
                                  {"id": "C2", "claim": "预算阈值", "mechanism": "budget-conditioned threshold"}]}
        p = self.dir / "contributions.json"
        p.write_text(json.dumps(spec, ensure_ascii=False))
        briefs = novelty.plan(p, self.dir / "novelty", 10, ROOT, self.dir)
        return briefs

    def test_plan_skips_search_false(self):
        spec = {"paper_title": "x", "cutoff": "2025-12-31", "contributions": [{"id": "C1", "claim": "a"}, {"id": "C3", "claim": "b", "search": False}]}
        p = self.dir / "c.json"
        p.write_text(json.dumps(spec))
        briefs = novelty.plan(p, self.dir / "nv", 5, ROOT, self.dir)
        self.assertEqual([b.name for b in briefs], ["C1.brief.md"])
        self.assertEqual(json.loads((self.dir / "nv/plan.json").read_text())["skipped"], ["C3"])

    def test_plan(self):
        briefs = self.make()
        self.assertEqual(len(briefs), 2)
        t = briefs[0].read_text()
        self.assertIn("保密稿件", t)
        self.assertIn("2025-12-31", t)
        self.assertIn("fetch_arxiv.py", t)

    def result(self, cid, quote="The threshold is constant across denoising steps.", page=1, assessment="overlap"):
        return {"contribution_id": cid, "assessment": assessment, "rationale": "r", "sources": [],
                "comparisons": [{"comparator_id": "threshskip", "common_mechanism": "x", "differences": "y",
                                 "comparator_quotes": [{"quote": quote, "page": page}]}]}

    def test_merge_ok(self):
        self.make()
        for cid in ("C1", "C2"):
            (self.dir / "novelty" / f"{cid}.json").write_text(json.dumps(self.result(cid)))
        errors, warnings = novelty.merge(self.dir / "novelty", self.dir / "review.json")
        self.assertEqual(errors, [])
        r = json.loads((self.dir / "review.json").read_text())
        self.assertEqual({n["contribution_id"] for n in r["novelty"]}, {"C1", "C2"})
        self.assertTrue((self.dir / "novelty-comparisons.json").exists())
        self.assertTrue(any("used by" in w for w in warnings))

    def test_merge_rejects_bad_quote_and_missing(self):
        self.make()
        (self.dir / "novelty" / "C1.json").write_text(json.dumps(self.result("C1", quote="invented sentence about tokens")))
        errors, _ = novelty.merge(self.dir / "novelty", self.dir / "review.json")
        self.assertTrue(any("quote not in snapshot" in e for e in errors))
        self.assertTrue(any("C2: result file missing" in e for e in errors))


class TestCritique(Base):
    def test_lint_and_resolution_required(self):
        import critique
        r = copy.deepcopy(self.review)
        r.pop("critique", None)
        self.assertFalse(validate(r, self.dir, strict=True)["valid"])  # full mode without critique
        fs = critique.lint(r)
        self.assertTrue(any(f["type"] == "missing_literature" for f in fs))
        critique._store(r, fs)
        res = validate(r, self.dir)
        self.assertTrue(any("unresolved" in e for e in res["errors"]), res["errors"])
        for f in r["critique"]["findings"]:
            f.update(resolution="rejected", response="合成示例只比较提供的前作。")
        self.assertTrue(validate(r, self.dir, strict=True)["valid"])

    def test_lint_detects_problems(self):
        import critique
        r = copy.deepcopy(self.review)
        r["critique"] = {"abstractions": ["x"], "findings": []}
        r["overall"]["score"] = 6  # top of the ICML scale with several confirmed majors
        dup = copy.deepcopy(self.c("W03", r))
        dup["id"] = "W99"
        r["comments"].append(dup)
        self.c("W04", r)["confidence"] = "low"
        self.c("W04", r)["body"] += " 这显然是错误的。"
        types = {f["type"] for f in critique.lint(r)}
        self.assertTrue({"inconsistent", "duplicate", "severity", "tone"} <= types, types)

    def test_merge_checks_quotes(self):
        import critique
        d = self.tmp / "crit.json"
        d.write_text(json.dumps({"critic": "t", "findings": [
            {"id": "K01", "type": "misread", "target": "W02", "severity": "major", "detail": "x",
             "evidence": [{"source_id": "paper", "quote": "a sentence that is not in the paper at all", "page": 1}]}]}))
        self.assertTrue(critique.merge(d, self.dir / "review.json"))
        d.write_text(json.dumps({"critic": "t", "findings": [
            {"id": "K01", "type": "misread", "target": "W02", "severity": "major", "detail": "x",
             "evidence": [{"source_id": "paper", "quote": "AdaRoute reduces sampling latency by 52%", "page": 1}]}]}))
        self.assertEqual(critique.merge(d, self.dir / "review.json"), [])
        r = json.loads((self.dir / "review.json").read_text())
        self.assertTrue(any(f["source"] == "critic" for f in r["critique"]["findings"]))

    def test_plan_brief(self):
        import critique
        p = critique.plan(self.dir / "review.json", self.dir / "crit")
        t = p.read_text()
        self.assertIn("上位问题", t)
        self.assertIn("不得检索", t)
        self.assertNotIn("CCA", t)


class TestRevision(Base):
    def snaps(self):
        import revision
        return revision._snap(str(self.dir / "sources/paper.txt")), revision._snap(str(self.dir / "sources/revised.txt"))

    def test_board_and_priority(self):
        import revision
        b = revision.board(self.review, [{"reviewer": "R1", "text": "Need more baselines", "severity": "major"}])
        ids = [i["id"] for i in b["issues"]]
        self.assertIn("I-W01", ids)
        self.assertIn("I-R01", ids)
        self.assertNotIn("I-S01", ids)  # strengths are not issues
        b["issues"][0]["plan"]["cost"] = "low"
        self.assertEqual(revision.plan_order(b)[0]["id"], b["issues"][0]["id"])

    def test_diff(self):
        import revision
        old, new = self.snaps()
        d = revision.diff(old, new)
        self.assertTrue(any("38%" in a["text"] for a in d["added"]))
        self.assertTrue(any("52%" in a["text"] for a in d["removed"]))

    def test_verify_rules(self):
        import revision
        data = json.loads((self.dir / "revision/issues.json").read_text())
        for it in data["issues"]:  # reset statuses produced by the demo run
            if it["status"] in ("verified", "failed"):
                it["status"] = "done"
        old, new = self.snaps()
        revision.verify(data, old, new)
        st = {i["id"]: i["status"] for i in data["issues"]}
        self.assertEqual(st["I-W02"], "verified")
        self.assertEqual(st["I-W06"], "verified")  # removal
        self.assertEqual(st["I-W03"], "failed")    # cites text that already existed
        ok, blocking = revision.gate(data)
        self.assertFalse(ok)
        self.assertEqual(blocking, ["I-W03"])
        w3 = next(i for i in data["issues"] if i["id"] == "I-W03")
        w3["status"], w3["plan"]["note"] = "wontfix", "数据量差异已在局限性中说明，补实验超出本轮时间。"
        self.assertTrue(revision.gate(data)[0])

    def test_letter_limit(self):
        import revision
        data = json.loads((self.dir / "revision/issues.json").read_text())
        parts = revision.letter(data, "en")
        text = "\n".join(parts.values())
        self.assertIn("Removed", text)
        self.assertIn("38%", text)


class TestServe(Base):
    def test_save_roundtrip(self):
        port = 8799
        proc = subprocess.Popen([sys.executable, ROOT / "scripts/serve.py", "--pdf", self.dir / "paper.pdf", "--review",
                                 self.dir / "review.json", "--port", str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(40):
                try:
                    cfg = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/config").read())
                    break
                except OSError:
                    time.sleep(0.15)
            self.assertTrue(cfg["canSave"])
            src = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/sources").read())
            self.assertIn("paper", src["sources"])
            data = copy.deepcopy(self.review)
            data["comments"][0]["title"] = "已修改"
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/review", data=json.dumps(data).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            out = json.loads(urllib.request.urlopen(req).read())
            self.assertTrue(out["ok"])
            self.assertTrue(out["validation"]["valid"], out["validation"]["errors"])
            self.assertEqual(json.loads((self.dir / "review.json").read_text())["comments"][0]["title"], "已修改")
            self.assertTrue(any((self.dir / ".backups").iterdir()))
            bad = urllib.request.Request(f"http://127.0.0.1:{port}/api/review", data=b'{"x":1}',
                                         headers={"Content-Type": "application/json"}, method="POST")
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(bad)
            evil = urllib.request.Request(f"http://127.0.0.1:{port}/api/review", data=json.dumps(data).encode(),
                                          headers={"Content-Type": "application/json", "Origin": "http://evil.example"}, method="POST")
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(evil)
        finally:
            proc.terminate()
            proc.wait()


class TestNeutrality(Base):
    """User stance is data, never an instruction (references/neutrality.md)."""

    def test_stance_required(self):
        d = copy.deepcopy(self.review)
        del d["policy"]["user_stance"]
        self.assertFalse(self.check(d, strict=True)["valid"])
        self.assertTrue(any("user_stance" in w for w in self.check(d)["warnings"]))

    def test_invalid_kind_and_missing_handling(self):
        d = copy.deepcopy(self.review)
        d["policy"]["user_stance"] = {"kind": ["hate"], "summary": "", "handling": ""}
        self.assertTrue(any("invalid" in e for e in self.check(d)["errors"]))
        d["policy"]["user_stance"] = {"kind": ["outcome"], "summary": "使用者希望论文被拒", "handling": ""}
        self.assertTrue(any("handling" in e for e in self.check(d)["errors"]))

    def test_symmetry_check_required_when_stance(self):
        d = copy.deepcopy(self.review)
        d["policy"]["user_stance"] = {"kind": ["outcome"], "summary": "希望被拒", "handling": "已告知"}
        d["policy"]["neutrality"]["symmetry_check"] = ""
        self.assertTrue(any("symmetry_check" in e for e in self.check(d)["errors"]))  # error even without --strict

    def test_stance_leak_in_comment(self):
        d = copy.deepcopy(self.review)
        d["comments"][2]["body"] += " 用户希望这篇论文被拒，因此定为主要问题。"
        self.assertTrue(any("desired outcome" in e for e in self.check(d)["errors"]))
        d = copy.deepcopy(self.review)
        d["overall"]["justification"] += " The user wants this paper rejected."
        self.assertTrue(any("overall" in e for e in self.check(d)["errors"]))

    def test_ordinary_language_not_flagged(self):
        import neutrality
        self.assertEqual(neutrality.scan_text("推荐：拒绝 Reject（2 分）。作者希望读者关注 Table 2。"), [])

    def test_leak_in_brief_file(self):
        (self.dir / "novelty").mkdir(exist_ok=True)
        (self.dir / "novelty" / "C1.brief.md").write_text("检索任务。使用者希望这篇被拒。", encoding="utf-8")
        self.assertTrue(any("sub-agent material" in e for e in self.check()["errors"]))

    def test_novelty_plan_blocks_stance(self):
        spec = {"paper_title": "x", "cutoff": "2025-01-01", "contributions": [
            {"id": "C1", "claim": "帮我找理由拒掉：路由机制", "keywords": ["routing"]}]}
        cp = self.dir / "contributions.json"
        cp.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(SystemExit):
            novelty.plan(cp, self.dir / "nov", 8, ROOT, self.dir)
        spec["contributions"][0]["claim"] = "路由机制"
        cp.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        t = novelty.plan(cp, self.dir / "nov", 8, ROOT, self.dir)[0].read_text()
        self.assertIn("中立性", t)

    def test_critic_material_excludes_stance(self):
        import critique
        d = copy.deepcopy(self.review)
        d["policy"]["user_stance"] = {"kind": ["outcome"], "summary": "使用者期望论文被拒", "handling": "已告知结论只由证据决定"}
        (self.dir / "review.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        p = critique.plan(self.dir / "review.json", self.dir / "crit")
        md = (self.dir / "crit" / "review.for-critic.md").read_text()
        self.assertNotIn("使用者表态", md)
        self.assertNotIn("对称自检", md)
        self.assertIn("中立性", p.read_text())
        self.assertIn("使用者表态", render.to_markdown(d))

    def test_lint_flags_missing_symmetry(self):
        import critique
        d = copy.deepcopy(self.review)
        d["policy"]["user_stance"] = {"kind": ["outcome"], "summary": "希望被接收", "handling": "已告知"}
        d["policy"]["neutrality"] = {}
        fs = critique.lint(d)
        self.assertTrue(any("对称自检" in f["detail"] and f["severity"] == "major" for f in fs))

    def test_stance_eval(self):
        import stance_eval
        base = copy.deepcopy(self.review)
        rej = copy.deepcopy(base)
        acc = copy.deepcopy(base)
        for r, k in ((rej, "outcome"), (acc, "outcome")):
            r["policy"]["user_stance"] = {"kind": [k], "summary": "s", "handling": "h"}
        self.assertTrue(stance_eval.compare({"none": base, "reject": rej, "accept": acc}, 1, 1)["pass"])
        acc["overall"]["score"] = 5
        for c in acc["comments"]:
            if c.get("severity") == "major":
                c["severity"] = "minor"
        res = stance_eval.compare({"none": base, "reject": rej, "accept": acc}, 1, 1)
        self.assertFalse(res["pass"])
        self.assertTrue(any("总分" in f for f in res["failures"]))
        rej["policy"]["user_stance"] = {"kind": ["none"]}
        self.assertTrue(any("reject" in f for f in stance_eval.compare({"none": base, "reject": rej, "accept": base}, 1, 1)["failures"]))


class TestIntegrity(unittest.TestCase):
    def test_modified_rules_detected(self):
        import integrity
        tmp = Path(tempfile.mkdtemp())
        try:
            sk = tmp / "skill"
            shutil.copytree(ROOT, sk, ignore=shutil.ignore_patterns("examples", "__pycache__", ".backups"))
            self.assertTrue(integrity.status(sk)["rules_official"], integrity.status(sk)["changed"])
            f = sk / "references" / "neutrality.md"
            f.write_text(f.read_text(encoding="utf-8").replace("不可协商", "可协商"), encoding="utf-8")
            st = integrity.status(sk)
            self.assertFalse(st["rules_official"])
            self.assertIn("references/neutrality.md", st["changed"])
            (sk / "SKILL.md").write_bytes((sk / "SKILL.md").read_bytes().replace(b"\n", b"\r\n"))
            self.assertNotIn("SKILL.md", integrity.status(sk)["changed"])  # line endings do not matter
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def _fill_submission(sk):
    """Replace TODO prose in a skeleton with neutral English of the right length."""
    s1 = "The evaluation supports the main claim in the reported setting, as shown in Table 1 of the paper."
    lv = {"short": s1, "medium": " ".join([s1] * 3), "long": " ".join([s1] * 6)}
    sk["summary"] = "The paper proposes a method and evaluates it on standard benchmarks. " * 4
    sk["venue_note"] = "Labels follow the generic form."
    for k in ("strengths", "weaknesses", "questions"):
        for it in sk[k]:
            it["title"] = "Item title"
            it.update(lv)
    sk["justification"] = dict(lv)
    for k, v in list(sk["scores"].items()):
        sk["scores"][k] = v.replace("TODO venue label", "label")
    return sk


class TestSubmission(Base):
    def setUp(self):
        super().setUp()
        import submission
        self.S = submission
        self.sub = _fill_submission(submission.skeleton(self.review))

    def errs(self, sub, strict=False):
        return self.S.check(sub, self.review, strict)["errors"]

    def test_skeleton_passes_when_filled(self):
        self.assertEqual(self.errs(self.sub), [])
        ids = [c["id"] for c in self.review["comments"] if c.get("status") == "strength"]
        self.assertEqual([it["from"][0] for it in self.sub["strengths"]], ids)

    def test_text_rules(self):
        for bad, frag in [("See W01 for details.", "internal id"), ("这里有中文。", "Chinese"),
                          ("The cost is $O(n^2) per step.", "unbalanced $"),
                          ("As noted in review.json, this fails.", "process term"),
                          ("The user wants the paper to be rejected.", "stance")]:
            sub = copy.deepcopy(self.sub)
            sub["weaknesses"][0]["medium"] = bad
            self.assertTrue(any(frag in e for e in self.errs(sub)), (bad, self.errs(sub)))
        sub = copy.deepcopy(self.sub)
        sub["weaknesses"][0]["short"] = "The gain is $3\\times$ with $$\\sum_i x_i$$ and a cost of \\$5."
        self.assertEqual(self.errs(sub), [])
        sub["weaknesses"][0]["short"] = "The parameter α is not tuned."
        self.assertEqual(self.errs(sub), [])
        self.assertTrue(any("outside math" in e for e in self.errs(sub, strict=True)))

    def test_one_paragraph_and_levels(self):
        sub = copy.deepcopy(self.sub)
        sub["strengths"][0]["long"] = "First paragraph.\n\nSecond paragraph."
        self.assertTrue(any("one paragraph" in e for e in self.errs(sub)))
        sub = copy.deepcopy(self.sub)
        sub["justification"].pop("medium")
        self.assertTrue(any("justification" in e for e in self.errs(sub)))

    def test_scores_and_coverage(self):
        sub = copy.deepcopy(self.sub)
        sub["scores"]["rating"] = "1: reject"
        self.assertTrue(any("overall.score" in e for e in self.errs(sub)))
        majors = [c["id"] for c in self.review["comments"]
                  if c.get("severity") == "major" and c.get("status") in ("confirmed", "limitation")]
        if majors:
            sub = copy.deepcopy(self.sub)
            sub["weaknesses"] = [w for w in sub["weaknesses"] if majors[0] not in w["from"]]
            self.assertTrue(any(majors[0] in e and "not covered" in e for e in self.errs(sub)))
        sub = copy.deepcopy(self.sub)
        sub["weaknesses"][0]["from"] = ["X99"]
        self.assertTrue(any("not a review.json comment" in e for e in self.errs(sub)))

    def test_build_fills_template(self):
        page = self.S.build(self.sub, self.review)
        self.assertNotIn("{{BPR_", page)
        self.assertIn(self.review["paper"]["title"].split()[0], page)
        self.assertIn('id="data"', page)
        self.assertNotIn("OpenReview", page)
        self.assertIn("katex", page.lower())

    def test_evidence_and_finalize(self):
        r = copy.deepcopy(self.review)
        r["mode"] = "full"
        r.setdefault("policy", {}).pop("submission_skipped", None)
        self.assertTrue(any("submission_en.json" in e for e in validate(r, self.dir, True)["errors"]))
        r["policy"]["submission_skipped"] = "demo"
        self.assertFalse(any("no submission_en.json" in e for e in validate(r, self.dir, True)["errors"]))
        (self.dir / "submission_en.json").write_text(json.dumps(self.sub), encoding="utf-8")
        self.assertFalse(any("submission_en.json" in e for e in validate(self.review, self.dir)["errors"]))
        import finalize
        name = finalize.folder_name({"review_date": "2026-11-01", "paper": {"title": "SCRAPL: Scattering-Transform (v2)"}})
        self.assertEqual(name, "20261101_SCRAPL_Scattering_Transform_v2")
        out = self.tmp / "reviews"
        (out / name).mkdir(parents=True)
        self.assertEqual(finalize.target_dir(out, name).name, name + "_2")
        pdfs = list(self.dir.glob("*.pdf")) + list(self.dir.parent.glob("*.pdf"))
        if pdfs:
            res = subprocess.run([sys.executable, str(ROOT / "scripts" / "finalize.py"), str(self.dir), "--pdf", str(pdfs[0]),
                                  "--out", str(out), "--force"], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            made = [d for d in out.iterdir() if d.name != name]
            self.assertEqual(len(made), 1)
            top = sorted(p.name for p in made[0].iterdir())
            self.assertEqual(top, ["comments_submission.html", "review-viewer.html", "review_process_files", "source"])
            self.assertTrue((made[0] / "source" / "submission_en.json").exists())
            self.assertTrue((self.dir / "submission_en.json").exists())  # WORK untouched
            page = (made[0] / "review-viewer.html").read_text(encoding="utf-8")
            self.assertIn('id="pane-digest"', page)  # finalize writes the reader


class TestViewerPages(Base):
    def test_reader_default_and_editor(self):
        import package_viewer
        pdf = self.dir / "paper.pdf"
        reader = package_viewer.build(pdf, self.dir / "review.json")
        for frag in ('id="pane-digest"', 'id="thumbs"', "__BPR_EMBED__", "katex", "pdfjsLib"):
            self.assertIn(frag, reader)
        self.assertNotIn('href="viewer.css"', reader)
        self.assertNotIn('src="viewer.js"', reader)
        self.assertEqual(reader.count("<script>window.__BPR_EMBED__="), 1)
        editor = package_viewer.build(pdf, self.dir / "review.json", editor=True)
        self.assertIn('id="btnRegion"', editor)
        self.assertNotIn('id="pane-digest"', editor)
        res = subprocess.run([sys.executable, str(ROOT / "scripts" / "package_viewer.py"), str(pdf), str(self.dir / "review.json"),
                              str(self.tmp / "e.html"), "--editor"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn('id="btnRegion"', (self.tmp / "e.html").read_text(encoding="utf-8"))


class TestFocusV15(unittest.TestCase):
    """v1.5: weaknesses ordered by impact on core claims; tier C merged."""

    def setUp(self):
        import submission
        self.sub = submission
        self.review = {"comments": [
            {"id": "W01", "status": "confirmed", "severity": "major", "tier": "C"},
            {"id": "W02", "status": "confirmed", "severity": "major", "tier": "B"},
            {"id": "W03", "status": "limitation", "severity": "minor", "tier": "C"},
            {"id": "W04", "status": "confirmed", "severity": "minor", "tier": "B"}],
            "overall": {"core_claims": [{"claim": "x", "verdict": "partly", "impacts": {"W04": "cut", "W02": "narrow"}}]}}

    def test_rank(self):
        fr = self.sub.focus_rank(self.review)
        self.assertEqual(fr, {"W01": 3, "W02": 1, "W03": 3, "W04": 0})
        self.assertEqual(self.sub.focus_rank({"comments": [{"id": "W01", "status": "confirmed"}]}), {})

    def test_skeleton_order(self):
        sk = self.sub.skeleton(self.review)
        self.assertEqual([w["from"][0] for w in sk["weaknesses"]][:2], ["W04", "W02"])

    def test_check_warns(self):
        sub = {"weaknesses": [{"id": "W1", "from": ["W01"]}, {"id": "W2", "from": ["W03"]},
                              {"id": "W3", "from": ["W04"]}, {"id": "W4", "from": ["W02"]}]}
        res = self.sub.check(sub, self.review)
        msgs = " | ".join(res["warnings"])
        self.assertIn("v1.5 focus", msgs)
        self.assertIn("merge them into one item", msgs)


class TestScoreRangeV151(Base):
    """v1.5.1: score range follows core-claim verdicts only (calibration.md G7)."""

    def setUp(self):
        super().setUp()
        from evidence import score_range_check
        self.f = score_range_check
        self.ids = {"A1": {"tier": "A"}, "B1": {"tier": "B"}, "C1": {"tier": "C"}}

    def w(self, claims, score, sig="medium"):
        return " | ".join(self.f(claims, self.ids, score, sig))

    def test_missing_baseline_is_not_capped_at_3(self):
        cc = [{"verdict": "holds", "impacts": {"B1": "narrow"}}]
        self.assertEqual(self.w(cc, 4), "")          # v1.5 G7 would have capped this at 3
        self.assertEqual(self.w(cc, 3), "")
        self.assertIn("outside 3-4", self.w(cc, 5, "high"))

    def test_bands(self):
        self.assertIn("outside 1-2", self.w([{"verdict": "fails", "impacts": {"A1": "cut"}}], 3))
        self.assertEqual(self.w([{"verdict": "partly", "impacts": {"B1": "cut"}}], 3), "")
        self.assertIn("outside 2-3", self.w([{"verdict": "partly", "impacts": {}}], 4))
        self.assertEqual(self.w([{"verdict": "holds", "impacts": {"C1": "none"}}], 4), "")
        self.assertIn("needs significance high", self.w([{"verdict": "holds", "impacts": {}}], 5))
        self.assertEqual(self.w([{"verdict": "holds", "impacts": {}}], 6, "high"), "")

    def test_low_significance_extends_floor(self):
        self.assertEqual(self.w([{"verdict": "holds", "impacts": {}}], 3, "low"), "")
        self.assertIn("outside", self.w([{"verdict": "holds", "impacts": {}}], 3, "medium"))

    def test_contradictions(self):
        self.assertIn("tier C", self.w([{"verdict": "partly", "impacts": {"C1": "cut"}}], 3))
        self.assertIn("tier A", self.w([{"verdict": "fails", "impacts": {"A1": "narrow"}}], 2))
        self.assertIn("verdict holds although", self.w([{"verdict": "holds", "impacts": {"B1": "cut"}}], 4))
        self.assertIn("should be fails", self.w([{"verdict": "partly", "impacts": {"A1": "cut"}}], 3))

    def test_validate_reports_range_as_nonstrict_warning(self):
        self.review.setdefault("policy", {})["mode"] = "full"
        self.c("W01")["tier"] = "B"
        self.review["overall"].update({"score": 5, "significance": "medium", "g13_check": "x",
                                       "core_claims": [{"claim": "x", "verdict": "holds", "impacts": {"W01": "narrow"}}]})
        r = self.check()
        self.assertTrue(any("v1.5.1 G7" in w for w in r["warnings"]), r["warnings"])
        self.review["overall"]["significance"] = "huge"
        self.assertFalse(self.check()["valid"])


if __name__ == "__main__":
    unittest.main()

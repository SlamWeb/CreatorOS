"""Research SDK receipts and source bindings, without network or model calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock
from types import SimpleNamespace

from openai_codex.generated.v2_all import WebSearchThreadItem
from creatoros.integrations.codex_public_events import PublicEventCapture

from creatoros.evaluation.benchmark.research_evidence import collect_research_evidence, _url, MissingResearchEvidence
from creatoros.evaluation.benchmark.grounding_guard import qualify_grounding_judgments


URL = "https://dictionary.cambridge.org/dictionary/english/borrow"
BODY = "Dictionary definition and usage for the requested word. " * 3


def sha(body):
    return hashlib.sha256(body).hexdigest()


def event(action, line_url=None, **changes):
    result = {"method":"item/completed", "status":"completed", "at":"2026-11-05T00:00:00+00:00", "thread_id":"thread-a", "turn_id":"turn-a",
        "redacted":False, "omissions":[], "truncated":False, "incomplete":False,
        "payload":{"item":{"type":"webSearch", "action":action,
            "results": ([{"type":"text_result", "url":line_url,"title":"English dictionary", "status_code":200,
                          "content_type":"text/plain", "body":BODY, "truncated":False}] if line_url else [])}}}
    result.update(changes)
    return result


def sdk_open_event(results):
    item = WebSearchThreadItem.model_validate({"id":"open-1", "query":"", "type":"webSearch",
                                               "action":{"type":"openPage", "url":URL}, "results":results})
    with TemporaryDirectory() as directory:
        capture = PublicEventCapture(directory, "turn-a", "research", "thread-a")
        capture.observe(SimpleNamespace(method="item/completed", payload=SimpleNamespace(thread_id="thread-a", item=item)))
        result = json.loads(capture.path.read_text(encoding="utf8"))
    result["at"] = "2026-11-05T00:00:00+00:00"
    return result


class ResearchEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.prefix = "world-producer-skills-topic-research"
        self.batch = {"id":"batch-a", "series_id":"series-a", "status":"ready", "attempt":1,
            "thread_id":"thread-a", "candidates":[{"id":"c1", "title":"borrow", "angle":"receive from another person",
              "sources":[{"url":URL,"title":"Cambridge"}]}]}
        self.receipt = {"thread_id":"thread-a", "turns":[{"id":"turn-a", "phase":"research", "status":"completed"}]}
        self.events = [event({"type":"search", "query":"borrow meaning"}),
                       event({"type":"openPage", "url":URL}, URL)]
        self.files = {}
        self.write("batches/batch-a.json", self.batch)
        self.write("work/batch-a/1/worker_receipt.json", self.receipt)
        self.write_events(self.events)
        self.data = {"variables":{"creator_a":"creator-a","series_a":"series-a"},
            "before":{"files":{}}, "after":{"files":self.files,
                "database":{"series":[{"id":"series-a","creator_id":"creator-a"}]}},
            "background":{"research":[{"id":"batch-a","status":"ready"}]}}
        self.fetch = Mock(return_value={"url":URL, "status":200, "text":"A dictionary definition from the captured source. " * 3,
                                       "raw_sha256":sha(b"body"), "fetched_at":"2026-11-05T00:01:00+00:00"})

    def write(self, name, value):
        relative = self.prefix + "/" + name
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(value, ensure_ascii=False).encode()
        path.write_bytes(raw)
        self.files[relative] = sha(raw)

    def write_events(self, events):
        relative = self.prefix + "/work/batch-a/1/codex_public_events.jsonl"
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = ("\n".join(json.dumps(x, ensure_ascii=False) for x in events)+"\n").encode()
        path.write_bytes(raw)
        self.files[relative] = sha(raw)

    def collect(self):
        return collect_research_evidence(self.root, self.data, fetcher=self.fetch)

    def test_verified_search_open_and_independent_fetch_are_distinct(self):
        result = self.collect()
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["sources"][0]["url"], URL)
        self.assertEqual(result["sources"][0]["agent_text"], BODY)
        self.assertEqual(result["sources"][0]["agent_text_sha256"], sha(BODY.encode()))
        self.assertIn("events_sha256", result["provenance"])
        self.assertIn("do not prove", result["limitations"])
        self.fetch.assert_called_once_with(URL)
        judge = {"items":[{"id":"b48-s1", "pass":True, "evidence":"source supports claim"}],
                 "research_evidence":{**result, "sent_source_ids":["source-1"]}}
        self.assertIs(qualify_grounding_judgments({"id":"B48"}, judge)["items"][0]["pass"], True)
        judge["research_evidence"]["sent_source_ids"] = []
        self.assertIsNone(qualify_grounding_judgments({"id":"B48"}, judge)["items"][0]["pass"])

    def test_mixed_sources_keep_search_snippets_without_claiming_page_access(self):
        second = "https://www.merriam-webster.com/dictionary/lend"
        third = "https://dictionary.cambridge.org/dictionary/english/lend"
        self.batch["candidates"][0]["sources"] += [{"url":second,"title":"Merriam-Webster"},
                                                     {"url":third,"title":"Cambridge lend"}]
        self.write("batches/batch-a.json", self.batch)
        search = deepcopy(self.events[0])
        search["payload"]["item"]["results"] = [{"type":"text_result", "url":second,
            "title":"Lend definition", "snippet":"to give for temporary use"},
            {"type":"text_result", "url":"https://example.com/unrelated", "title":"Ignore", "snippet":"untrusted"}]
        self.write_events([search, self.events[1]])
        result = self.collect()
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(len(result["sources"]), 1)
        self.assertEqual(len(result["source_inventory"]), 3)
        self.assertEqual(result["source_inventory"][0]["agent_open"], "verified_body")
        self.assertEqual(result["source_inventory"][1]["agent_open"], "not_evidenced")
        snippet = result["source_inventory"][1]["search_snippets"][0]
        self.assertEqual(snippet["text"], "to give for temporary use")
        self.assertEqual(snippet["sha256"], sha(snippet["text"].encode()))
        self.assertEqual(result["source_inventory"][2]["search_snippets"], [])
        self.fetch.assert_called_once_with(URL)
        judge = {"items":[{"id":"b48-s1", "pass":True, "evidence":"snippet"}],
                 "research_evidence":{**result, "sent_source_ids":["source-1"]}}
        self.assertIsNone(qualify_grounding_judgments({"id":"B48"}, judge)["items"][0]["pass"])

    def test_post_task_source_fetch_is_separate_and_cannot_prove_agent_open(self):
        search = deepcopy(self.events[0])
        search["payload"]["item"]["results"] = [{"type":"text_result", "url":URL,
            "title":"Borrow", "snippet":"Receive something for a limited period"}]
        self.write_events([search, event({"type":"openPage", "url":URL}, None)])
        result=collect_research_evidence(self.root,self.data,fetcher=self.fetch,independent_fetch=True)
        self.fetch.assert_called_once_with(URL)
        self.assertEqual(result["status"],"needs_review")
        self.assertEqual(result["sources"],[])
        self.assertEqual(len(result["host_sources"]),1)
        self.assertEqual(result["source_inventory"][0]["agent_open"],"attempted_no_body")
        items={item["id"]:item["status"] for item in result["subchecks"]}
        self.assertEqual(items["research_search_observed"],"passed")
        self.assertEqual(items["source-1_agent_body"],"needs_review")
        self.assertEqual(items["source-1_posthoc_host_body"],"passed")
        self.assertEqual(items["source-1_claim_support"],"needs_review")
        base={"items":[{"id":"b48-s1","pass":True,"evidence":"source"},
                       {"id":"b48-source-1-support","pass":True,"evidence":"cited body"}],
              "research_evidence":{**result,"sent_source_ids":[],"sent_host_source_ids":["source-1"]}}
        qualified=qualify_grounding_judgments({"id":"B48"},base)
        self.assertIsNone(qualified["items"][0]["pass"])
        self.assertIs(qualified["items"][1]["pass"],True)
        partial=deepcopy(base)
        partial["items"][1]["evidence"]="该来源是词表；但仅支持等级标注这一部分主张。"
        qualified=qualify_grounding_judgments({"id":"B48"},partial)
        self.assertIsNone(qualified["items"][1]["pass"])
        self.assertIs(partial["items"][1]["pass"],True)
        full=deepcopy(base)
        full["items"][1]["evidence"]="该来源明确支持这条主张。"
        self.assertIs(qualify_grounding_judgments({"id":"B48"},full)["items"][1]["pass"],True)
        for mutation in ("not_sent","changed_text","wrong_url","missing_provenance"):
            bad=deepcopy(base)
            if mutation=="not_sent": bad["research_evidence"]["sent_host_source_ids"]=[]
            elif mutation=="changed_text": bad["research_evidence"]["host_sources"][0]["text"]+="tampered"
            elif mutation=="wrong_url": bad["research_evidence"]["host_sources"][0]["url"]+="other"
            else: del bad["research_evidence"]["provenance"]
            self.assertIsNone(qualify_grounding_judgments({"id":"B48"},bad)["items"][1]["pass"])

    def test_independent_fetch_is_opt_in_and_bounded_to_three_matched_sources(self):
        urls=[f"https://dictionary.cambridge.org/dictionary/english/word{i}" for i in range(5)]
        self.batch["candidates"][0]["sources"]=[{"url":url,"title":"Dictionary"} for url in urls]
        self.write("batches/batch-a.json",self.batch)
        search=deepcopy(self.events[0])
        search["payload"]["item"]["results"]=[{"type":"text_result","url":url,"title":"Dictionary","snippet":"brief"} for url in urls]
        self.write_events([search])
        result=self.collect()
        self.fetch.assert_not_called()
        self.assertEqual(result["host_sources"],[])
        self.fetch.side_effect=lambda url:{"url":url,"status":200,"text":BODY,"raw_sha256":sha(b"body"),
                                         "fetched_at":"2026-11-05T00:01:00+00:00"}
        result=collect_research_evidence(self.root,self.data,fetcher=self.fetch,independent_fetch=True)
        self.assertEqual(self.fetch.call_count,3)
        self.assertEqual(len(result["host_sources"]),3)
        self.assertEqual([row["host_fetch"] for row in result["source_inventory"]],
                         ["verified_body"]*3+["not_attempted"]*2)
        self.assertEqual(result["status"],"needs_review")

    def test_four_sources_do_not_exceed_the_old_three_source_limit(self):
        urls = [URL] + [f"https://dictionary.cambridge.org/dictionary/english/word{i}" for i in range(3)]
        self.batch["candidates"][0]["sources"] = [{"url":url,"title":"Dictionary"} for url in urls]
        self.write("batches/batch-a.json", self.batch)
        self.write_events([self.events[0]] + [event({"type":"openPage", "url":url},url) for url in urls])
        self.fetch.side_effect = lambda url: {"url":url,"status":200,"text":BODY,"raw_sha256":sha(b"body"),
                                               "fetched_at":"2026-11-05T00:01:00+00:00"}
        result = self.collect()
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(result["sources"]), 4)
        judge = {"items":[{"id":"b48-s1","pass":True,"evidence":"verified"}],
                 "research_evidence":{**result,"sent_source_ids":["source-1","source-2","source-3","source-4"]}}
        self.assertIs(qualify_grounding_judgments({"id":"B48"},judge)["items"][0]["pass"], True)
        judge["research_evidence"]["source_inventory"][1]["url"] = URL
        self.assertIsNone(qualify_grounding_judgments({"id":"B48"},judge)["items"][0]["pass"])

    def test_one_host_fetch_failure_does_not_discard_other_verified_source(self):
        second = "https://dictionary.cambridge.org/dictionary/english/lend"
        self.batch["candidates"][0]["sources"].append({"url":second,"title":"Cambridge"})
        self.write("batches/batch-a.json", self.batch)
        self.write_events([self.events[0],self.events[1],event({"type":"openPage","url":second},second)])
        good = self.fetch.return_value
        self.fetch.side_effect = [good, MissingResearchEvidence("timeout")]
        result = self.collect()
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(len(result["sources"]),1)
        self.assertEqual([x["host_fetch"] for x in result["source_inventory"]], ["verified_body","unavailable"])
        self.assertEqual(result["source_inventory"][1]["agent_open"], "verified_body")

    def test_missing_provenance_or_oversized_source_cannot_unlock_judge(self):
        evidence = self.collect()
        evidence["sent_source_ids"] = ["source-1"]
        judge = {"items":[{"id":"b48-s1", "pass":True, "evidence":"claimed"}], "research_evidence":evidence}
        for mutation in ("provenance", "oversized", "date", "tampered_text", "tampered_agent_text", "missing_agent_text"):
            item = deepcopy(judge)
            if mutation == "provenance":
                del item["research_evidence"]["provenance"]
            elif mutation == "oversized":
                item["research_evidence"]["sources"][0]["text"] = "x" * 16001
            elif mutation == "date":
                item["research_evidence"]["sources"][0]["fetched_at"] = "2026-11-04T00:00:00+00:00"
            elif mutation == "tampered_agent_text":
                item["research_evidence"]["sources"][0]["agent_text"] += " tampered"
            elif mutation == "missing_agent_text":
                del item["research_evidence"]["sources"][0]["agent_text"]
            else:
                item["research_evidence"]["sources"][0]["text"] += " tampered"
            self.assertIsNone(qualify_grounding_judgments({"id":"B48"}, item)["items"][0]["pass"])

    def test_other_action_search_only_or_missing_success_does_not_prove_open(self):
        for action, result_url in (({"type":"other"}, URL), ({"type":"open","url":URL}, None)):
            with self.subTest(action=action, result=result_url):
                self.write_events([self.events[0], event(action,result_url)])
                self.fetch.reset_mock()
                self.assertEqual(self.collect()["status"], "needs_review")
                self.fetch.assert_not_called()

    def test_wrong_thread_turn_order_or_incomplete_event_never_prove_grounding(self):
        variants = [[event({"type":"openPage","url":URL},URL),self.events[0]],
                    [self.events[0],event({"type":"openPage","url":URL},URL,thread_id="other")],
                    [self.events[0],event({"type":"openPage","url":URL},URL,turn_id="other")],
                    [self.events[0],event({"type":"openPage","url":URL},URL,truncated=True)]]
        for events in variants:
            with self.subTest(events=events):
                self.write_events(events)
                self.fetch.reset_mock()
                self.assertEqual(self.collect()["status"],"needs_review")
                self.fetch.assert_not_called()

    def test_real_sdk_serialization_requires_success_and_complete_body(self):
        valid = {"type":"text_result", "url":URL, "title":"Dictionary", "status_code":200,
                 "content_type":"text/plain", "body":BODY, "truncated":False}
        for changes in (None, {"status_code":403, "title":"Access Denied"}, {"status_code":None},
                        {"body":None}, {"truncated":True}, {"content_type":None},
                        {"url":"https://dictionary.cambridge.org/other"}, {"error":"failed"}):
            with self.subTest(changes=changes):
                results = None if changes is None else [{**valid, **changes}]
                sdk_event = sdk_open_event(results)
                self.assertEqual(sdk_event["status"], "completed")
                self.assertEqual(sdk_event["payload"]["item"]["action"]["type"], "openPage")
                self.write_events([self.events[0], sdk_event])
                self.fetch.reset_mock()
                self.assertEqual(self.collect()["status"], "needs_review")
                self.fetch.assert_not_called()
        self.write_events([self.events[0], sdk_open_event([valid])])
        result = self.collect()
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["sources"][0]["agent_text"], BODY)

    def test_wrong_owner_or_modified_snapshot_cannot_authorize_fetch(self):
        self.data["after"]["database"]["series"][0]["creator_id"] = "other"
        self.assertEqual(self.collect()["status"], "needs_review")
        self.fetch.assert_not_called()
        self.data["after"]["database"]["series"][0]["creator_id"] = "creator-a"
        path = self.root/self.prefix/"batches/batch-a.json"
        path.write_text("{}",encoding="utf8")
        self.assertEqual(self.collect()["status"], "needs_review")
        self.fetch.assert_not_called()

    def test_snapshot_missing_batch_old_batch_and_unavailable_body_are_unknown(self):
        old = deepcopy(self.data)
        old["before"]["files"] = dict(self.files)
        self.assertEqual(collect_research_evidence(self.root, old, fetcher=self.fetch)["status"],"needs_review")
        self.fetch.return_value = {"url":URL, "status":403}
        self.assertEqual(self.collect()["status"],"needs_review")

    def test_fetch_before_open_or_truncated_document_is_unknown(self):
        for changes in ({"fetched_at":"2026-11-04T23:59:59+00:00"}, {"text_truncated":True}):
            self.fetch.return_value.update(changes)
            self.assertEqual(self.collect()["status"], "needs_review")
            self.fetch.return_value = {"url":URL, "status":200, "text":"A dictionary definition from the captured source. " * 3,
                                       "raw_sha256":sha(b"body"), "fetched_at":"2026-11-05T00:01:00+00:00"}

    def test_network_allowlist_rejects_ssrf_redirect_candidates_and_credentials(self):
        for bad in ("http://dictionary.cambridge.org/dictionary/english/borrow",
                    "https://dictionary.cambridge.org.evil.example/x", "https://127.0.0.1/private",
                    "https://user:pass@dictionary.cambridge.org/x", "https://dictionary.cambridge.org:8443/x",
                    "https://dictionary.cambridge.org/x#frag", "https://dictionary.cambridge.org/x?token=secret",
                    "https://[::1"):
            with self.subTest(url=bad), self.assertRaises(MissingResearchEvidence): _url(bad)

    def test_baseline_v21_is_read_only_and_unknown_without_explicit_open(self):
        root = Path("data/agent-benchmark-v2/baseline-v21-001/records/0a7d5be8a8e449ccb2e9681569b282fd")
        if not root.is_dir():
            self.skipTest("Optional local historical evidence unavailable")
        def read(name): return json.loads((root / name).read_text(encoding="utf8"))
        evidence = {"variables":read("variables.json"), "before":read("before.json"),
                    "after":read("after.json"), "background":read("background.json")}
        fetch = Mock(side_effect=AssertionError("No external fetch without explicit opens"))
        result = collect_research_evidence(root/"world", evidence, fetcher=fetch)
        self.assertEqual(result["status"],"needs_review")
        fetch.assert_not_called()


if __name__ == "__main__": unittest.main()

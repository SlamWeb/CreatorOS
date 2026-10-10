"""Pure grader false-pass checks, no model or business operations."""
from copy import deepcopy
import unittest
from creatoros.evaluation.navigation import navigation_result


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.links = [{"url": "/series/a", "label": "查看栏目", "source_tool": "list_series_topics"}]
        self.doc = {"creator_id": "account-a", "entries": [{"kind": "assistant", "complete": True, "links": self.links}]}
        self.seen = {"host_links": self.links, "displayed_hrefs": ["/series/a"], "additional_turn_posts": 0,
            "checked": [{"url": "/series/a", "clicked": True, "destination_matches": True,
                         "resource_readable": True, "creator_id": "account-a"}]}

    def test_valid_click_not_raw_answer_accuracy(self):
        self.assertEqual(navigation_result(self.doc, self.seen, "E06", True)["status"], "passed")

    def test_missing_click_wrong_object_scope_replay_and_incomplete_fail(self):
        for field, value in [("clicked", False), ("destination_matches", False), ("resource_readable", False),
                             ("creator_id", "other"), ("url", "/series/b")]:
            seen = deepcopy(self.seen)
            seen["checked"][0][field] = value
            self.assertEqual(navigation_result(self.doc, seen, "E06", True)["status"], "failed")
        for field, value in [("additional_turn_posts", 1), ("checked", []), ("host_links", []), ("displayed_hrefs", [])]:
            self.assertEqual(navigation_result(self.doc, self.seen | {field: value}, "E06", True)["status"], "failed")
        self.assertEqual(navigation_result(self.doc, self.seen, "E06", False)["status"], "failed")

    def test_no_link_expected_only_on_unrelated_read(self):
        doc = {"creator_id": "account-a", "entries": [{"kind": "assistant", "complete": True, "links": []}]}
        seen = {"host_links": [], "displayed_hrefs": [], "checked": [], "additional_turn_posts": 0}
        self.assertEqual(navigation_result(doc, seen, "E01", True)["status"], "passed")
        self.assertEqual(navigation_result(doc, seen, "E06", True)["status"], "failed")

    def test_correct_url_but_hidden_batch_is_not_a_pass(self):
        seen, doc = deepcopy(self.seen), deepcopy(self.doc)
        url = "/series/a?research=batch-a"
        doc["entries"][0]["links"][0]["url"] = url
        seen["host_links"] = doc["entries"][0]["links"]
        seen["displayed_hrefs"] = [url]
        seen["checked"][0]["url"] = url
        self.assertEqual(navigation_result(doc, seen, "E09", True)["status"], "failed")
        seen["checked"][0]["research_record_visible"] = True
        self.assertEqual(navigation_result(doc, seen, "E09", True)["status"], "passed")

    def test_relevance_uses_independent_expectation_not_host_echo(self):
        self.assertEqual(navigation_result(self.doc, self.seen, "E10", True,
                         expected_urls=["/series/a"])["relevance_status"], "passed")
        result = navigation_result(self.doc, self.seen, "E10", True,
                                   expected_urls=["/series/a", "/runs/failed"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["relevance_status"], "failed")
        self.assertEqual(navigation_result(self.doc, self.seen, "E10", True,
                         expected_urls=[])["status"], "failed")

    def test_correct_run_url_with_only_heading_is_not_readable_delivery(self):
        doc, seen = deepcopy(self.doc), deepcopy(self.seen)
        url = "/runs/a"
        doc["entries"][0]["links"][0]["url"] = url
        seen["host_links"] = doc["entries"][0]["links"]
        seen["displayed_hrefs"] = [url]
        seen["checked"][0]["url"] = url
        self.assertEqual(navigation_result(doc, seen, "E10", True)["status"], "failed")
        seen["checked"][0]["production_record_visible"] = True
        self.assertEqual(navigation_result(doc, seen, "E10", True)["status"], "passed")


if __name__ == "__main__":
    unittest.main()

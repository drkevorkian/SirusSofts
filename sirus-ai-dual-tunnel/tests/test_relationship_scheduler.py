"""No autonomous relationship indexing during chat or while disabled."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"gentoo"))
import relationship_scheduler as sched

class ScheduleTests(unittest.TestCase):
    def test_chat_and_disabled_never_run_index(self):
        runner=Mock()
        for enabled,interactive in ((False,False),(False,True),(True,True)):
            result=sched.index_if_idle(enabled=enabled,interactive=interactive,
                source=Path("/unread"),index=Path("/unwritten"),run=runner)
            self.assertTrue(result["skipped"])
        runner.assert_not_called()
    def test_bounded_admission_only_when_chat_idle(self):
        runner=Mock(return_value={"refreshed":2})
        result=sched.index_if_idle(enabled=True,interactive=False,
            source=Path("/pdf.db"),index=Path("/derived.db"),run=runner)
        self.assertEqual(result["refreshed"],2)
        runner.assert_called_once_with(source=Path("/pdf.db"),
            index=Path("/derived.db"),batch=4)
    def test_invalid_state_fails_closed(self):
        with self.assertRaises(ValueError):
            sched.index_if_idle(enabled="yes",interactive=False,
                source=Path("/a"),index=Path("/b"))


    def test_review_recheck_is_chat_first_and_bounded(self):
        runner=Mock(return_value={"status":"monitoring","current_candidate_count":2})
        for enabled,chat in ((False,False),(False,True),(True,True)):
            result=sched.recheck_if_idle(enabled=enabled,interactive=chat,
                source=Path("/pdf"),index=Path("/idx"),queue=Path("/queue"),run=runner)
            self.assertTrue(result["skipped"])
        runner.assert_not_called()
        result=sched.recheck_if_idle(enabled=True,interactive=False,
            source=Path("/pdf"),index=Path("/idx"),queue=Path("/queue"),run=runner)
        self.assertEqual(result["status"],"monitoring")
        runner.assert_called_once_with(queue=Path("/queue"),
            source=Path("/pdf"),index=Path("/idx"))

    def test_review_recheck_rejects_implicit_state(self):
        with self.assertRaises(ValueError):
            sched.recheck_if_idle(enabled=1,interactive=False,
                queue=Path("/q"),source=Path("/s"),index=Path("/i"))

if __name__=="__main__":
    unittest.main()

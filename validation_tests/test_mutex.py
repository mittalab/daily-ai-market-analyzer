"""
Unit tests for JobMutex and @job_mutex decorator in new_utils/mutex.py.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from new_utils.mutex import JobMutex, job_mutex


class TestJobMutex(unittest.TestCase):

    def test_single_acquire_and_release(self):
        m = JobMutex("unit_test_job_1")
        self.assertTrue(m.acquire())
        m.release()

    def test_concurrent_acquire_fails(self):
        m1 = JobMutex("unit_test_job_2")
        self.assertTrue(m1.acquire())

        m2 = JobMutex("unit_test_job_2")
        self.assertFalse(m2.acquire())

        m1.release()

        # Now m2 should be able to acquire
        self.assertTrue(m2.acquire())
        m2.release()

    def test_context_manager(self):
        m1 = JobMutex("unit_test_job_3")
        with m1 as acquired1:
            self.assertTrue(acquired1)
            m2 = JobMutex("unit_test_job_3")
            with m2 as acquired2:
                self.assertFalse(acquired2)

        # After context exit, can acquire again
        m3 = JobMutex("unit_test_job_3")
        with m3 as acquired3:
            self.assertTrue(acquired3)

    def test_decorator_sync(self):
        calls = []

        @job_mutex("unit_test_job_4")
        def work(val):
            calls.append(val)
            # Inner attempt to run the same job concurrently should be rejected
            nested_run()

        @job_mutex("unit_test_job_4")
        def nested_run():
            calls.append("nested_should_not_run")

        work("first")
        self.assertEqual(calls, ["first"])


if __name__ == "__main__":
    unittest.main()

import time
import unittest

import workers


# Worker jobs must be module-level functions so a fresh process can import them.
def add(a, b):
    return a + b


def refuse():
    raise ValueError('Not a model file.')


def hog_memory(megabytes):
    block = b'x' * (megabytes * 2**20)  # written, so it really occupies memory
    time.sleep(2)
    return len(block)


def sleep_for(seconds):
    time.sleep(seconds)


class WorkerTests(unittest.TestCase):
    def test_returns_the_result(self):
        self.assertEqual(workers.run(add, 2, 3), 5)

    def test_value_errors_pass_through(self):
        with self.assertRaisesRegex(ValueError, 'Not a model file.'):
            workers.run(refuse)

    def test_memory_budget_stops_the_job_not_the_server(self):
        with self.assertRaisesRegex(workers.WorkerFailed, 'needs more than the 300 MB'):
            workers.run(hog_memory, 600, memory_mb=300)
        # The next job runs normally.
        self.assertEqual(workers.run(add, 1, 1), 2)

    def test_time_limit(self):
        with self.assertRaisesRegex(workers.WorkerFailed, 'took too long'):
            workers.run(sleep_for, 10, timeout=1)


if __name__ == '__main__':
    unittest.main()

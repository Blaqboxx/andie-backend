import unittest
import os
from andie_core.agents.app_builder_agent import AppBuilderAgent

class TestAppBuilderAgent(unittest.TestCase):
    def setUp(self):
        self.test_dir = "/tmp/andie_test"
        os.makedirs(self.test_dir, exist_ok=True)
        self.agent = AppBuilderAgent(self.test_dir)

    def tearDown(self):
        # Clean up test directory
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_scaffold_and_build(self):
        self.assertTrue(self.agent.scaffold_app("demo_app"))
        self.assertTrue(self.agent.build_app("demo_app"))

if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest


class SmokeTests(unittest.TestCase):
    def test_app_imports(self) -> None:
        from backend.main import app

        self.assertEqual(app.title, "HabitMeme Mobile")


if __name__ == "__main__":
    unittest.main()

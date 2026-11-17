from pathlib import Path
import sys
import unittest


def main():
    root = Path(__file__).resolve().parent
    suite = unittest.defaultTestLoader.discover(str(root / 'tests'), top_level_dir=str(root))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        return 1
    import selected_check
    selected_check.run()
    return 0


if __name__ == '__main__':
    sys.exit(main())

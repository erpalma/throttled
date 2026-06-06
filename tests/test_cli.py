import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_throttled():
    spec = importlib.util.spec_from_file_location('throttled_under_test', ROOT / 'throttled.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CLITests(unittest.TestCase):
    def test_argument_parser_disables_argparse_color_when_supported(self):
        throttled = load_throttled()

        parser = throttled.build_arg_parser()

        if hasattr(parser, 'color'):
            self.assertIs(parser.color, False)

    def test_help_output_has_no_ansi_escape_sequences(self):
        throttled = load_throttled()

        help_output = throttled.build_arg_parser().format_help()

        self.assertNotIn('\x1b[', help_output)


if __name__ == '__main__':
    unittest.main()

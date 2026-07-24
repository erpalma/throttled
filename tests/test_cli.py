import importlib.util
import io
import unittest
from contextlib import redirect_stdout
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

    def test_version_comes_from_package_metadata(self):
        throttled = load_throttled()
        output = io.StringIO()

        with redirect_stdout(output):
            with self.assertRaises(SystemExit) as exit_context:
                throttled.build_arg_parser().parse_args(['--version'])

        self.assertEqual(exit_context.exception.code, 0)
        self.assertTrue(output.getvalue().strip().endswith(f' {throttled.__version__}'))


if __name__ == '__main__':
    unittest.main()

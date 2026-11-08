import argparse
import contextlib
import io
import math
import os
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lifetime import add_lifetime_arguments,duration_from_arguments,deadline_for,watch_parent_pipe


class SessionLifetimeTests(unittest.TestCase):
    def parser(self):
        parser=argparse.ArgumentParser()
        add_lifetime_arguments(parser)
        return parser

    def test_continuous_mode_is_explicit(self):
        parser=self.parser()
        self.assertEqual(duration_from_arguments(parser,parser.parse_args([]),1800),1800)
        self.assertIsNone(duration_from_arguments(parser,parser.parse_args(['--until-stopped']),1800))
        self.assertTrue(math.isinf(deadline_for(None)))

    def test_duration_and_continuous_mode_cannot_be_combined(self):
        parser=self.parser()
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            parser.parse_args(['--seconds','30','--until-stopped'])

    def test_zero_seconds_is_not_an_implicit_continuous_session(self):
        parser=self.parser()
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            duration_from_arguments(parser,parser.parse_args(['--seconds','0']),1800)

    def test_parent_pipe_eof_requests_cleanup(self):
        reader,writer=os.pipe()
        stopped=threading.Event()
        thread=watch_parent_pipe(reader,stopped.set)
        try:
            os.write(writer,b'ignored input')
            self.assertFalse(stopped.wait(.03))
            os.close(writer);writer=None
            self.assertTrue(stopped.wait(1))
            thread.join(1)
            self.assertFalse(thread.is_alive())
        finally:
            if writer is not None:os.close(writer)
            os.close(reader)

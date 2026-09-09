"""Target canonicalization and scope matching tests (TASK_SCHEMA.md s17)."""
import os
import tempfile
import unittest

from policy.canonical import Canonicalizer, match_scope

from core import TargetType


class CanonicalizationTests(unittest.TestCase):

    def setUp(self):
        self.canon = Canonicalizer(resolve_symlinks=False)

    def test_path_normalization(self):
        self.assertEqual(
            self.canon.canonicalize(TargetType.FILESYSTEM, "/data/./x/../y.txt"),
            "/data/y.txt")

    def test_relative_path_resolved_against_base(self):
        self.assertEqual(
            self.canon.canonicalize(TargetType.FILESYSTEM, "x.txt"), "/x.txt")

    def test_traversal_normalized(self):
        self.assertEqual(
            self.canon.canonicalize(TargetType.FILESYSTEM, "/data/../../etc/passwd"),
            "/etc/passwd")

    def test_nul_byte_is_ambiguous(self):
        self.assertIsNone(self.canon.canonicalize(TargetType.FILESYSTEM, "/data/a\x00b"))
        self.assertIsNone(self.canon.canonicalize(TargetType.PACKAGE, "com.x\x00y"))

    def test_empty_and_nonstring_are_ambiguous(self):
        self.assertIsNone(self.canon.canonicalize(TargetType.FILESYSTEM, ""))
        self.assertIsNone(self.canon.canonicalize(TargetType.FILESYSTEM, None))

    def test_package_validation(self):
        self.assertEqual(
            self.canon.canonicalize(TargetType.PACKAGE, "com.example.app"),
            "com.example.app")
        self.assertIsNone(self.canon.canonicalize(TargetType.PACKAGE, "com example!app"))
        self.assertIsNone(self.canon.canonicalize(TargetType.PACKAGE, "../etc/passwd"))

    def test_domain_normalization_and_idna(self):
        self.assertEqual(
            self.canon.canonicalize(TargetType.NETWORK_DOMAIN, "Example.COM."),
            "example.com")
        self.assertEqual(
            self.canon.canonicalize(TargetType.NETWORK_DOMAIN, "bücher.de"),
            "xn--bcher-kva.de")
        self.assertIsNone(self.canon.canonicalize(TargetType.NETWORK_DOMAIN, "exa mple.com"))

    def test_network_destination(self):
        self.assertEqual(
            self.canon.canonicalize(TargetType.NETWORK_DESTINATION, "1.2.3.4:8080"),
            "1.2.3.4:8080")
        self.assertIsNone(
            self.canon.canonicalize(TargetType.NETWORK_DESTINATION, "host:notaport"))

    def test_android_setting(self):
        self.assertEqual(
            self.canon.canonicalize(TargetType.ANDROID_SETTING, "Global.Airplane_Mode_On"),
            "global.airplane_mode_on")
        self.assertIsNone(
            self.canon.canonicalize(TargetType.ANDROID_SETTING, "bad setting!"))

    def test_ui_process_resource(self):
        self.assertEqual(
            self.canon.canonicalize(TargetType.UI, "  OK Button  "), "OK Button")
        self.assertEqual(
            self.canon.canonicalize(TargetType.PROCESS, " com.termux "), "com.termux")
        self.assertEqual(
            self.canon.canonicalize(TargetType.RESOURCE, "android_foreground"),
            "ANDROID_FOREGROUND")

    def test_symlink_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "real"))
            try:
                os.symlink(os.path.join(tmp, "real"), os.path.join(tmp, "link"))
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable on this platform")
            canon = Canonicalizer(base_dir=tmp, resolve_symlinks=True)
            self.assertEqual(
                canon.canonicalize(TargetType.FILESYSTEM, os.path.join(tmp, "link", "f.txt")),
                os.path.realpath(os.path.join(tmp, "real", "f.txt")))


class ScopeMatchingTests(unittest.TestCase):

    def test_exact_match(self):
        self.assertTrue(match_scope("/data/file.txt", ["/data/file.txt"]))

    def test_recursive_glob(self):
        self.assertTrue(match_scope("/data/file.txt", ["/data/**"]))
        self.assertTrue(match_scope("/data/a/b/c.txt", ["/data/**"]))

    def test_single_star_stays_in_segment(self):
        self.assertTrue(match_scope("/data/a.txt", ["/data/*.txt"]))
        self.assertFalse(match_scope("/data/sub/a.txt", ["/data/*.txt"]))

    def test_no_match(self):
        self.assertFalse(match_scope("/etc/passwd", ["/data/**"]))

    def test_prefix_is_not_sufficient(self):
        self.assertFalse(match_scope("/data2/x.txt", ["/data/**"]))

    def test_domain_glob(self):
        self.assertTrue(match_scope("api.example.com", ["*.example.com"]))
        self.assertTrue(match_scope("deep.sub.example.com", ["*.example.com"]))
        self.assertFalse(match_scope("example.org", ["*.example.com"]))

    def test_question_mark(self):
        self.assertTrue(match_scope("/data/a1.txt", ["/data/a?.txt"]))
        self.assertFalse(match_scope("/data/a12.txt", ["/data/a?.txt"]))


if __name__ == "__main__":
    unittest.main()

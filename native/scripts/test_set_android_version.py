import unittest

from set_android_version import set_android_version


EXAMPLE = '''android {
    defaultConfig {
        applicationId "com.kofadimpex.market"
        minSdkVersion 23
        versionCode 1
        versionName "1.0"
    }
}
'''


class AndroidVersionTests(unittest.TestCase):
    def test_replaces_version_code_and_version_name_without_changing_app_id(self):
        updated = set_android_version(EXAMPLE, "1.2.3", 123)
        self.assertIn('applicationId "com.kofadimpex.market"', updated)
        self.assertIn('versionCode 123', updated)
        self.assertIn('versionName "1.2.3"', updated)

    def test_invalid_input_fails_closed(self):
        for name in ('1.0.0; rm -rf /', '1.2', '01.2.3', '1.2.3-beta', '1.0.0"'):
            with self.assertRaises(ValueError, msg=name):
                set_android_version(EXAMPLE, name, 101)
        for code in (0, -1, 2_100_000_001, True):
            with self.assertRaises(ValueError):
                set_android_version(EXAMPLE, '1.2.3', code)

    def test_missing_version_fields_fails_closed(self):
        with self.assertRaises(ValueError):
            set_android_version('android { applicationId "x" }', "1.0.1", 2)


if __name__ == "__main__":
    unittest.main()

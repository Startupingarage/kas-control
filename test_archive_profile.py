import sys,unittest
from archive_profile import protect

@unittest.skipUnless(sys.platform=='win32','Windows DPAPI')
class ProfileTests(unittest.TestCase):
    def test_cookie_transfer_is_encrypted_and_roundtrips(self):
        data=b'private local session data'
        encrypted=protect(data)
        self.assertNotIn(data,encrypted)
        self.assertEqual(protect(encrypted,decrypt=True),data)
        with self.assertRaises(OSError):protect(b'invalid encrypted blob',decrypt=True)

if __name__=='__main__':unittest.main()

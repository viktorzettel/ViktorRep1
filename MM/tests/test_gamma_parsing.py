
import json
import unittest

class TestGammaParsing(unittest.TestCase):
    def test_list_parsing(self):
        # Case 1: API returns a list (Normal)
        data = [{"clobTokenIds": ["123", "456"]}]
        clob_ids = data[0].get('clobTokenIds', [])
        
        # My logic from main.py:
        if isinstance(clob_ids, str):
            try:
                clob_ids = json.loads(clob_ids)
            except:
                pass
                
        self.assertIsInstance(clob_ids, list)
        self.assertEqual(len(clob_ids), 2)
        self.assertEqual(clob_ids[0], "123")

    def test_stringified_parsing(self):
        # Case 2: API returns a stringified list (The Bug)
        data = [{"clobTokenIds": "[\"123\", \"456\"]"}]
        clob_ids = data[0].get('clobTokenIds', [])
        
        # My logic from main.py:
        if isinstance(clob_ids, str):
            try:
                clob_ids = json.loads(clob_ids)
            except:
                pass
                
        self.assertIsInstance(clob_ids, list)
        self.assertEqual(len(clob_ids), 2)
        self.assertEqual(clob_ids[0], "123")

if __name__ == '__main__':
    unittest.main()

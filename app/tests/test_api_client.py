import unittest
from unittest.mock import patch

import api_client


class ApiClientPaginationTest(unittest.TestCase):
    def test_pull_collects_all_server_pages(self):
        responses = [
            {
                "records": [{"local_id": "1"}],
                "server_time": "2026-08-24T00:00:00Z",
                "has_more": True,
                "next_offset": 1,
            },
            {
                "records": [{"local_id": "2"}],
                "server_time": "2026-08-24T00:00:01Z",
                "has_more": False,
                "next_offset": None,
            },
        ]
        with patch("api_client._request_json", side_effect=responses) as request:
            result = api_client.pull_sync_records("token", table_name="sale items")

        self.assertEqual([row["local_id"] for row in result["records"]], ["1", "2"])
        self.assertIn("offset=0", request.call_args_list[0].args[0])
        self.assertIn("offset=1", request.call_args_list[1].args[0])
        self.assertIn("table_name=sale+items", request.call_args_list[0].args[0])


if __name__ == "__main__":
    unittest.main()

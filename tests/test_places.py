"""Structured campus-place index: aliases, intent and coordinate payload."""

import tempfile
import unittest
from pathlib import Path

from kb.places import PlaceIndex

ROWS = [
    {
        "name": "明德楼（2号楼）", "alias": "2号楼", "category": "行政办公", "campus": "北京校区",
        "lat": 39.911914, "lon": 116.550726,
        "glat": 39.913001, "glon": 116.556565, "blat": 39.918879, "blon": 116.563093,
        "source": "校方3D热点", "acc": "中", "detail": "党政机关", "room": "", "note": "",
    },
    {
        "name": "立德楼（1号楼）", "alias": "1号楼", "category": "教学科研", "campus": "北京校区",
        "lat": 39.910682, "lon": 116.550215,
        "glat": 39.911770, "glon": 116.556055, "blat": 39.917657, "blon": 116.562581,
        "source": "校方3D热点", "acc": "中", "detail": "", "room": "", "note": "",
    },
    {
        "name": "校医院", "alias": "", "category": "医疗/附属", "campus": "北京校区",
        "lat": 39.911900, "lon": 116.552846,
        "glat": 39.912983, "glon": 116.558680, "blat": 39.918825, "blon": 116.565220,
        "source": "校方3D热点", "acc": "中", "detail": "", "room": "",
        "note": "2025-09-30 起迁入20号楼",
    },
    {
        "name": "校医院(41号楼)", "alias": "", "category": "医疗/附属", "campus": "北京校区",
        "lat": 39.914431, "lon": 116.548976,
        "glat": 39.915522, "glon": 116.554819, "blat": 39.921430, "blon": 116.561335,
        "source": "校方3D热点", "acc": "中", "detail": "", "room": "", "note": "旧地址",
    },
    {
        "name": "研究生院·综合办", "alias": "", "category": "办公地点", "campus": "北京校区",
        "lat": 39.910767, "lon": 116.552473,
        "glat": 39.911850, "glon": 116.558307, "blat": 39.917699, "blon": 116.564847,
        "source": "校方3D热点", "acc": "中", "detail": "研究生院", "room": "艺术与传播大楼3层",
        "note": "",
    },
    {
        "name": "图书馆", "alias": "大阅城", "category": "文化场馆", "campus": "北京校区",
        "lat": 39.913559, "lon": 116.551823,
        "glat": 39.914644, "glon": 116.557659, "blat": 39.920503, "blon": 116.564193,
        "source": "OpenStreetMap", "acc": "高", "detail": "", "room": "", "note": "",
    },
    {
        "name": "校史馆", "alias": "图书馆副楼一层", "category": "文化场馆", "campus": "北京校区",
        "lat": 39.913077, "lon": 116.551496,
        "glat": 39.914162, "glon": 116.557333, "blat": 39.920027, "blon": 116.563865,
        "source": "校方3D热点", "acc": "中", "detail": "", "room": "", "note": "",
    },
    {
        "name": "14–19号楼（东侧住宅带）", "alias": "", "category": "公寓/住宿", "campus": "北京校区",
        "lat": 39.911850, "lon": 116.554950,
        "glat": 39.912933, "glon": 116.560784, "blat": 39.918775, "blon": 116.567324,
        "source": "2018版校园图", "acc": "低", "detail": "", "room": "", "note": "楼号级待实测",
    },
]


class PlaceIndexTest(unittest.TestCase):
    def setUp(self):
        self.index = PlaceIndex(rows=ROWS)

    def names(self, query, **kwargs):
        return [place["name"] for place in self.index.search(query, **kwargs)]

    def test_exact_name_and_alias(self):
        self.assertEqual(self.names("明德楼"), ["明德楼（2号楼）"])
        self.assertEqual(self.names("2号楼"), ["明德楼（2号楼）"])
        self.assertEqual(self.names("1号楼"), ["立德楼（1号楼）"])

    def test_location_phrases_are_stripped(self):
        for query in ("请问明德楼在哪", "明德楼位置", "明德楼怎么走", "明德楼的坐标"):
            with self.subTest(query=query):
                self.assertEqual(self.names(query)[0], "明德楼（2号楼）")
                self.assertTrue(self.index.is_location_intent(query))

    def test_bare_name_is_location_intent(self):
        self.assertTrue(self.index.is_location_intent("图书馆"))
        self.assertFalse(self.index.is_location_intent("图书馆几点开门"))

    def test_parenthetical_number_does_not_steal_current_place(self):
        self.assertEqual(self.names("校医院"), ["校医院"])
        self.assertEqual(self.names("41号楼"), ["校医院(41号楼)"])

    def test_office_unit_matches(self):
        self.assertEqual(self.names("研究生院在哪"), ["研究生院·综合办"])

    def test_multiple_places_are_returned(self):
        self.assertEqual(set(self.names("图书馆和校史馆在哪")), {"图书馆", "校史馆"})

    def test_range_building_numbers(self):
        for number in (14, 15, 16, 17, 18, 19):
            with self.subTest(number=number):
                self.assertEqual(self.names(f"{number}号楼"), ["14–19号楼（东侧住宅带）"])

    def test_no_match(self):
        self.assertEqual(self.names("学生公寓床铺尺寸"), [])
        self.assertFalse(self.index.is_location_intent("学生公寓床铺尺寸"))

    def test_coordinate_payload(self):
        place = self.index.search("明德楼")[0]
        self.assertEqual(place["wgs84"], {"lat": 39.911914, "lon": 116.550726})
        self.assertEqual(place["gcj02"], {"lat": 39.913001, "lon": 116.556565})
        self.assertEqual(place["bd09"], {"lat": 39.918879, "lon": 116.563093})
        self.assertEqual(place["matched_term"], "明德楼")
        self.assertEqual(place["accuracy"], "中")

    def test_missing_file_is_empty(self):
        index = PlaceIndex()
        self.assertEqual(index.load(Path(tempfile.mkdtemp()) / "missing.json"), 0)
        self.assertEqual(index.search("明德楼"), [])


if __name__ == "__main__":
    unittest.main()
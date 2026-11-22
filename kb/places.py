"""Structured campus-place index for location queries.

`data/places.json` is generated from the 2026-11-20 campus location table
(`知识库地点坐标-20261120/`).  It gives every known campus place three
coordinate systems:

* ``wgs84``  -- GPS / OpenStreetMap / Leaflet / GeoJSON
* ``gcj02``  -- 高德 / 腾讯地图
* ``bd09``   -- 百度地图

The index is intentionally tiny (a few hundred rows) and does plain
substring/alias matching, so it adds well under a millisecond to a search and
does not depend on Qdrant or the embedding model.
"""

import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

# Words that signal the user is asking where a place is, rather than a rule or
# service question that merely mentions a building.
LOCATION_MARKERS = (
    "在哪",
    "哪里",
    "哪儿",
    "哪个楼",
    "哪栋",
    "什么位置",
    "什么地方",
    "方位",
    "位置",
    "坐标",
    "地址",
    "地点",
    "怎么走",
    "怎么去",
    "怎么到",
    "如何走",
    "如何到",
    "附近",
    "周边",
)

# Terms too generic to identify one place on their own.  They are dropped from
# the index; the full building names remain.
GENERIC_TERMS = {
    "楼",
    "馆",
    "中心",
    "学院",
    "学校",
    "大学",
    "公寓",
    "餐厅",
    "食堂",
    "办公室",
    "服务",
    "大厅",
    "广场",
    "公园",
    "校区",
    "地点",
    "位置",
    "地址",
}

PAREN_RE = re.compile(r"[（(]([^）)]*)[）)]")
ALIAS_SPLIT_RE = re.compile(r"[；;、]")
POLITE_PREFIX_RE = re.compile(r"^(请问|想问一下|麻烦问一下|我想问|问一下|帮我查一下|帮我查|查一下)")
# Keep punctuation that can be meaningful in a name (·, /, -, –), drop the rest.
STRIP_RE = re.compile(r"[\s，。！？!?、；;：:（）()《》〈〉“”‘’\"']")
RANGE_RE = re.compile(r"^(\d{1,2})[–-](\d{1,2})号楼")
NUMBERED_RE = re.compile(r"^\d+号楼$")


def normalize(text):
    """Case/width-insensitive form used for term matching."""
    text = unicodedata.normalize("NFKC", text or "").lower()
    return STRIP_RE.sub("", text)


class PlaceIndex:
    """Alias index over the campus location table."""

    def __init__(self, rows=None):
        self.rows = []
        self.term_index = {}
        self.exact_terms = set()
        if rows:
            self._build(rows)

    # ---- loading -----------------------------------------------------------

    def load(self, path):
        try:
            rows = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"cannot load places {path}: {exc}", flush=True)
            self.rows, self.term_index, self.exact_terms = [], {}, set()
            return 0
        if not isinstance(rows, list):
            print(f"places {path} is not a list; ignoring", flush=True)
            self.rows, self.term_index, self.exact_terms = [], {}, set()
            return 0
        self._build(rows)
        return len(self.rows)

    def _build(self, rows):
        self.rows = [row for row in rows if isinstance(row, dict) and row.get("name")]
        names = {row["name"] for row in self.rows}
        term_index = defaultdict(list)
        for row in self.rows:
            for term in self._terms(row, names):
                term_index[term].append(row)
        self.term_index = dict(term_index)
        self.exact_terms = set(term_index)

    def _terms(self, row, names):
        terms = set()
        name = str(row.get("name") or "").strip()
        if name:
            terms.add(name)
        for part in ALIAS_SPLIT_RE.split(str(row.get("alias") or "")):
            part = part.strip()
            if part:
                terms.add(part)
        # "研究生院·综合办" should also answer "研究生院".
        if "·" in name:
            unit = name.split("·", 1)[0].strip()
            if unit:
                terms.add(unit)
        # "立德楼（1号楼）" -> 立德楼 + 1号楼; "校医院(41号楼)" keeps only
        # 41号楼 because bare 校医院 belongs to the current 20号楼 row.
        base = PAREN_RE.sub("", name).strip()
        if base and base != name:
            if base not in names or base == name:
                terms.add(base)
            for inner in PAREN_RE.findall(name):
                inner = inner.strip()
                if inner:
                    terms.add(inner)
        match = RANGE_RE.match(name)
        if match:
            low, high = int(match.group(1)), int(match.group(2))
            if 0 < high - low <= 10:
                terms.update(f"{number}号楼" for number in range(low, high + 1))
        return {
            normalize(term)
            for term in terms
            if len(term) >= 2 and term not in GENERIC_TERMS
        }

    # ---- matching ----------------------------------------------------------

    def search(self, query, limit=5):
        """Return up to ``limit`` matching places, best match first."""
        text = normalize(query)
        if not text or not self.term_index:
            return []
        bare = self._strip_intent(text) or text
        scored = {}
        for term, rows in self.term_index.items():
            if self._contains(term, bare):
                score = 100 if bare == term else 70 + min(len(term), 12)
            elif len(bare) >= 4 and self._contains(bare, term):
                score = 50 + min(len(bare), 12)
            else:
                continue
            for row in rows:
                key = (row.get("name"), row.get("campus"))
                if key not in scored or score > scored[key][0]:
                    scored[key] = (score, term, row)
        ranked = sorted(
            scored.values(),
            key=lambda item: (-item[0], -len(item[1]), str(item[2].get("name") or "")),
        )
        # An exact name/alias wins outright: don't also return "南大门" for
        # "东南门" just because the shorter alias "南门" is a suffix.
        if any(score == 100 for score, _, _ in ranked):
            ranked = [item for item in ranked if item[0] == 100]
        public = []
        seen_points = set()
        for score, term, row in ranked:
            # An office row inside the building that already matched is noise
            # for a "where is X" answer: keep the building, drop the desk.
            point = (row.get("lat"), row.get("lon"))
            if row.get("category") == "办公地点" and point in seen_points:
                continue
            public.append(self._public(row, term, score))
            seen_points.add(point)
            if len(public) >= limit:
                break
        return public

    def is_location_intent(self, query):
        """True when the query is asking where a known place is."""
        text = normalize(query)
        if not text:
            return False
        if any(marker in text for marker in LOCATION_MARKERS):
            return True
        bare = self._strip_intent(text) or text
        return bare in self.exact_terms

    @staticmethod
    def _contains(needle, haystack):
        """Substring match that keeps numbered buildings from cross-matching.

        ``1号楼`` must not match ``41号楼`` or ``21号楼``; require no digit on
        either side for bare ``N号楼`` terms.
        """
        if NUMBERED_RE.match(needle):
            pattern = rf"(?<!\d){re.escape(needle)}(?!\d)"
            return re.search(pattern, haystack) is not None
        return needle in haystack

    def _strip_intent(self, text):
        for marker in sorted(LOCATION_MARKERS, key=len, reverse=True):
            text = text.replace(marker, "")
        text = POLITE_PREFIX_RE.sub("", text)
        return text.strip()

    def _public(self, row, term, score):
        def point(lat_key, lon_key):
            return {"lat": row.get(lat_key), "lon": row.get(lon_key)}

        item = {
            "name": row.get("name", ""),
            "alias": row.get("alias", ""),
            "category": row.get("category", ""),
            "campus": row.get("campus", ""),
            "wgs84": point("lat", "lon"),
            "gcj02": point("glat", "glon"),
            "bd09": point("blat", "blon"),
            "source": row.get("source", ""),
            "accuracy": row.get("acc", ""),
            "matched_term": term,
            "score": score,
        }
        for key, field in (("detail", "detail"), ("room", "room"), ("note", "note")):
            value = row.get(field)
            if value:
                item[key] = value
        return item
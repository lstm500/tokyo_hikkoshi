"""住まいコンパス v25 - verified red-button foreground search / progress

設計方針:
- 起動時は外部通信を行わない。
- streamlit-folium、子プロセス、常駐バックグラウンドスレッドを使わない。
- 物件取得はユーザーがボタンを押した時だけ実行する。
- 検索中は、地域判定・検索元・進捗・保存件数を文字で逐次表示する。
- 取得は地域 x 取得元単位で最大8並列。ネストしたThreadPoolは作らない。
- HOME'S / SUUMO の公開ページから候補を集め、SRC・築20年以内・対象間取り・位置情報を確認する。
- 確認済み物件はタスク完了ごとにSupabaseへ保存し、途中までの成果を残す。
- 地図表示はStreamlit標準 st.map のみを使う。
"""
from __future__ import annotations

import base64
import concurrent.futures
import functools
import html as html_lib
import json
import math
import os
import re
import queue
import threading
import time
import unicodedata
import urllib.robotparser
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import streamlit as st


APP_VERSION = "v25-search-storage-light"
TARGET_STRUCTURE = "SRC"
MAX_BUILDING_AGE = 20

STATIONS = {
    "東京": (35.6812, 139.7671),
    "品川": (35.6285, 139.7388),
    "大崎": (35.6197, 139.7286),
    "五反田": (35.6264, 139.7235),
    "目黒": (35.6339, 139.7158),
    "恵比寿": (35.6467, 139.7101),
    "渋谷": (35.6580, 139.7016),
    "新宿": (35.6909, 139.7003),
    "池袋": (35.7295, 139.7109),
}

LAYOUT_GROUPS = {
    "1K・1L・1DK": ("1K", "1L", "1DK"),
    "1LDK・2DK": ("1LDK", "2DK"),
    "2LDK・3DK": ("2LDK", "3DK"),
    "3LDK": ("3LDK",),
}
TARGET_LAYOUTS = tuple(dict.fromkeys(x for xs in LAYOUT_GROUPS.values() for x in xs))

MAP_TILE_URL = "https://www.homes.co.jp/_ajax/map/realestate_article/tile/"
MAP_INFO_URL = "https://www.homes.co.jp/_ajax/map/realestate_article/info_view/"
HOMES_ROOT = "https://www.homes.co.jp"
SUUMO_SEARCH_URL = "https://suumo.jp/jj/chintai/ichiran/FR301FC001/"
GSI_REVERSE_URL = "https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress"
GSI_SEARCH_URL = "https://msearch.gsi.go.jp/address-search/AddressSearch"
GSI_MUNI_URL = "https://maps.gsi.go.jp/js/muni.js"

HEADERS = {
    "User-Agent": "SumaiCompass/17.0 (personal rental map; contact via application owner)",
    "Accept-Language": "ja,en;q=0.8",
}

_HTTP_LOCAL = threading.local()


class StorageError(Exception):
    pass


def secret_value(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None:
        try:
            value = st.secrets.get(name, default)
        except Exception:
            value = default
    return str(value or "").strip()


def http_session():
    import requests
    if not hasattr(_HTTP_LOCAL, "session"):
        session = requests.Session()
        session.headers.update(HEADERS)
        _HTTP_LOCAL.session = session
    return _HTTP_LOCAL.session


def request(method: str, url: str, **kwargs):
    import requests
    kwargs.setdefault("timeout", (5, 20))
    kwargs.setdefault("allow_redirects", False)
    try:
        response = http_session().request(method, url, **kwargs)
    except requests.RequestException as exc:
        raise RuntimeError(f"接続失敗: {urlparse(url).hostname}") from exc
    if 300 <= response.status_code < 400:
        raise RuntimeError(f"取得先がリダイレクトしました: {urlparse(url).hostname}")
    response.raise_for_status()
    return response




def source_get(url: str, hostname: str, path_prefix: str, timeout=(6, 20)):
    """Follow only safe same-provider redirects for public listing/search pages."""
    import requests
    try:
        response = http_session().get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    except requests.RequestException as exc:
        raise RuntimeError(f"接続失敗: {hostname}") from exc
    response.raise_for_status()
    final = urlparse(response.url)
    if final.scheme != "https" or final.hostname != hostname or not final.path.startswith(path_prefix):
        raise RuntimeError(f"安全でないリダイレクトを検出: {hostname}")
    if len(response.content) > 8_000_000:
        raise RuntimeError("取得ページが大きすぎます")
    response.encoding = response.apparent_encoding or "utf-8"
    return response


def supabase_settings():
    url = secret_value("SUPABASE_URL").rstrip("/")
    key = secret_value("SUPABASE_SECRET_KEY") or secret_value("SUPABASE_SERVICE_ROLE_KEY")
    namespace = secret_value("SUPABASE_NAMESPACE", "sumai-compass")
    if not url or not key:
        raise StorageError("Streamlit Secrets に SUPABASE_URL と SUPABASE_SECRET_KEY を設定してください。")
    try:
        parsed = urlparse(url)
        valid = (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and parsed.hostname.endswith(".supabase.co")
            and not parsed.username
            and not parsed.password
            and parsed.port in (None, 443)
            and parsed.path in ("", "/")
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        valid = False
    if not valid:
        raise StorageError("SUPABASE_URL は https://プロジェクトID.supabase.co 形式で設定してください。")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", namespace):
        raise StorageError("SUPABASE_NAMESPACE の形式が不正です。")

    headers = {"apikey": key, "Accept": "application/json", "Content-Type": "application/json"}
    if not key.startswith("sb_secret_"):
        try:
            parts = key.split(".")
            payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
            if len(parts) != 3 or payload.get("role") != "service_role":
                raise ValueError()
        except Exception:
            raise StorageError("Secret key（sb_secret_…）または旧service_roleキーを使用してください。") from None
        headers["Authorization"] = "Bearer " + key
    return url, headers, namespace


def supabase_request(method: str, route: str, *, params=None, payload=None, prefer=None):
    import requests
    url, headers, _ = supabase_settings()
    if route not in {"sumai_properties", "sumai_metadata", "sumai_fetch_queries"}:
        raise StorageError("Supabase参照先が不正です。")
    headers = dict(headers)
    if prefer:
        headers["Prefer"] = prefer
    try:
        response = requests.request(
            method,
            f"{url}/rest/v1/{route}",
            headers=headers,
            params=params,
            json=payload,
            timeout=(4, 20),
            allow_redirects=False,
        )
    except requests.RequestException:
        raise StorageError("Supabaseに接続できませんでした。") from None
    if response.status_code in (401, 403):
        raise StorageError("SupabaseのSecret keyまたは権限を確認してください。")
    if response.status_code == 404:
        raise StorageError("Supabaseのsumai_propertiesテーブルが見つかりません。公開先の接続設定を確認してください。")
    if not 200 <= response.status_code < 300:
        try:
            error_code = str(response.json().get("code", ""))
        except (ValueError, AttributeError):
            error_code = ""
        if error_code in ("42703", "PGRST204"):
            message = "sumai_propertiesの必須列（namespace・identity・payload・fetched_at）を確認してください。"
        elif error_code == "42P10":
            message = "sumai_propertiesのnamespace・identityに複合UNIQUE制約が必要です。"
        elif response.status_code == 429 or response.status_code >= 500:
            message = "Supabaseが一時的に応答できません。検索結果は画面に保持しています。再試行してください。"
        else:
            message = "Supabaseのテーブル設定・接続状態を確認してください。"
        raise StorageError(f"Supabase処理失敗 HTTP {response.status_code}: {message}")
    if not response.content.strip():
        return None
    try:
        return response.json()
    except ValueError:
        return None


def bounds_from_center(lat: float, lng: float, radius_m: int):
    dlat = radius_m / 111_320
    dlng = radius_m / (111_320 * max(0.2, math.cos(math.radians(lat))))
    return [lat - dlat, lng - dlng, lat + dlat, lng + dlng]


def inside(lat: float, lng: float, bounds) -> bool:
    south, west, north, east = bounds
    return south <= lat <= north and west <= lng <= east


def distance_m(lat1, lng1, lat2, lng2):
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def strip_html(value: str) -> str:
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", str(value or ""))
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def text_from_fragment(fragment: str) -> str:
    return strip_html(fragment)


def money(value) -> int:
    text = unicodedata.normalize("NFKC", str(value or "")).replace(",", "").strip()
    if text in ("", "-", "なし", "無", "―"):
        return 0
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*万円", text)
    if m:
        return int(round(float(m.group(1)) * 10000))
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*円", text)
    if m:
        return int(round(float(m.group(1))))
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", text):
        return int(round(float(text)))
    raise ValueError("金額を解釈できません")


def normalize_structure(value: str) -> str:
    t = unicodedata.normalize("NFKC", str(value or "")).upper().replace(" ", "")
    if "鉄骨鉄筋" in t or re.search(r"(^|[^A-Z])SRC([^A-Z]|$)", t):
        return "SRC"
    if "鉄筋コンクリート" in t or re.search(r"(^|[^A-Z])RC([^A-Z]|$)", t):
        return "RC"
    if "軽量鉄骨" in t:
        return "軽量鉄骨"
    if "鉄骨" in t:
        return "鉄骨"
    if "木造" in t:
        return "木造"
    return t[:30]


def building_age_from_values(*values):
    now = datetime.now(timezone.utc)
    for raw in values:
        text = unicodedata.normalize("NFKC", str(raw or ""))
        if "新築" in text:
            return 0, now.year
        m = re.search(r"築\s*(\d{1,3})\s*年", text)
        if m:
            age = int(m.group(1))
            return age, now.year - age
        m = re.search(r"((?:19|20)\d{2})\s*年\s*(\d{1,2})?\s*月?", text)
        if m:
            year = int(m.group(1))
            month = int(m.group(2) or 1)
            age = now.year - year - (1 if now.month < month else 0)
            if 0 <= age <= 150:
                return age, year
    return None, None


def target_property(p: dict) -> bool:
    try:
        return (
            p.get("layout") in TARGET_LAYOUTS
            and normalize_structure(p.get("structure")) == TARGET_STRUCTURE
            and p.get("building_age") not in (None, "")
            and 0 <= int(p["building_age"]) <= MAX_BUILDING_AGE
            and 34 <= float(p["lat"]) <= 37
            and 138 <= float(p["lng"]) <= 141
            and float(p["area"]) > 0
            and int(p["rent"]) > 0
            and int(p.get("fees", 0)) >= 0
        )
    except (TypeError, ValueError, KeyError):
        return False


def extract_jsonld(html: str):
    blocks = re.findall(
        r"(?is)<script[^>]+type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        html,
    )
    for block in blocks:
        raw = html_lib.unescape(block.strip())
        try:
            yield json.loads(raw)
        except (ValueError, TypeError):
            continue


def walk_json(value):
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from walk_json(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk_json(v)


def extract_labeled_value(html: str, labels) -> str:
    for label in labels:
        e = re.escape(label)
        patterns = [
            rf"(?is)<th[^>]*>\s*(?:<[^>]+>\s*)*{e}(?:\s*</[^>]+>)*\s*</th>\s*<td[^>]*>(.*?)</td>",
            rf"(?is)<dt[^>]*>\s*(?:<[^>]+>\s*)*{e}(?:\s*</[^>]+>)*\s*</dt>\s*<dd[^>]*>(.*?)</dd>",
            rf"(?is)<[^>]+class=[\"'][^\"']*(?:label|title|name)[^\"']*[\"'][^>]*>\s*{e}\s*</[^>]+>\s*<[^>]+>(.*?)</[^>]+>",
        ]
        for pattern in patterns:
            m = re.search(pattern, html)
            if m:
                value = text_from_fragment(m.group(1))
                if value:
                    return value[:200]
    text = strip_html(html)
    for label in labels:
        m = re.search(re.escape(label) + r"\s*[:：]?\s*([^|｜]{1,60})", text)
        if m:
            return m.group(1).strip()[:200]
    return ""


def extract_coordinate_candidates(html: str):
    candidates = []
    seen = set()

    def add(lat, lng, source, priority):
        try:
            lat, lng = float(lat), float(lng)
        except (TypeError, ValueError):
            return
        if not (34 <= lat <= 37 and 138 <= lng <= 141):
            return
        key = (round(lat, 7), round(lng, 7))
        if key in seen:
            return
        seen.add(key)
        candidates.append({"lat": lat, "lng": lng, "source": source, "priority": priority})

    for data in extract_jsonld(html):
        for node in walk_json(data):
            geo = node.get("geo") if isinstance(node, dict) else None
            if isinstance(geo, dict):
                add(geo.get("latitude"), geo.get("longitude"), "JSON-LD地図座標", 100)

    normalized = unicodedata.normalize("NFKC", html)
    pair_patterns = [
        (r"[\"']lat(?:itude)?[\"']\s*[:=]\s*[\"']?(3[4-7]\.\d+)[\"']?.{0,160}?[\"'](?:lng|lon|longitude)[\"']\s*[:=]\s*[\"']?(1(?:3[8-9]|40)\.\d+)", False),
        (r"[\"'](?:lng|lon|longitude)[\"']\s*[:=]\s*[\"']?(1(?:3[8-9]|40)\.\d+)[\"']?.{0,160}?[\"']lat(?:itude)?[\"']\s*[:=]\s*[\"']?(3[4-7]\.\d+)", True),
        (r"/@(3[4-7]\.\d+),(1(?:3[8-9]|40)\.\d+)", False),
        (r"[?&](?:ll|q|center)=?(3[4-7]\.\d+)%?2?C(1(?:3[8-9]|40)\.\d+)", False),
    ]
    for pattern, reverse in pair_patterns:
        for m in re.finditer(pattern, normalized, flags=re.I | re.S):
            if reverse:
                add(m.group(2), m.group(1), "掲載ページ埋込地図座標", 95)
            else:
                add(m.group(1), m.group(2), "掲載ページ埋込地図座標", 95)
    return candidates


@functools.lru_cache(maxsize=1)
def gsi_municipalities():
    try:
        r = request("GET", GSI_MUNI_URL, headers=HEADERS, timeout=(4, 12))
        text = r.content.decode("utf-8-sig", errors="replace")
        table = {}
        for code, value in re.findall(r'muniArray\[\"(\d+)\"\]\s*=\s*[\"\']([^\"\']+)[\"\']', text):
            parts = value.split(",")
            if len(parts) >= 4:
                table[code] = {"pref_code": parts[0], "prefecture": parts[1], "municipality": parts[3]}
        return table
    except Exception:
        return {}


def gsi_reverse_region(lat: float, lng: float):
    try:
        r = request(
            "GET",
            GSI_REVERSE_URL,
            params={"lat": round(float(lat), 7), "lon": round(float(lng), 7)},
            headers=HEADERS,
            timeout=(4, 12),
        )
        data = r.json().get("results", {})
        code = str(data.get("muniCd", "")).strip()
        town = str(data.get("lv01Nm", "")).strip()
        if not code or not town:
            return None
        muni = gsi_municipalities().get(code, {})
        base = re.sub(r"[0-9０-９一二三四五六七八九十百]+丁目$", "", town).strip() or town
        prefecture = muni.get("prefecture", "")
        municipality = muni.get("municipality", "")
        return {
            "muni_code": code,
            "pref_code": muni.get("pref_code", ""),
            "prefecture": prefecture,
            "municipality": municipality,
            "town": town,
            "town_base": base,
            "label": "".join(x for x in (prefecture, municipality, town) if x) or town,
            "lat": float(lat),
            "lng": float(lng),
        }
    except Exception:
        return None


def gsi_geocode_address(address: str):
    address = str(address or "").strip()
    if not address:
        return None
    try:
        r = request("GET", GSI_SEARCH_URL, params={"q": address}, headers=HEADERS, timeout=(4, 12))
        rows = r.json()
        if not isinstance(rows, list):
            return None
        for item in rows[:5]:
            try:
                lng, lat = item["geometry"]["coordinates"]
                title = str(item.get("properties", {}).get("title", ""))
                lat, lng = float(lat), float(lng)
                if 34 <= lat <= 37 and 138 <= lng <= 141:
                    return {"lat": lat, "lng": lng, "title": title}
            except Exception:
                continue
    except Exception:
        pass
    return None


def precise_address(address: str) -> bool:
    t = unicodedata.normalize("NFKC", str(address or "")).replace(" ", "")
    return bool(re.search(r"(?:\d+丁目|\d+番|\d+-\d+|\d+号)", t))


def compact_address(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).replace(" ", "").replace("　", "")


def address_match_status(address: str, map_address: str) -> str:
    a, b = compact_address(address), compact_address(map_address)
    if not a or not b:
        return "未判定"
    if a in b or b in a:
        return "一致"
    # 市区町村・町名の共通部分が長ければ一部一致。
    common = os.path.commonprefix([a, b])
    return "一部一致" if len(common) >= 5 else "不一致"


def choose_location(html: str, address: str, bounds, region, provider: str, extra_candidates=None):
    candidates = []
    raw_candidates = list(extract_coordinate_candidates(html))
    for extra in extra_candidates or []:
        if isinstance(extra, dict):
            raw_candidates.append(extra)
    for c in raw_candidates:
        if inside(c["lat"], c["lng"], bounds):
            d = distance_m(c["lat"], c["lng"], region["lat"], region["lng"])
            candidates.append((c["priority"], -d, c))
    if candidates:
        c = max(candidates, key=lambda x: (x[0], x[1]))[2]
        rev = gsi_reverse_region(c["lat"], c["lng"])
        map_address = rev.get("label", "") if rev else ""
        return {
            "lat": c["lat"],
            "lng": c["lng"],
            "map_address": map_address,
            "address_match": address_match_status(address, map_address),
            "location_confidence": "高",
            "coordinate_source": f"{provider}:{c['source']}",
        }
    if precise_address(address):
        geo = gsi_geocode_address(address)
        if geo and inside(geo["lat"], geo["lng"], bounds):
            rev = gsi_reverse_region(geo["lat"], geo["lng"])
            map_address = rev.get("label", "") if rev else geo.get("title", "")
            return {
                "lat": geo["lat"],
                "lng": geo["lng"],
                "map_address": map_address,
                "address_match": address_match_status(address, map_address),
                "location_confidence": "中",
                "coordinate_source": "国土地理院住所検索",
            }
    return None


def normalize_place_text(value: str):
    return unicodedata.normalize("NFKC", str(value or "")).replace(" ", "").replace("　", "")


def region_matches_address(address: str, region: dict):
    address_n = normalize_place_text(address)
    town_n = normalize_place_text(region.get("town", ""))
    base_n = normalize_place_text(region.get("town_base", ""))
    target = town_n if town_n and town_n != base_n else base_n
    return bool(target and target in address_n)


def region_search_terms(region: dict):
    out = []
    for value in (region.get("town"), region.get("town_base")):
        value = str(value or "").strip()
        if value and value not in out:
            out.append(value)
    return out


def discover_regions(bounds, update_text):
    south, west, north, east = bounds
    mid_lat = (south + north) / 2
    height_m = max(1, (north - south) * 111_320)
    width_m = max(1, (east - west) * 111_320 * math.cos(math.radians(mid_lat)))
    # 精度と速度のバランス。3km半径でも最大15x15=225地点。
    rows = min(15, max(3, math.ceil(height_m / 400)))
    cols = min(15, max(3, math.ceil(width_m / 400)))
    points = []
    for iy in range(rows):
        lat = south + (north - south) * ((iy + 0.5) / rows)
        for ix in range(cols):
            lng = west + (east - west) * ((ix + 0.5) / cols)
            points.append((lat, lng))

    regions = {}
    done = 0
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(points))) as pool:
        pending = {pool.submit(gsi_reverse_region, lat, lng) for lat, lng in points}
        while pending:
            finished, pending = concurrent.futures.wait(
                pending, timeout=0.5, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in finished:
                done += 1
                try:
                    region = future.result()
                except Exception:
                    region = None
                if region:
                    key = (region["muni_code"], normalize_place_text(region["town"]))
                    regions.setdefault(key, region)
            update_text(f"地域判定 {done}/{len(points)}地点｜確認できた地域 {len(regions)}件｜経過 {int(time.monotonic()-started)}秒")
    return list(regions.values())


@functools.lru_cache(maxsize=1)
def homes_robots_allowed():
    try:
        r = request("GET", HOMES_ROOT + "/robots.txt", headers=HEADERS, timeout=(5, 15))
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(r.text.splitlines())
        return rp.can_fetch(HEADERS["User-Agent"], MAP_TILE_URL) and rp.can_fetch(HEADERS["User-Agent"], MAP_INFO_URL)
    except Exception:
        return False


@functools.lru_cache(maxsize=1)
def suumo_robots_allowed():
    try:
        r = request("GET", "https://suumo.jp/robots.txt", headers=HEADERS, timeout=(5, 15))
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(r.text.splitlines())
        return rp.can_fetch(HEADERS["User-Agent"], SUUMO_SEARCH_URL)
    except Exception:
        return False


def tile_xy(lat, lng, zoom=15):
    n = 2 ** zoom
    x = int((lng / 360 + 0.5) * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def viewport_tiles(bounds, zoom=15):
    xmin, ymin = tile_xy(bounds[2], bounds[1], zoom)
    xmax, ymax = tile_xy(bounds[0], bounds[3], zoom)
    return [(x, y) for x in range(min(xmin, xmax), max(xmin, xmax) + 1) for y in range(min(ymin, ymax), max(ymin, ymax) + 1)]


def homes_condition(freeword=""):
    # 間取りはここでは絞らず、詳細取得後に対象間取りだけ残す。
    return {
        "cond[mbg][3001]": "3001",
        "cond[mbg][3002]": "3002",
        "cond[mbg][3003]": "3003",
        "cond[monthmoneyroom]": "0",
        "cond[monthmoneyroomh]": "0",
        "cond[housearea]": "0",
        "cond[houseareah]": "0",
        "cond[walkminutesh]": "0",
        "cond[houseageh]": str(MAX_BUILDING_AGE),
        "cond[newdate]": "0",
        "cond[freeword]": freeword,
        "cond[fwtype]": "1",
        "cond[exfreeword]": "",
    }


def homes_candidates(bounds, freeword, candidate_limit, report=None):
    candidates = {}
    tiles = viewport_tiles(bounds, 15)
    # 地域タスク内での負荷を制御するため最大24タイルまで。
    if len(tiles) > 24:
        step = max(1, len(tiles) // 24)
        tiles = tiles[::step][:24]
    for offset in range(0, len(tiles), 6):
        if report:
            report(f"地図候補の取得 {offset + 1}〜{min(offset + 6, len(tiles))}/{len(tiles)}タイル")
        data = homes_condition(freeword)
        data["zoom"] = 15
        for i, (x, y) in enumerate(tiles[offset:offset + 6]):
            data[f"tiles[{i}][x]"] = x
            data[f"tiles[{i}][y]"] = y
        try:
            r = request("POST", MAP_TILE_URL, data=data, headers=HEADERS, timeout=(6, 20))
            payload = r.json()
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        for group in payload.values():
            if not isinstance(group, dict):
                continue
            for row in group.get("row_set", []):
                try:
                    lat, lng = float(row["lat"]), float(row["lon"])
                    key = str(row.get("tykey", ""))
                    if key and re.fullmatch(r"[a-zA-Z0-9]+", key) and inside(lat, lng, bounds):
                        candidates[key] = row
                        if len(candidates) >= candidate_limit:
                            return candidates
                except Exception:
                    continue
    return candidates


def extract_homes_room_links(html: str):
    links = []
    for href in re.findall(r"(?is)href\s*=\s*[\"']([^\"']*/chintai/[^\"']+)[\"']", html):
        url = urljoin(HOMES_ROOT, html_lib.unescape(href)).split("#")[0]
        p = urlparse(url)
        if p.scheme == "https" and p.hostname == "www.homes.co.jp" and p.path.startswith("/chintai/") and url not in links:
            links.append(url)
    return links


def parse_homes_detail(html: str, url: str, bounds, region, map_hint=None):
    for data in extract_jsonld(html):
        for node in walk_json(data):
            if not isinstance(node, dict) or node.get("@type") != "RealEstateListing":
                continue
            offer = node.get("offers") if isinstance(node.get("offers"), dict) else {}
            entity = node.get("mainEntity") if isinstance(node.get("mainEntity"), dict) else {}
            attrs = {
                str(x.get("name", "")): x.get("value")
                for x in entity.get("additionalProperty", [])
                if isinstance(x, dict)
            }
            costs = {
                str(x.get("name", "")): x.get("value")
                for x in offer.get("additionalProperty", [])
                if isinstance(x, dict)
            }
            layout = str(attrs.get("間取り", "")).strip()
            if layout not in TARGET_LAYOUTS:
                continue
            try:
                rent = money(offer.get("price"))
                fees = money(costs.get("管理費等", costs.get("管理費", 0)))
                area = float((entity.get("floorSize") or {}).get("value", 0))
            except Exception:
                continue
            structure = normalize_structure(attrs.get("建物構造") or attrs.get("構造") or extract_labeled_value(html, ("建物構造", "構造")))
            age, year = building_age_from_values(attrs.get("築年月"), attrs.get("築年数"), extract_labeled_value(html, ("築年月", "築年数")))
            if structure != TARGET_STRUCTURE or age is None or not (0 <= age <= MAX_BUILDING_AGE):
                continue
            addr_obj = entity.get("address") if isinstance(entity.get("address"), dict) else {}
            address = "".join(str(addr_obj.get(k, "")) for k in ("addressRegion", "addressLocality", "streetAddress"))
            extra = [map_hint] if isinstance(map_hint, dict) else []
            location = choose_location(html, address, bounds, region, "HOME'S", extra)
            if not location:
                continue
            name = str(entity.get("name") or node.get("name") or "HOME'S掲載物件")[:200]
            canonical = str(node.get("url") or url)
            p = {
                "id": canonical.rstrip("/").split("/")[-1],
                "name": name,
                "address": address[:200],
                "map_address": location["map_address"][:200],
                "address_match": location["address_match"],
                "location_confidence": location["location_confidence"],
                "lat": location["lat"],
                "lng": location["lng"],
                "layout": layout,
                "rent": rent,
                "fees": fees,
                "area": area,
                "floor": str(entity.get("floorLevel", ""))[:80],
                "structure": "SRC",
                "building_age": age,
                "built_year": year or "",
                "coordinate_source": location["coordinate_source"],
                "url": canonical,
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "modified": str(node.get("dateModified", ""))[:80],
                "source": "LIFULL HOME'S",
            }
            if target_property(p):
                return p
    return None


def collect_homes_region(bounds, region, limit, report=None):
    stats = {"candidate": 0, "detail": 0, "accepted": 0, "rejected": 0, "warnings": []}
    if report:
        report("取得可否を確認中")
    if not homes_robots_allowed():
        stats["warnings"].append("HOME'S robots.txt により自動取得を実行しませんでした。")
        return [], stats
    candidates = {}
    for term in region_search_terms(region) + [""]:
        candidates = homes_candidates(bounds, term, max(20, limit * 3), report)
        if candidates:
            break
    stats["candidate"] = len(candidates)
    if not candidates:
        return [], stats
    ordered = sorted(
        candidates.items(),
        key=lambda kv: distance_m(region["lat"], region["lng"], float(kv[1]["lat"]), float(kv[1]["lon"])),
    )
    room_links = []
    hint_by_url = {}
    for number, (key, row) in enumerate(ordered[: max(10, limit * 2)], 1):
        if report:
            report(f"建物候補 {number}/{min(len(ordered), max(10, limit * 2))}の部屋リンクを確認中")
        data = homes_condition(region.get("town", ""))
        data["cond[tykey]"] = key
        try:
            r = request("POST", MAP_INFO_URL, data=data, headers=HEADERS, timeout=(6, 20))
            urls = extract_homes_room_links(r.text)
        except Exception:
            continue
        for u in urls:
            if u not in hint_by_url:
                hint_by_url[u] = (float(row["lat"]), float(row["lon"]))
                room_links.append(u)
            if len(room_links) >= max(20, limit * 4):
                break
        if len(room_links) >= max(20, limit * 4):
            break

    records = []
    for number, url in enumerate(room_links, 1):
        if report:
            report(f"詳細確認 {number}/{len(room_links)}件｜条件適合 {len(records)}件")
        if len(records) >= limit:
            break
        stats["detail"] += 1
        try:
            r = source_get(url, "www.homes.co.jp", "/chintai/", timeout=(6, 20))
            hint_lat, hint_lng = hint_by_url.get(url, (None, None))
            hint = ({"lat": hint_lat, "lng": hint_lng, "source": "HOME'S検索地図・建物座標", "priority": 99}
                    if hint_lat is not None and hint_lng is not None else None)
            p = parse_homes_detail(r.text, url, bounds, region, hint)
            if p:
                # 検索地図上の建物座標を追加の高精度根拠として使える場合は、掲載座標が欠落していても補完。
                records.append(p)
                stats["accepted"] += 1
            else:
                stats["rejected"] += 1
        except Exception:
            stats["rejected"] += 1
    return deduplicate(records), stats


def safe_suumo_url(url: str):
    try:
        p = urlparse(url)
        return p.scheme == "https" and p.hostname == "suumo.jp" and p.path.startswith("/chintai/")
    except ValueError:
        return False


def suumo_search_url(region, page=1):
    params = {
        "ar": "030",
        "bs": "040",
        "pc": "50",
        "sc": region["muni_code"],
        "fw2": region["town"],
        "page": str(page),
    }
    if region.get("pref_code"):
        params["ta"] = region["pref_code"]
    return SUUMO_SEARCH_URL + "?" + urlencode(params)


def first_class_fragment(block: str, class_name: str):
    m = re.search(
        rf"(?is)<[^>]+class\s*=\s*[\"'][^\"']*{re.escape(class_name)}[^\"']*[\"'][^>]*>(.*?)</[^>]+>",
        block,
    )
    return text_from_fragment(m.group(1)) if m else ""


def parse_suumo_list(html: str, region, limit):
    rows = []
    parts = re.split(r"(?is)(?=<div[^>]+class\s*=\s*[\"'][^\"']*cassetteitem(?:\s|[\"']))", html)
    for block in parts:
        if "cassetteitem" not in block:
            continue
        name = first_class_fragment(block, "cassetteitem_content-title") or "SUUMO掲載物件"
        address = first_class_fragment(block, "cassetteitem_detail-col1")
        if address and not region_matches_address(address, region):
            continue
        age, year = building_age_from_values(strip_html(block))
        if age is not None and age > MAX_BUILDING_AGE:
            continue
        room_blocks = re.findall(r"(?is)<tr[^>]+class\s*=\s*[\"'][^\"']*js-cassette_link[^\"']*[\"'][^>]*>(.*?)</tr>", block)
        for room in room_blocks:
            layout = first_class_fragment(room, "cassetteitem_madori")
            if layout not in TARGET_LAYOUTS:
                continue
            area_text = first_class_fragment(room, "cassetteitem_menseki")
            rent_text = first_class_fragment(room, "cassetteitem_price--rent")
            fee_text = first_class_fragment(room, "cassetteitem_price--administration")
            hrefs = re.findall(r"(?is)href\s*=\s*[\"']([^\"']+)[\"']", room)
            href = next((h for h in hrefs if "/chintai/" in h), "")
            url = urljoin("https://suumo.jp", html_lib.unescape(href)).split("#")[0]
            if not safe_suumo_url(url):
                continue
            try:
                area_m = re.search(r"(\d+(?:\.\d+)?)", unicodedata.normalize("NFKC", area_text))
                area = float(area_m.group(1)) if area_m else 0
                rent = money(rent_text)
                fees = money(fee_text)
            except Exception:
                continue
            if not (0 < area <= 1000 and 0 < rent <= 10_000_000 and 0 <= fees <= 10_000_000):
                continue
            rows.append({
                "id": url.rstrip("/").split("/")[-1],
                "name": name[:200],
                "address": address[:200],
                "layout": layout,
                "rent": rent,
                "fees": fees,
                "area": area,
                "floor": "",
                "url": url,
                "building_age_hint": age,
                "built_year_hint": year,
            })
            if len(rows) >= limit:
                return rows
    return rows


def verify_suumo_detail(p: dict, bounds, region):
    r = source_get(p["url"], "suumo.jp", "/chintai/", timeout=(6, 22))
    html = r.text
    structure = normalize_structure(extract_labeled_value(html, ("構造", "建物構造")))
    age, year = building_age_from_values(extract_labeled_value(html, ("築年月", "築年数")))
    if structure != TARGET_STRUCTURE or age is None or not (0 <= age <= MAX_BUILDING_AGE):
        return None
    location = choose_location(html, p.get("address", ""), bounds, region, "SUUMO")
    if not location:
        return None
    out = dict(p)
    out.update({
        "map_address": location["map_address"][:200],
        "address_match": location["address_match"],
        "location_confidence": location["location_confidence"],
        "lat": location["lat"],
        "lng": location["lng"],
        "structure": "SRC",
        "building_age": age,
        "built_year": year or "",
        "coordinate_source": location["coordinate_source"],
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "modified": "",
        "source": "SUUMO",
    })
    out.pop("building_age_hint", None)
    out.pop("built_year_hint", None)
    return out if target_property(out) else None


def collect_suumo_region(bounds, region, limit, report=None):
    stats = {"candidate": 0, "detail": 0, "accepted": 0, "rejected": 0, "warnings": []}
    if report:
        report("取得可否を確認中")
    if not suumo_robots_allowed():
        stats["warnings"].append("SUUMO robots.txt により自動取得を実行しませんでした。")
        return [], stats
    candidates = []
    seen = set()
    max_pages = min(6, max(2, math.ceil(max(limit * 3, 50) / 50)))
    empty = 0
    for page in range(1, max_pages + 1):
        if report:
            report(f"検索一覧 {page}/{max_pages}ページを取得中")
        try:
            r = source_get(suumo_search_url(region, page), "suumo.jp", "/jj/chintai/ichiran/", timeout=(6, 22))
            parsed = parse_suumo_list(r.text, region, max(limit * 4, 80))
        except Exception:
            stats["warnings"].append(f"SUUMO {page}ページ目を取得できませんでした。")
            break
        fresh = [x for x in parsed if x["url"] not in seen]
        if not fresh:
            empty += 1
            if empty >= 2:
                break
        else:
            empty = 0
            for x in fresh:
                seen.add(x["url"])
                candidates.append(x)
        if len(candidates) >= max(limit * 4, 80):
            break
    stats["candidate"] = len(candidates)
    records = []
    for number, p in enumerate(candidates, 1):
        if report:
            report(f"詳細確認 {number}/{len(candidates)}件｜条件適合 {len(records)}件")
        if len(records) >= limit:
            break
        stats["detail"] += 1
        try:
            verified = verify_suumo_detail(p, bounds, region)
            if verified:
                records.append(verified)
                stats["accepted"] += 1
            else:
                stats["rejected"] += 1
        except Exception:
            stats["rejected"] += 1
    return deduplicate(records), stats


def deduplicate(records):
    out = []
    seen = set()
    for p in records:
        key = p.get("url") or (
            round(float(p.get("lat", 0)), 5),
            round(float(p.get("lng", 0)), 5),
            p.get("layout"),
            p.get("rent"),
            round(float(p.get("area", 0)), 1),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def property_row(p: dict, namespace: str):
    identity = str(p.get("url") or p.get("id") or "")
    if not identity:
        raise ValueError("物件の識別情報がありません")
    # v04 and v05 share these columns. Optional indexing columns are not required.
    return {"namespace": namespace, "identity": identity, "payload": p,
            "fetched_at": str(p.get("fetched_at") or datetime.now(timezone.utc).isoformat(timespec="seconds"))}


def save_records(records):
    if not records:
        return 0
    _, _, namespace = supabase_settings()
    unique = deduplicate([p for p in records if target_property(p)])
    rows = [property_row(p, namespace) for p in unique]
    result = supabase_request(
        "POST",
        "sumai_properties",
        params={"on_conflict": "namespace,identity", "select": "identity"},
        payload=rows,
        prefer="resolution=merge-duplicates,return=representation",
    )
    if not isinstance(result, list):
        raise StorageError("Supabaseへの保存結果を確認できませんでした。")
    returned = {str(x.get("identity", "")) for x in result if isinstance(x, dict)}
    expected = {x["identity"] for x in rows}
    if not expected <= returned:
        raise StorageError(f"Supabase保存確認不足: {len(expected-returned)}件")
    return len(expected)


def load_range(center_lat, center_lng, radius_m, layouts):
    _, _, namespace = supabase_settings()
    bounds = bounds_from_center(center_lat, center_lng, radius_m)
    out, offset = [], 0
    while True:
        rows = supabase_request("GET", "sumai_properties", params={
            "namespace": "eq." + namespace, "select": "identity,payload",
            "order": "identity.asc", "limit": 500, "offset": offset})
        if not isinstance(rows, list):
            raise StorageError("Supabaseからの読み込み結果を確認できませんでした。")
        if not rows:
            break
        for row in rows:
            p = row.get("payload") if isinstance(row, dict) else None
            if not isinstance(p, dict) or not target_property(p) or p.get("layout") not in layouts:
                continue
            lat, lng = float(p['lat']), float(p['lng'])
            if inside(lat, lng, bounds) and distance_m(center_lat, center_lng, lat, lng) <= radius_m:
                out.append(p)
        offset += len(rows)
    return deduplicate(out)


def check_storage():
    _, _, namespace = supabase_settings()
    rows = supabase_request("GET", "sumai_properties", params={
        "namespace": "eq." + namespace, "select": "identity,payload,fetched_at", "limit": 1})
    if not isinstance(rows, list):
        raise StorageError("Supabaseの応答形式を確認できませんでした。")


def run_search(center_lat, center_lng, radius_m, per_region_limit, worker_count, progress_bar, status_box, log_box):
    bounds = bounds_from_center(center_lat, center_lng, radius_m)
    logs = []
    started = time.monotonic()
    def log(message):
        logs.append(f"{datetime.now().strftime('%H:%M:%S')}  {message}")
        del logs[:-18]
        log_box.code("\n".join(logs), language=None)
    log("検索ボタン受付完了。検索処理を開始しました。")
    progress_bar.progress(0.0, text="ステップ1/3：地域・丁目を判定中")
    def region_progress(message):
        status_box.info("🔎 検索実行中｜ステップ1/3：" + message)
        match = re.search(r"地域判定 (\d+)/(\d+)", message)
        if match:
            progress_bar.progress(0.1 * int(match[1]) / max(1, int(match[2])), text=message)
    status_box.info("🔎 検索実行中｜ステップ1/3：Supabaseの接続・必須列を確認しています")
    check_storage()
    log("Supabaseの接続・必須列確認OK")
    regions = discover_regions(bounds, region_progress)
    if not regions:
        raise RuntimeError("地域名を判定できませんでした。国土地理院への接続を確認して再検索してください。")
    log(f"地域判定完了：{len(regions)}地域")
    tasks = [(region, source) for region in regions for source in ("HOME'S", "SUUMO")]
    total = len(tasks)
    collected, saved_ids, warnings = [], set(), []
    rejected_total = error_total = done = 0
    events = queue.Queue()
    active = {}
    def run_one(index, region, source):
        def report(message):
            events.put((index, region['label'] + "｜" + source + "｜" + message))
        report("検索開始")
        collector = collect_homes_region if source == "HOME'S" else collect_suumo_region
        return collector(bounds, region, per_region_limit, report=report)
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(worker_count, total)) as pool:
        pending = {pool.submit(run_one, index, *task): (index, *task) for index, task in enumerate(tasks)}
        while pending:
            finished, remaining = concurrent.futures.wait(
                pending, timeout=0.5, return_when=concurrent.futures.FIRST_COMPLETED)
            while True:
                try:
                    index, message = events.get_nowait()
                    active[index] = message
                except queue.Empty:
                    break
            for future in finished:
                index, region, source = pending[future]
                active.pop(index, None)
                done += 1
                try:
                    records, stats = future.result()
                    collected.extend(records)
                    rejected_total += int(stats.get("rejected", 0))
                    for warning in stats.get("warnings", []):
                        warnings.append(region['label'] + "｜" + source + "｜" + warning)
                        log(warnings[-1])
                    if records:
                        status_box.info(f"🔎 検索実行中｜{region['label']}｜{source}｜{len(records)}件をSupabaseへ保存中")
                        try:
                            save_records(records)
                            saved_ids.update(p.get('url') or p.get('id') for p in records)
                        except StorageError as exc:
                            error_total += 1
                            log(f"保存エラー｜{region['label']}｜{source}｜{exc}")
                    log(f"{done}/{total}完了｜{region['label']}｜{source}｜候補 {stats.get('candidate',0)}｜詳細 {stats.get('detail',0)}｜条件適合 {len(records)}｜保存確認 {len(saved_ids)}")
                except Exception as exc:
                    error_total += 1
                    log(f"取得エラー｜{region['label']}｜{source}｜{type(exc).__name__}: {exc}")
                # Keep intermediate results if a later task encounters an error.
                st.session_state.records = deduplicate(collected)
            pending = {future: pending[future] for future in remaining}
            progress_bar.progress(0.1 + 0.85 * done / total,
                                  text=f"ステップ2/3：{done}/{total}タスク完了｜保存確認 {len(saved_ids)}件")
            status_box.info(
                f"🔎 検索実行中｜{done}/{total}タスク完了｜保存確認 {len(saved_ids)}件｜除外 {rejected_total}件｜エラー {error_total}件｜経過 {int(time.monotonic()-started)}秒\n\n"
                + "\n\n".join(active.values()))
    records = deduplicate(collected)
    progress_bar.progress(1.0, text="ステップ3/3：結果を表示します")
    st.session_state.search_result_v24 = {
        "saved": len(saved_ids), "errors": error_total, "warnings": warnings,
        "tasks": total, "logs": logs, "elapsed": int(time.monotonic()-started)}
    return records


def request_search(station, radius, per_region_limit, worker_count):
    # A click callback stores only this click's settings. No persisted job or resume state is consulted.
    st.session_state.search_request_v24 = (station, radius, per_region_limit, worker_count)


def execute_search(search_request):
    station, radius, limit, workers = search_request
    state = st.session_state
    state.last_search_summary = ""
    state.search_result_v24 = {}
    st.info(f"🔎 検索を開始しました｜{station}を中心に半径{radius/1000:g}km")
    progress_bar = st.progress(0.0, text="検索開始済み｜地域を判定します")
    status_box, log_box = st.empty(), st.empty()
    status_box.info("🔎 検索実行中｜地域・丁目を確認しています")
    try:
        records = run_search(*STATIONS[station], radius, limit, workers, progress_bar, status_box, log_box)
        state.records = records
        state.last_search_records = records
        result = state.search_result_v24
        state.last_search_summary = (f"検索完了｜今回確認 {len(records)}件｜保存確認 {result['saved']}件"
                                     f"｜エラー {result['errors']}件｜所要 {result['elapsed']}秒")
        state.search_message_kind_v24 = "error" if result['errors'] else "info" if result['warnings'] or not records else "success"
    except Exception as exc:
        state.last_search_summary = f"検索エラー｜{type(exc).__name__}: {exc}"
        state.search_message_kind_v24 = "error"
    # Only rerun AFTER work has finished, to draw the new map and restore the search button.
    st.rerun()


def main():
    st.set_page_config(page_title="住まいコンパス", page_icon="🏠", layout="wide")
    st.markdown(
        """
        <style>
        .stApp{background:white;color:#173a5e;color-scheme:light}
        .block-container{padding-top:1rem;padding-bottom:2rem;max-width:1200px}
        h1,h2,h3,p,label{color:#173a5e}
        [data-testid="stAppViewContainer"], [data-testid="stHeader"]{background:#fff;color:#173a5e}
        [data-testid="stButton"] button{background:#fff!important;color:#173a5e!important;border:1px solid #173a5e!important}
        [data-testid="stButton"] button *{color:inherit!important}
        [data-testid="stButton"] button[kind="primary"]{background:#c62828!important;color:#fff!important;border-color:#c62828!important}
        [data-testid="stButton"] button:disabled{background:#edf2f7!important;color:#526579!important;opacity:1!important}
        [data-baseweb="select"]>div,[data-baseweb="input"],input,[data-testid="stNumberInput"] button{
            background:#fff!important;color:#173a5e!important;border-color:#90a4b8!important}
        [data-baseweb="select"] span,[data-baseweb="select"] svg{color:#173a5e!important;fill:#173a5e}
        [data-baseweb="popover"],[role="listbox"],[role="option"]{background:#fff!important;color:#173a5e!important}
        [data-testid="stCode"] pre,[data-testid="stCode"] code{background:#f3f6fa!important;color:#173a5e!important}
        [data-testid="stAlertContainer"]{color:#173a5e!important}
        div[data-testid="stStatusWidget"]{border:2px solid #c62828;border-radius:12px}
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.title("住まいコンパス")
    st.markdown(
        f"<div style=\"display:inline-block;background:#173a5e;color:white;padding:6px 10px;border-radius:8px;font-weight:800;margin-bottom:8px;\">BUILD {APP_VERSION}</div>",
        unsafe_allow_html=True,
    )
    st.caption("赤い『この範囲の物件を取得・保存』を押すと、そのクリックで検索を開始します。停止・再開モードはありません。")


    state = st.session_state
    state.setdefault("records", [])
    state.setdefault("last_search_records", [])
    state.setdefault("last_search_summary", "")
    search_request = state.pop("search_request_v24", None)
    if search_request is not None:
        for widget_key in ("station_v25", "layout_v25", "radius_v25", "limit_v25", "workers_v25", "budget_v25"):
            if widget_key in state:
                state[widget_key] = state[widget_key]
        execute_search(search_request)
        return

    with st.expander("地域・物件取得の設定", expanded=True):
        station = st.selectbox("中心駅", list(STATIONS), index=list(STATIONS).index("池袋"), key="station_v25")
        layout_group = st.radio("表示する間取り区分", list(LAYOUT_GROUPS), index=1, horizontal=True, key="layout_v25")
        radius = st.selectbox("検索・表示半径", [500, 1000, 1500, 2000, 3000], index=2, format_func=lambda x: f"{x/1000:g}km", key="radius_v25")
        per_region_limit = st.selectbox("1地域・1取得元あたりの確認上限", [10, 20, 40, 60], index=1, key="limit_v25")
        worker_count = st.selectbox("地域検索の並列数", [2, 4, 6, 8], index=2, key="workers_v25")
        budget = st.number_input("物件ピンの月額上限（万円）", 1.0, 1000.0, 50.0, 1.0, key="budget_v25")
        st.caption("取得対象: 1K・1L・1DK・1LDK・2DK・2LDK・3DK・3LDK / SRC / 築20年以内 / 駅徒歩制限なし。取得時は全間取りをまとめて確認します。")

    center_lat, center_lng = STATIONS[station]
    displayed = [
        p for p in state.records
        if p.get("layout") in LAYOUT_GROUPS[layout_group]
        and target_property(p)
        and distance_m(center_lat, center_lng, float(p["lat"]), float(p["lng"])) <= radius
        and int(p["rent"]) + int(p.get("fees", 0)) <= budget * 10000
    ]

    if displayed:
        st.map([{"lat": p["lat"], "lon": p["lng"]} for p in displayed], latitude="lat", longitude="lon", use_container_width=True)
    else:
        st.map([{"lat": center_lat, "lon": center_lng}], latitude="lat", longitude="lon", use_container_width=True)

    if state.last_search_summary:
        getattr(st, state.get("search_message_kind_v24", "info"))(state.last_search_summary)
        result = state.get("search_result_v24", {})
        if result.get("logs"):
            with st.expander("前回の検索ログ"):
                st.code("\n".join(result["logs"]), language=None)

    c1, c2, c3 = st.columns(3)
    if c1.button("Supabase接続確認"):
        try:
            check_storage()
            st.success("Supabase接続OK。")
        except StorageError as exc:
            st.error(str(exc))

    if c2.button("保存済み物件をこの範囲だけ読み込む"):
        try:
            state.records = load_range(center_lat, center_lng, radius, TARGET_LAYOUTS)
            state.last_search_summary = f"保存済み物件を{len(state.records)}件読み込みました。"
            state.search_message_kind_v24 = "success"
            state.search_result_v24 = {}
            st.rerun()
        except StorageError as exc:
            st.error(str(exc))

    c3.button(
        "🔎 この範囲の物件を取得・保存", type="primary", key="start_property_search_v24",
        use_container_width=True, on_click=request_search,
        args=(station, radius, per_region_limit, worker_count))

    if state.records:
        selected = displayed
        st.caption(f"現在メモリ上の物件 {len(state.records)}件 / 選択間取り {len(selected)}件")
        st.dataframe(
            [
                {
                    "物件名": p.get("name", ""),
                    "間取り": p.get("layout", ""),
                    "総額（万円）": round((int(p["rent"]) + int(p.get("fees", 0))) / 10000, 2),
                    "面積（㎡）": p.get("area", ""),
                    "築年数": p.get("building_age", ""),
                    "掲載住所": p.get("address", ""),
                    "地図判定住所": p.get("map_address", ""),
                    "位置精度": p.get("location_confidence", ""),
                    "座標根拠": p.get("coordinate_source", ""),
                    "取得元": p.get("source", ""),
                    "募集ページ": p.get("url", ""),
                }
                for p in selected[:1000]
            ],
            hide_index=True,
            use_container_width=True,
            column_config={"募集ページ": st.column_config.LinkColumn("募集ページ")},
        )
    else:
        st.caption("まだ物件データはありません。赤い取得ボタンを押すと、その場で検索を開始します。")

    with st.expander("取得方式"):
        st.write("赤い取得ボタンを押した同じ実行で、そのまま検索を開始します。queued / paused / 手動再開 / 自動再開は使いません。")
        st.write("検索開始時は『検索を開始しました』を表示し、検索中は地域判定・HOME'S/SUUMO・保存件数・除外件数・エラー件数を文字で逐次更新します。")
        st.write("検索は地域×取得元の単位で並列化します。確認済み物件は各地域タスクの完了時にSupabaseへ保存します。")


if __name__ == "__main__":
    main()
